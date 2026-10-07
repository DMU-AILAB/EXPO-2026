"""대시보드 푸시 업데이트 — 번들 구성과 기기별 결과.

Pi는 respx로 흉내 낸다. 흉내 낸 경로가 실재하는지는 루트
`tests/test_dashboard_pi_routes.py`가 소스 대조로 따로 지킨다.
"""

import io
import json
import tarfile

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.routers import devices as devices_router
from app.services.bundle_builder import build_bundle, repo_root


@pytest.fixture(autouse=True)
def fast_wait(monkeypatch):
    monkeypatch.setattr(devices_router, "_UPDATE_FIRST_WAIT_SEC", 0.0)
    monkeypatch.setattr(devices_router, "_UPDATE_POLL_SEC", 0.01)
    monkeypatch.setattr(devices_router, "_UPDATE_WAIT_SEC", 0.2)


@pytest.fixture
def auth_headers(client: TestClient, admin_user):
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test_password"}
                        ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _register(client, headers, device_id, ip):
    """프로비저닝까지 성공한 기기 — control_key가 있어야 업데이트가 된다."""
    pi = f"http://{ip}:5000"
    respx.post(f"{pi}/api/identity").mock(return_value=httpx.Response(200, json={"ok": True}))
    respx.get(f"{pi}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": []}))
    res = client.post("/api/devices", json={"id": device_id, "name": device_id, "ip": ip,
                                            "location": "x"}, headers=headers)
    assert res.status_code == 201 and res.json()["data"]["provisioned"] is True
    return pi


def _members(data: bytes) -> dict[str, bytes]:
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf.getmembers():
            out[m.name] = tf.extractfile(m).read()
    return out


def test_bundle_matches_makefile_deploy_list():
    """번들 목록은 Makefile DEPLOY_PY 그대로 — 복제본이 아니다."""
    import re
    mk = (repo_root() / "Makefile").read_text(encoding="utf-8")
    m = re.search(r"DEPLOY_PY\s*[:+]?=\s*((?:[^\n\\]*\\\n)*[^\n]*)", mk)
    listed = set(re.findall(r"device/([A-Za-z_0-9]+\.py)", m.group(1)))
    members = _members(build_bundle().data)
    top_level_py = {n for n in members if "/" not in n and n.endswith(".py")}
    assert top_level_py == listed
    assert "roi_editor/server.py" in members
    assert "simulator/roi_manager.py" in members
    assert not any(n.startswith("runs/") for n in members)        # 모델은 기본 제외


def test_bundle_is_deterministic_and_manifest_matches():
    a, b = build_bundle(), build_bundle()
    assert a.data == b.data and a.bundle_id == b.bundle_id
    members = _members(a.data)
    manifest = json.loads(members["manifest.json"])
    assert manifest["bundle_id"] == a.bundle_id
    import hashlib
    for name, digest in manifest["files"].items():
        assert hashlib.sha256(members[name]).hexdigest() == digest


def test_bundle_with_models_keeps_same_id():
    code_only = build_bundle()
    with_models = build_bundle(include_models=True)
    assert with_models.bundle_id == code_only.bundle_id            # id는 코드 기준
    assert any(n.startswith("runs/") and n.endswith(".tflite") for n in _members(with_models.data))


@respx.mock
def test_update_one_device_sends_key_and_waits_for_bundle(client, auth_headers):
    pi = _register(client, auth_headers, "d1", "10.0.0.1")
    bundle_id = build_bundle().bundle_id
    upload = respx.post(f"{pi}/api/update").mock(
        return_value=httpx.Response(200, json={"ok": True, "applied": 41, "bundle_id": bundle_id}))
    respx.get(f"{pi}/api/update/status").mock(
        return_value=httpx.Response(200, json={"bundle_id": bundle_id, "has_backup": True}))

    res = client.post("/api/devices/d1/update", json={}, headers=auth_headers)

    assert res.status_code == 200
    data = res.json()["data"]
    assert data["ok"] is True and data["came_back"] is True and data["bundle_id"] == bundle_id
    req = upload.calls.last.request
    assert req.headers["x-device-key"].startswith("vg_")
    assert b"manifest.json" in req.content or len(req.content) > 1000


@respx.mock
def test_update_reports_not_coming_back(client, auth_headers):
    pi = _register(client, auth_headers, "d1", "10.0.0.1")
    respx.post(f"{pi}/api/update").mock(return_value=httpx.Response(200, json={"ok": True, "applied": 1}))
    respx.get(f"{pi}/api/update/status").mock(side_effect=httpx.ConnectError("down"))

    data = client.post("/api/devices/d1/update", json={}, headers=auth_headers).json()["data"]
    assert data["ok"] is True and data["came_back"] is False


@respx.mock
def test_bulk_update_continues_after_one_failure(client, auth_headers):
    ok = _register(client, auth_headers, "ok", "10.0.0.1")
    bad = _register(client, auth_headers, "bad", "10.0.0.2")
    bundle_id = build_bundle().bundle_id
    respx.post(f"{ok}/api/update").mock(return_value=httpx.Response(200, json={"ok": True, "applied": 41}))
    respx.get(f"{ok}/api/update/status").mock(
        return_value=httpx.Response(200, json={"bundle_id": bundle_id, "has_backup": True}))
    respx.post(f"{bad}/api/update").mock(
        return_value=httpx.Response(422, json={"detail": "문법 오류로 적용하지 않았습니다"}))

    res = client.post("/api/devices/update",
                      json={"device_ids": ["bad", "ok", "ghost"]}, headers=auth_headers)

    results = {r["device_id"]: r for r in res.json()["data"]["results"]}
    assert results["ok"]["ok"] is True
    assert results["bad"]["ok"] is False and "문법" in results["bad"]["error"]
    assert results["ghost"]["ok"] is False
    assert [r["device_id"] for r in res.json()["data"]["results"]] == ["bad", "ok", "ghost"]


@respx.mock
def test_update_old_device_without_endpoint_gets_hint(client, auth_headers):
    pi = _register(client, auth_headers, "old", "10.0.0.3")
    respx.post(f"{pi}/api/update").mock(return_value=httpx.Response(404, json={"detail": "Not Found"}))

    res = client.post("/api/devices/update", json={"device_ids": ["old"]}, headers=auth_headers)
    err = res.json()["data"]["results"][0]["error"]
    assert "make sync" in err


def test_update_requires_provisioned_device(client, auth_headers):
    """신원이 안 심긴 기기는 키가 없어 409 — 인수 후 /provision으로 먼저 심는다."""
    with respx.mock:
        respx.post("http://10.0.0.4:5000/api/identity").mock(return_value=httpx.Response(401, json={"detail": "x"}))
        respx.get("http://10.0.0.4:5000/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": []}))
        client.post("/api/devices", json={"id": "np", "name": "np", "ip": "10.0.0.4", "location": "x"},
                    headers=auth_headers)
        res = client.post("/api/devices/np/update", json={}, headers=auth_headers)
    assert res.status_code == 409


@respx.mock
def test_update_status_and_info_and_rollback(client, auth_headers):
    pi = _register(client, auth_headers, "d1", "10.0.0.1")
    latest = build_bundle().bundle_id
    assert client.get("/api/devices/update-info", headers=auth_headers).json()["data"]["bundle_id"] == latest

    respx.get(f"{pi}/api/update/status").mock(
        return_value=httpx.Response(200, json={"bundle_id": "b-old", "has_backup": True}))
    st = client.get("/api/devices/d1/update-status", headers=auth_headers).json()["data"]
    assert st["up_to_date"] is False and st["latest"] == latest and st["has_backup"] is True

    rb = respx.post(f"{pi}/api/update/rollback").mock(
        return_value=httpx.Response(200, json={"ok": True, "restored": 3, "removed": 0}))
    res = client.post("/api/devices/d1/update/rollback", headers=auth_headers)
    assert res.status_code == 200 and rb.calls.last.request.headers["x-device-key"].startswith("vg_")


@respx.mock
def test_provisioned_flag_and_reprovision(client, auth_headers):
    """주입에 실패한 기기는 목록에서 provisioned=false이고, 재주입하면 true가 된다."""
    pi = "http://10.0.0.9:5000"
    identity = respx.post(f"{pi}/api/identity").mock(
        return_value=httpx.Response(401, json={"detail": "기존 device key가 일치하지 않습니다"}))
    respx.get(f"{pi}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": []}))
    created = client.post("/api/devices", json={"id": "re", "name": "re", "ip": "10.0.0.9",
                                                "location": "x"}, headers=auth_headers).json()["data"]
    assert created["provisioned"] is False

    listed = {d["id"]: d for d in client.get("/api/devices", headers=auth_headers).json()["data"]}
    assert listed["re"]["provisioned"] is False
    assert client.get("/api/devices/re", headers=auth_headers).json()["data"]["provisioned"] is False

    # 기기가 인수를 받아들이게 되면(키 없이 덮어쓰기) 재주입이 성공한다
    identity.mock(return_value=httpx.Response(200, json={"ok": True, "usable": True}))
    res = client.post("/api/devices/re/provision", headers=auth_headers)
    assert res.status_code == 200 and res.json()["data"]["provisioned"] is True

    assert client.get("/api/devices/re", headers=auth_headers).json()["data"]["provisioned"] is True
    # 이제 키가 있으므로 업데이트 경로가 409(NOT_PROVISIONED)로 막히지 않는다
    respx.post(f"{pi}/api/update").mock(return_value=httpx.Response(200, json={"ok": True, "applied": 1}))
    respx.get(f"{pi}/api/update/status").mock(side_effect=httpx.ConnectError("down"))
    assert client.post("/api/devices/re/update", json={}, headers=auth_headers).status_code == 200
