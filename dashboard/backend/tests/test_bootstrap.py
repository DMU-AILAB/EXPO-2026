"""Pi 부트스트랩 설치 — 토큰 흐름, 내려받기, 서버 등록.

설치 스크립트는 root 권한으로 도는 코드를 서버에서 받아 실행하는 경로라, 토큰 없는
접근과 치환 오류(주소가 지워지거나 셸로 새는 것)를 테스트로 고정한다.
"""

import hashlib
import io
import subprocess
import tarfile

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.models.device import Device
from app.routers import bootstrap
from app.services import server_address
from app.services.bundle_builder import BundleError, installer_script


@pytest.fixture
def auth_headers(client: TestClient, admin_user):
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test_password"}
                        ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def fixed_server(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "http://192.168.0.105:8001")
    bootstrap._tokens.clear()


def _token(client, headers):
    return client.post("/api/bootstrap/token", headers=headers).json()["data"]


def test_token_requires_admin(client):
    assert client.post("/api/bootstrap/token").status_code in (401, 403)


def test_token_response_has_ready_to_paste_command(client, auth_headers):
    d = _token(client, auth_headers)
    assert d["expires_in"] == 30 * 60 and d["server_url"] == "http://192.168.0.105:8001"
    # 파이프(curl | bash)가 아니라 프로세스 치환 — 스크립트가 stdin을 차지해 sudo 프롬프트가 꼬이지 않게
    assert d["command"] == f'bash <(curl -fsSL "http://192.168.0.105:8001/api/bootstrap/install.sh?token={d["token"]}")'
    assert d["dry_run_command"].endswith("--dry-run")


@pytest.mark.parametrize("path", ["install.sh", "self_update.py", "bundle.tar.gz", "assets.tar.gz"])
def test_downloads_require_a_valid_token(client, path):
    assert client.get(f"/api/bootstrap/{path}").status_code == 401
    assert client.get(f"/api/bootstrap/{path}?token=bogus").status_code == 401


def test_expired_token_is_rejected(client, auth_headers, monkeypatch):
    t = _token(client, auth_headers)["token"]
    assert client.get(f"/api/bootstrap/self_update.py?token={t}").status_code == 200
    now = bootstrap._now()
    monkeypatch.setattr(bootstrap, "_now", lambda: now + bootstrap.TOKEN_TTL_SEC + 1)
    assert client.get(f"/api/bootstrap/self_update.py?token={t}").status_code == 401


def test_downloads_can_repeat_before_expiry(client, auth_headers):
    t = _token(client, auth_headers)["token"]
    for _ in range(2):
        assert client.get(f"/api/bootstrap/assets.tar.gz?token={t}").status_code == 200


def test_install_script_has_server_address_substituted_once(client, auth_headers):
    t = _token(client, auth_headers)["token"]
    res = client.get(f"/api/bootstrap/install.sh?token={t}")
    assert res.status_code == 200 and "shellscript" in res.headers["content-type"]
    script = res.text
    assert 'SERVER_URL="${SERVER_URL:-http://192.168.0.105:8001}"' in script
    assert "__SERVER_URL__" not in script
    # 자리표시자 점검 줄이 치환에 같이 바뀌어 주소를 지우는 일이 없어야 한다
    assert 'PLACEHOLDER="__SERVER""_URL__"' in script
    out = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert out.returncode == 0, out.stderr


@pytest.mark.parametrize("bad", ['http://x";rm -rf /;"', "http://x; id", "http://x$(id)", "ftp://x", "x", "http://x y"])
def test_installer_script_refuses_unsafe_addresses(bad):
    with pytest.raises(BundleError):
        installer_script(bad)


def test_assets_are_the_makefile_install_service_files(client, auth_headers):
    t = _token(client, auth_headers)["token"]
    data = client.get(f"/api/bootstrap/assets.tar.gz?token={t}").content
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        names = {m.name for m in tf.getmembers()}
        mode = {m.name: m.mode for m in tf.getmembers()}
    assert {"visionguide-device.service", "visionguide-roi-editor.service", "visionguide-controls.service",
            "visionguide-systemctl.sudoers", "visionguide-network.sudoers", "auto_ap.sh"} <= names
    assert mode["auto_ap.sh"] == 0o755


def test_bundle_download_matches_dashboard_update_bundle(client, auth_headers):
    from app.services.bundle_builder import build_bundle
    t = _token(client, auth_headers)["token"]
    res = client.get(f"/api/bootstrap/bundle.tar.gz?token={t}")
    assert res.content == build_bundle().data and res.headers["x-bundle-id"] == build_bundle().bundle_id


PI = "http://10.0.0.7:5000"


def _pi_ok():
    respx.post(f"{PI}/api/identity").mock(return_value=httpx.Response(200, json={"ok": True, "usable": True}))
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": []}))


@respx.mock
def test_register_creates_device_and_provisions_it(client, auth_headers, db_session, monkeypatch):
    monkeypatch.setattr(bootstrap, "peer_ipv4", lambda request: "10.0.0.7")
    _pi_ok()
    t = _token(client, auth_headers)["token"]

    res = client.post("/api/bootstrap/register", json={"token": t, "hostname": "raspberrypi"})

    assert res.status_code == 200
    data = res.json()["data"]
    assert data["device_id"] == "pi-10-0-0-7" and data["provisioned"] is True
    row = db_session.query(Device).filter(Device.id == "pi-10-0-0-7").one()
    assert row.ip == "10.0.0.7" and row.name == "raspberrypi" and row.control_key
    assert hashlib.sha256(row.control_key.encode()).hexdigest() == row.api_key_hash
    assert "api_key" not in res.text                      # 키는 Pi에 서버가 직접 심는다 — 응답에 싣지 않는다


@respx.mock
def test_register_token_is_single_use_but_reinstall_rekeys(client, auth_headers, db_session, monkeypatch):
    monkeypatch.setattr(bootstrap, "peer_ipv4", lambda request: "10.0.0.7")
    _pi_ok()
    t1 = _token(client, auth_headers)["token"]
    assert client.post("/api/bootstrap/register", json={"token": t1}).status_code == 200
    assert client.post("/api/bootstrap/register", json={"token": t1}).status_code == 401   # 1회용
    first = db_session.query(Device).filter(Device.id == "pi-10-0-0-7").one().api_key_hash

    t2 = _token(client, auth_headers)["token"]                                              # 재설치
    assert client.post("/api/bootstrap/register", json={"token": t2}).status_code == 200
    db_session.expire_all()
    rows = db_session.query(Device).filter(Device.id == "pi-10-0-0-7").all()
    assert len(rows) == 1 and rows[0].api_key_hash != first


def test_register_rejects_bad_token_and_untrusted_address(client, auth_headers):
    assert client.post("/api/bootstrap/register", json={"token": "bogus"}).status_code == 401
    t = _token(client, auth_headers)["token"]
    # TestClient의 상대 주소는 사설 LAN이 아니다 — 외부 주소의 등록은 거부한다
    res = client.post("/api/bootstrap/register", json={"token": t})
    assert res.status_code == 400 and res.json()["error"] == "UNTRUSTED_ADDRESS"


@respx.mock
def test_register_reports_provision_failure_without_losing_device(client, auth_headers, db_session, monkeypatch):
    monkeypatch.setattr(bootstrap, "peer_ipv4", lambda request: "10.0.0.7")
    respx.post(f"{PI}/api/identity").mock(return_value=httpx.Response(503, json={"detail": "starting"}))
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": []}))
    t = _token(client, auth_headers)["token"]
    data = client.post("/api/bootstrap/register", json={"token": t}).json()["data"]
    assert data["provisioned"] is False and data["provision_error"]
    assert db_session.query(Device).filter(Device.id == "pi-10-0-0-7").one().control_key is None
