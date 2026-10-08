"""POST /api/devices/model-variant — 기존 기기의 카메라 모델을 일괄 변경한다.

지키는 것: ① 가중치가 없으면 바꾸지 않는다(파이프라인이 죽고 재시작을 반복한다) ② Coral은 건드리지 않는다
③ 바꾼 뒤 FPS가 미달이면 **그 카메라만** 되돌린다 ④ 기존 필드를 보존한다(목록 전체 치환이라 빠뜨리면 기본값으로
돌아간다) ⑤ 한 대가 실패해도 나머지는 계속한다 ⑥ 서버 캐시는 Pi가 받아들인 뒤에만 바꾼다.
시계와 sleep은 주입하므로 실제로 기다리지 않는다.
"""

import json

import httpx
import pytest
import respx

from app.models.camera import Camera
from app.models.device import Device
from app.routers import devices as devices_router
from app.services import model_rollout as mr

IP = "10.0.0.7"
PI = f"http://{IP}:5000"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """대기 시간을 없앤다 — 안정화·폴링 간격과 검증 제한 시간."""
    monkeypatch.setattr(mr, "SETTLE_SEC", 0.0)
    monkeypatch.setattr(mr, "POLL_SEC", 0.0)
    monkeypatch.setattr(mr, "VERIFY_SEC", 0.0)


@pytest.fixture
def auth(client, admin_user):
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test_password"}
                        ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _profile(cid="cam0", **kw):
    # 카메라마다 포트가 달라야 하고, 활성 카메라 둘 이상이 Coral을 쓸 수 없다(기기의 검증 규칙) — 그래서 tflite
    p = {"id": cid, "enabled": True, "label": "정문", "backend": "auto", "source": "0", "rotation": 0,
         "inference_backend": "tflite", "roi_config": f"rois.{cid}.json", "port": 8080 + int(cid[-1]),
         "traffic_db": f"ft.{cid}.db",
         "swap_rb": True, "model_variant": "v4_320", "capture_preset": "auto",
         "require_person_for_trigger": True, "roi_crop_inference": True}
    p.update(kw)
    return p


def _device(db, did="pi-1", ip=IP, cams=("cam0",), variant="v4_320"):
    d = Device(id=did, name=did, ip=ip, api_key_hash="x", config_etag="e0", control_key="vg_k")
    db.add(d)
    for c in cams:
        db.add(Camera(id=c, device_id=did, port=8080, model_variant=variant, is_active=True))
    db.commit()
    return d


def _variants(*available, default="v10_320", omit_flag=False):
    keys = ["v4_320", "v10_320", "v15_320"]
    out = []
    for k in keys:
        v = {"key": k}
        if not omit_flag:
            v["available"] = k in available
        out.append(v)
    return httpx.Response(200, json={"variants": out, "default": default})


def _metrics(fps_by_cam):
    return httpx.Response(200, json={"cameras": [
        {"camera_id": c, "streaming": f is not None, "stale": f is None, "fps": f} for c, f in fps_by_cam.items()]})


def _mock_pi(*, profiles, available=("v4_320", "v10_320", "v15_320"), fps=None, ip=IP, omit_flag=False):
    base = f"http://{ip}:5000"
    respx.get(f"{base}/api/model-variants").mock(return_value=_variants(*available, omit_flag=omit_flag))
    # 실제 Pi처럼 상태를 가진다 — POST로 저장한 목록을 이후 GET이 돌려준다(롤백이 최신 목록 위에서 이뤄지는지 보려고)
    state = {"cameras": profiles}

    def save(request):
        state["cameras"] = json.loads(request.content)["cameras"]
        return httpx.Response(200, json={"ok": True})

    respx.get(f"{base}/api/cameras").mock(side_effect=lambda r: httpx.Response(200, json={"cameras": state["cameras"]}))
    post = respx.post(f"{base}/api/cameras").mock(side_effect=save)
    respx.get(f"{base}/api/metrics").mock(
        return_value=_metrics(fps if fps is not None else {p["id"]: 12.5 for p in profiles}))
    return post


def _rollout(client, auth, **body):
    return client.post("/api/devices/model-variant", json={"to": "v15_320", **body}, headers=auth)


def _sent(post, i=-1):
    return json.loads(post.calls[i].request.content)["cameras"]


# --- 정상 ------------------------------------------------------------------------------

@respx.mock
def test_모델을_바꾸고_기존_필드를_보존한다(client, auth, db_session):
    _device(db_session)
    post = _mock_pi(profiles=[_profile()])
    res = _rollout(client, auth)

    assert res.status_code == 200
    [r] = res.json()["data"]["results"]
    assert r["ok"] is True and r["changed"][0]["previous"] == "v4_320" and r["changed"][0]["current"] == "v15_320"
    [sent] = _sent(post)
    assert sent["model_variant"] == "v15_320"
    # 백엔드가 모르는 필드까지 전부 보존 — 목록 전체 치환이라 빠뜨리면 코드 기본값으로 돌아간다
    expected = _profile()
    expected["model_variant"] = "v15_320"
    assert sent == expected


@respx.mock
def test_서버_캐시는_Pi가_받아들인_뒤에_바뀐다(client, auth, db_session):
    _device(db_session)
    _mock_pi(profiles=[_profile()])
    _rollout(client, auth)
    db_session.expire_all()
    assert db_session.query(Camera).filter(Camera.id == "cam0").one().model_variant == "v15_320"
    assert db_session.query(Device).filter(Device.id == "pi-1").one().config_etag != "e0"


@respx.mock
def test_Pi가_거부하면_캐시를_바꾸지_않는다(client, auth, db_session):
    _device(db_session)
    post = _mock_pi(profiles=[_profile()])
    post.mock(return_value=httpx.Response(400, json={"detail": ["bad"]}))
    [r] = _rollout(client, auth).json()["data"]["results"]
    assert r["ok"] is False and r["error"]
    db_session.expire_all()
    assert db_session.query(Camera).filter(Camera.id == "cam0").one().model_variant == "v4_320"


@respx.mock
def test_이미_목표인_카메라는_건드리지_않는다(client, auth, db_session):
    _device(db_session)
    post = _mock_pi(profiles=[_profile(model_variant="v15_320")])
    [r] = _rollout(client, auth).json()["data"]["results"]
    assert r["ok"] is True and r["changed"] == [] and post.call_count == 0


@respx.mock
def test_from_variants로_대상을_고른다(client, auth, db_session):
    _device(db_session, cams=("cam0", "cam1"))
    post = _mock_pi(profiles=[_profile("cam0", model_variant="v4_320"), _profile("cam1", model_variant="v10_320")])
    [r] = _rollout(client, auth, from_variants=["v4_320"]).json()["data"]["results"]
    assert [c["camera_id"] for c in r["changed"]] == ["cam0"]
    by_id = {p["id"]: p for p in _sent(post)}
    assert by_id["cam0"]["model_variant"] == "v15_320" and by_id["cam1"]["model_variant"] == "v10_320"


@respx.mock
def test_모델_필드가_없는_프로필은_기기의_기본값으로_본다(client, auth, db_session):
    _device(db_session)
    p = _profile()
    del p["model_variant"]                       # Pi 기본값(v10_320)이 적용되는 상태
    post = _mock_pi(profiles=[p])
    [r] = _rollout(client, auth).json()["data"]["results"]
    assert r["changed"][0]["previous"] == "v10_320" and _sent(post)[0]["model_variant"] == "v15_320"


@respx.mock
def test_비활성_카메라는_건드리지_않는다(client, auth, db_session):
    _device(db_session, cams=("cam0", "cam1"))
    post = _mock_pi(profiles=[_profile("cam0"), _profile("cam1", enabled=False)])
    [r] = _rollout(client, auth).json()["data"]["results"]
    assert [c["camera_id"] for c in r["changed"]] == ["cam0"]
    assert {p["id"]: p["model_variant"] for p in _sent(post)} == {"cam0": "v15_320", "cam1": "v4_320"}


# --- 안전장치 ---------------------------------------------------------------------------

@respx.mock
def test_가중치가_없으면_바꾸지_않는다(client, auth, db_session):
    """파일이 없는 모델로 바꾸면 파이프라인이 시작하자마자 죽고 재시작을 반복해 탐지가 멈춘다."""
    _device(db_session)
    post = _mock_pi(profiles=[_profile()], available=("v4_320", "v10_320"))        # v15 없음
    [r] = _rollout(client, auth).json()["data"]["results"]
    assert r["ok"] is False and r["skipped"] is True and "가중치" in r["reason"]
    assert post.call_count == 0


@respx.mock
def test_구버전_기기는_증명할_수_없으므로_건너뛴다(client, auth, db_session):
    _device(db_session)
    post = _mock_pi(profiles=[_profile()], omit_flag=True)                         # available 필드 없음
    [r] = _rollout(client, auth).json()["data"]["results"]
    assert r["skipped"] is True and "구버전" in r["reason"] and post.call_count == 0


@respx.mock
def test_기기가_모르는_모델이면_건너뛴다(client, auth, db_session):
    _device(db_session)
    post = _mock_pi(profiles=[_profile()])
    [r] = _rollout(client, auth, to="v99_320").json()["data"]["results"]
    assert r["skipped"] is True and "모르는" in r["reason"] and post.call_count == 0


@respx.mock
def test_Coral_카메라는_건드리지_않는다(client, auth, db_session):
    _device(db_session, cams=("cam0", "cam1"))
    post = _mock_pi(profiles=[_profile("cam0", inference_backend="edgetpu"), _profile("cam1")])
    [r] = _rollout(client, auth).json()["data"]["results"]
    assert [c["camera_id"] for c in r["changed"]] == ["cam1"]
    assert r["skipped_cameras"][0]["camera_id"] == "cam0" and "Coral" in r["skipped_cameras"][0]["reason"]
    assert {p["id"]: p["model_variant"] for p in _sent(post)}["cam0"] == "v4_320"


@respx.mock
def test_Pi가_거부할_설정은_보내지_않는다(client, auth, db_session):
    _device(db_session)
    post = _mock_pi(profiles=[_profile(), _profile()])                             # id 중복 — 기기가 거부할 설정
    [r] = _rollout(client, auth).json()["data"]["results"]
    assert r["ok"] is False and post.call_count == 0


# --- FPS 확인과 롤백 -----------------------------------------------------------------------

@respx.mock
def test_FPS가_미달이면_그_카메라만_되돌린다(client, auth, db_session):
    _device(db_session, cams=("cam0", "cam1"))
    post = _mock_pi(profiles=[_profile("cam0"), _profile("cam1")], fps={"cam0": 12.0, "cam1": 6.2})
    [r] = _rollout(client, auth).json()["data"]["results"]

    assert r["ok"] is False and "되돌렸습니다" in r["error"] and "6.2" in r["error"]
    by = {c["camera_id"]: c for c in r["changed"]}
    assert by["cam0"]["rolled_back"] is False and by["cam1"]["rolled_back"] is True
    assert post.call_count == 2                                                    # 변경 + 롤백
    final = {p["id"]: p["model_variant"] for p in _sent(post, -1)}
    assert final == {"cam0": "v15_320", "cam1": "v4_320"}                          # 미달인 쪽만 이전 값
    db_session.expire_all()
    cache = {c.id: c.model_variant for c in db_session.query(Camera).all()}
    assert cache == {"cam0": "v15_320", "cam1": "v4_320"}


@respx.mock
def test_측정할_수_없으면_미달로_본다(client, auth, db_session):
    """지표가 없거나 오래됐다 = 새 파이프라인이 뜨지 못했을 수 있다 — 통과로 보면 안 된다."""
    _device(db_session)
    post = _mock_pi(profiles=[_profile()], fps={"cam0": None})
    [r] = _rollout(client, auth).json()["data"]["results"]
    assert r["changed"][0]["rolled_back"] is True and "측정 불가" in r["error"]
    assert _sent(post, -1)[0]["model_variant"] == "v4_320"


@respx.mock
def test_메트릭_조회가_실패해도_미달로_본다(client, auth, db_session):
    _device(db_session)
    post = _mock_pi(profiles=[_profile()])
    respx.get(f"{PI}/api/metrics").mock(return_value=httpx.Response(503))
    [r] = _rollout(client, auth).json()["data"]["results"]
    assert r["changed"][0]["rolled_back"] is True
    assert post.call_count == 2


@respx.mock
def test_정확히_기준값이면_통과한다(client, auth, db_session):
    _device(db_session)
    post = _mock_pi(profiles=[_profile()], fps={"cam0": mr.MIN_FPS})
    [r] = _rollout(client, auth).json()["data"]["results"]
    assert r["ok"] is True and post.call_count == 1


@respx.mock
def test_롤백도_실패하면_직접_확인하라고_알린다(client, auth, db_session):
    _device(db_session)
    post = _mock_pi(profiles=[_profile()], fps={"cam0": 3.0})
    post.mock(side_effect=[httpx.Response(200, json={"ok": True}), httpx.Response(503, json={"detail": "down"})])
    [r] = _rollout(client, auth).json()["data"]["results"]
    assert r["ok"] is False and "되돌리지 못했습니다" in r["error"] and "v4_320" in r["error"]
    assert r["changed"][0]["rolled_back"] is False
    db_session.expire_all()
    assert db_session.query(Camera).filter(Camera.id == "cam0").one().model_variant == "v15_320"   # 실제 상태와 같다


@respx.mock
def test_verify를_끄면_측정하지_않는다(client, auth, db_session):
    _device(db_session)
    _mock_pi(profiles=[_profile()], fps={"cam0": 1.0})
    [r] = _rollout(client, auth, verify=False).json()["data"]["results"]
    assert r["ok"] is True and r["changed"][0]["rolled_back"] is False
    assert not any(c.request.url.path == "/api/metrics" for c in respx.calls)


@respx.mock
def test_바꾼_직후에는_안정화를_기다린_뒤_잰다(client, auth, db_session, monkeypatch):
    """이전 모델이 보고한 지표가 아직 신선해 보여 새 모델이 느려도 통과로 오판하는 것을 막는다."""
    _device(db_session)
    _mock_pi(profiles=[_profile()])
    sleeps = []

    async def fake_sleep(sec):
        sleeps.append(sec)

    async def go():
        return await mr.rollout_device(db_session, db_session.query(Device).one(), to="v15_320",
                                       sleep=fake_sleep, settle_sec=8.0)
    import asyncio
    asyncio.run(go())
    assert sleeps and sleeps[0] == 8.0                       # 첫 대기가 안정화 — 측정보다 먼저다


# --- 미리보기 · 모델 선행 배포 ----------------------------------------------------------------

@respx.mock
def test_dry_run은_아무것도_바꾸지_않는다(client, auth, db_session):
    _device(db_session)
    post = _mock_pi(profiles=[_profile()])
    res = _rollout(client, auth, dry_run=True).json()["data"]
    [r] = res["results"]
    assert res["dry_run"] is True and r["dry_run"] is True
    assert r["changed"][0]["previous"] == "v4_320" and r["changed"][0]["current"] == "v15_320"
    assert post.call_count == 0
    db_session.expire_all()
    assert db_session.query(Camera).filter(Camera.id == "cam0").one().model_variant == "v4_320"


@respx.mock
def test_dry_run도_가중치가_없으면_알린다(client, auth, db_session):
    _device(db_session)
    _mock_pi(profiles=[_profile()], available=("v4_320",))
    [r] = _rollout(client, auth, dry_run=True).json()["data"]["results"]
    assert r["skipped"] is True and "가중치" in r["reason"]


@respx.mock
def test_push_models면_가중치가_없는_기기에_먼저_올린_뒤_바꾼다(client, auth, db_session, monkeypatch):
    _device(db_session)
    base = PI
    # 처음엔 v15 없음 → 푸시 뒤에는 있음
    respx.get(f"{base}/api/model-variants").mock(side_effect=[_variants("v4_320", "v10_320"),
                                                            _variants("v4_320", "v10_320", "v15_320")])
    respx.get(f"{base}/api/cameras").mock(return_value=httpx.Response(200, json={"cameras": [_profile()]}))
    post = respx.post(f"{base}/api/cameras").mock(return_value=httpx.Response(200, json={"ok": True}))
    respx.get(f"{base}/api/metrics").mock(return_value=_metrics({"cam0": 12.0}))
    pushed = []

    async def fake_push(db, device):
        pushed.append(device.id)
    monkeypatch.setattr(mr, "_push_models", fake_push)

    [r] = _rollout(client, auth, push_models=True).json()["data"]["results"]
    assert pushed == ["pi-1"] and r["models_pushed"] is True and r["ok"] is True
    assert _sent(post)[0]["model_variant"] == "v15_320"


@respx.mock
def test_push_models여도_dry_run은_올리지_않는다(client, auth, db_session, monkeypatch):
    _device(db_session)
    _mock_pi(profiles=[_profile()], available=("v4_320",))
    pushed = []

    async def fake_push(db, device):
        pushed.append(1)
    monkeypatch.setattr(mr, "_push_models", fake_push)
    [r] = _rollout(client, auth, push_models=True, dry_run=True).json()["data"]["results"]
    assert pushed == [] and r["needs_push"] is True and r["changed"][0]["current"] == "v15_320"


@respx.mock
def test_push_models를_껐으면_올리지_않는다(client, auth, db_session, monkeypatch):
    _device(db_session)
    _mock_pi(profiles=[_profile()], available=("v4_320",))
    pushed = []
    monkeypatch.setattr(mr, "_push_models", lambda *a: pushed.append(1))
    [r] = _rollout(client, auth).json()["data"]["results"]
    assert pushed == [] and r["skipped"] is True


@respx.mock
def test_모델_푸시가_실패하면_그_기기만_실패한다(client, auth, db_session, monkeypatch):
    _device(db_session)
    _mock_pi(profiles=[_profile()], available=("v4_320",))

    async def boom(db, device):
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="업데이트 실패")
    monkeypatch.setattr(mr, "_push_models", boom)
    [r] = _rollout(client, auth, push_models=True).json()["data"]["results"]
    assert r["ok"] is False and "업데이트 실패" in r["error"]


# --- 여러 기기 ---------------------------------------------------------------------------

@respx.mock
def test_한_대가_실패해도_나머지는_계속한다(client, auth, db_session):
    _device(db_session, did="pi-1", ip="10.0.0.7")
    _device(db_session, did="pi-2", ip="10.0.0.8")
    _mock_pi(profiles=[_profile()], ip="10.0.0.7")
    respx.get("http://10.0.0.8:5000/api/model-variants").mock(side_effect=httpx.ConnectError("down"))

    results = {r["device_id"]: r for r in _rollout(client, auth).json()["data"]["results"]}
    assert results["pi-1"]["ok"] is True
    assert results["pi-2"]["ok"] is False and results["pi-2"]["error"]


@respx.mock
def test_device_ids로_대상을_고르고_없는_기기는_알린다(client, auth, db_session):
    _device(db_session, did="pi-1", ip="10.0.0.7")
    _device(db_session, did="pi-2", ip="10.0.0.8")
    _mock_pi(profiles=[_profile()], ip="10.0.0.7")
    results = _rollout(client, auth, device_ids=["pi-1", "ghost"]).json()["data"]["results"]
    by = {r["device_id"]: r for r in results}
    assert by["pi-1"]["ok"] is True and by["ghost"]["ok"] is False and "찾을 수 없습니다" in by["ghost"]["error"]
    assert "pi-2" not in by
    assert not any(c.request.url.host == "10.0.0.8" for c in respx.calls)


@respx.mock
def test_device_ids를_생략하면_전체다(client, auth, db_session):
    _device(db_session, did="pi-1", ip="10.0.0.7")
    _device(db_session, did="pi-2", ip="10.0.0.8")
    _mock_pi(profiles=[_profile()], ip="10.0.0.7")
    _mock_pi(profiles=[_profile()], ip="10.0.0.8")
    assert {r["device_id"] for r in _rollout(client, auth).json()["data"]["results"]} == {"pi-1", "pi-2"}


# --- 라우트 ------------------------------------------------------------------------------

def test_인증이_필요하다(client):
    assert client.post("/api/devices/model-variant", json={"to": "v15_320"}).status_code in (401, 403)


def test_to는_필수다(client, auth):
    assert client.post("/api/devices/model-variant", json={}, headers=auth).status_code in (400, 422)


def test_경로가_device_id로_먹히지_않는다(client, auth):
    """`/{device_id}`보다 먼저 선언돼야 한다 — 아니면 'model-variant'라는 id로 해석돼 404/405가 난다."""
    res = client.post("/api/devices/model-variant", json={"to": "v15_320"}, headers=auth)
    assert res.status_code == 200 and res.json()["data"]["results"] == []


def test_라우트_선언_순서():
    paths = [r.path for r in devices_router.router.routes]
    assert paths.index("/api/devices/model-variant") < paths.index("/api/devices/{device_id}")
