"""구조물 수집 일괄 실행·예약 (`/api/calibration/*`).

수집 시작은 roi_editor(5000)가 아니라 **카메라 포트**로 가야 한다 — 기기별 중계
(`test_relays.py`)와 같은 계약을 여러 대에 걸쳐 확인한다.
"""

import json

import httpx
import pytest
import respx

from app.models.calibration import CalibrationRun, CalibrationSchedule
from app.models.camera import Camera
from app.models.device import Device
from app.models.schedule import ScheduledReboot
from app.services import calibration_service
from app.services.scheduler_service import (add_schedule_job, calibration_job_id, remove_calibration_job,
                                            remove_schedule_job, scheduler)

A = "http://192.168.1.101:8081"
B = "http://192.168.1.102:8082"
C = "http://192.168.1.103:8083"


@pytest.fixture
def fleet(db_session):
    db_session.add_all([
        Device(id="dev-a", name="A 정문", ip="192.168.1.101", api_key_hash="x", config_etag="e", status="online"),
        Device(id="dev-b", name="B 후문", ip="192.168.1.102", api_key_hash="x", config_etag="e", status="online"),
        Device(id="dev-c", name="C 꺼짐", ip="192.168.1.103", api_key_hash="x", config_etag="e", status="offline"),
        Camera(id="cam0", device_id="dev-a", port=8081, model_variant="v10_320"),
        Camera(id="cam1", device_id="dev-a", port=8091, model_variant="v10_320", is_active=False),
        Camera(id="cam0", device_id="dev-b", port=8082, model_variant="v4_320"),
        Camera(id="cam0", device_id="dev-c", port=8083, model_variant="v10_320"),
    ])
    db_session.commit()


@pytest.fixture
def auth(client, admin_user):
    token = client.post("/api/auth/login", json={
        "username": "admin", "password": "test_password"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clean_jobs():
    calibration_service.invalidate_active_cache()
    yield
    calibration_service.invalidate_active_cache()
    for job in scheduler.get_jobs():
        scheduler.remove_job(job.id)


def _ok(route_url):
    return respx.post(f"{route_url}/calibrate/start").mock(
        return_value=httpx.Response(200, json={"ok": True}))


# ------------------------------------------------------------------ 지금 실행

@respx.mock
def test_run_all_starts_every_active_camera_and_skips_offline(client, auth, fleet, db_session):
    ra, rb, rc = _ok(A), _ok(B), _ok(C)
    body = client.post("/api/calibration/run", headers=auth,
                       json={"target_mode": "all", "seconds": 600}).json()["data"]

    assert ra.called and rb.called
    assert ra.calls.last.request.url.params["seconds"] == "600"
    assert not rc.called                             # 오프라인 기기는 연결을 시도하지 않는다
    assert body["total"] == 3 and body["started"] == 2   # 비활성 카메라(cam1)는 대상이 아니다
    skipped = [r for r in body["results"] if not r["ok"]]
    assert [r["device_id"] for r in skipped] == ["dev-c"]
    assert "오프라인" in skipped[0]["error"]
    assert db_session.query(CalibrationRun).count() == 3


@respx.mock
def test_run_by_model_only_targets_that_model(client, auth, fleet):
    ra, rb = _ok(A), _ok(B)
    body = client.post("/api/calibration/run", headers=auth, json={
        "target_mode": "model", "targets": ["v4_320"], "seconds": 60}).json()["data"]
    assert rb.called and not ra.called
    assert [(r["device_id"], r["model_variant"]) for r in body["results"]] == [("dev-b", "v4_320")]


@respx.mock
def test_run_by_devices(client, auth, fleet):
    ra, rb = _ok(A), _ok(B)
    client.post("/api/calibration/run", headers=auth,
                json={"target_mode": "devices", "targets": ["dev-a"], "seconds": 60})
    assert ra.called and not rb.called


@respx.mock
def test_device_refusal_and_unreachable_are_recorded_not_raised(client, auth, fleet):
    respx.post(f"{A}/calibrate/start").mock(return_value=httpx.Response(
        200, json={"ok": False, "error": "이미 수집 중입니다"}))
    respx.post(f"{B}/calibrate/start").mock(side_effect=httpx.ConnectError("refused"))
    res = client.post("/api/calibration/run", headers=auth, json={"target_mode": "all", "seconds": 60})
    assert res.status_code == 200                    # 한 대의 실패가 요청 전체를 실패시키지 않는다
    errors = {r["device_id"]: r["error"] for r in res.json()["data"]["results"]}
    assert errors["dev-a"] == "이미 수집 중입니다"
    assert "연결할 수 없습니다" in errors["dev-b"]


def test_run_validation(client, auth, fleet):
    def post(payload):
        return client.post("/api/calibration/run", headers=auth, json=payload).status_code
    assert post({"target_mode": "all", "seconds": 5}) in (400, 422)        # 기기와 같은 하한
    assert post({"target_mode": "nope"}) == 400
    assert post({"target_mode": "model", "targets": []}) == 400
    assert post({"target_mode": "model", "targets": ["v99"]}) == 400
    assert post({"target_mode": "devices", "targets": ["ghost"]}) == 400
    assert post({"target_mode": "model", "targets": ["v15_320"]}) == 400   # 해당 카메라 없음


@respx.mock
def test_targets_show_last_run(client, auth, fleet):
    _ok(A), _ok(B)
    client.post("/api/calibration/run", headers=auth, json={"target_mode": "all", "seconds": 60})
    data = client.get("/api/calibration/targets", headers=auth).json()["data"]
    a = next(d for d in data["devices"] if d["id"] == "dev-a")
    cams = {c["id"]: c for c in a["cameras"]}
    assert cams["cam0"]["last_run"]["ok"] is True
    assert cams["cam1"]["last_run"] is None
    assert "v10_320" in data["model_variants"]


# ------------------------------------------------------------------ 예약

def test_schedule_crud_registers_job(client, auth, fleet, db_session):
    res = client.post("/api/calibration/schedules", headers=auth, json={
        "name": "폐장 후", "days": [1, 3, 5], "hour": 22, "minute": 30, "seconds": 600,
        "target_mode": "model", "targets": ["v10_320"]})
    assert res.status_code == 201
    s = res.json()["data"]
    assert s["display"] == "월·수·금 22:30"
    job = scheduler.get_job(calibration_job_id(s["id"]))
    assert job is not None and tuple(job.args) == (s["id"],)

    client.patch(f"/api/calibration/schedules/{s['id']}", headers=auth, json={"is_enabled": False})
    assert scheduler.get_job(calibration_job_id(s["id"])) is None

    client.patch(f"/api/calibration/schedules/{s['id']}", headers=auth, json={"is_enabled": True, "minute": 45})
    assert scheduler.get_job(calibration_job_id(s["id"])) is not None
    assert client.get("/api/calibration/schedules", headers=auth).json()["data"][0]["minute"] == 45

    assert client.delete(f"/api/calibration/schedules/{s['id']}", headers=auth).status_code == 204
    assert scheduler.get_job(calibration_job_id(s["id"])) is None
    assert db_session.query(CalibrationSchedule).count() == 0


def test_schedule_validation(client, auth, fleet):
    def post(**kw):
        body = {"days": [1], "hour": 22, "minute": 0, "target_mode": "all"} | kw
        return client.post("/api/calibration/schedules", headers=auth, json=body).status_code
    assert post(days=[]) == 400
    assert post(days=[7]) == 400
    assert post(hour=24) == 400
    assert post(minute=60) == 400
    assert post(seconds=2000) in (400, 422)


def test_calibration_job_does_not_clobber_reboot_job_with_same_id(db_session, fleet):
    """재부팅 예약의 작업 id는 `str(id)`다. 수집 예약도 같은 id를 쓰면 덮어쓴다."""
    from app.services.scheduler_service import add_calibration_job
    add_schedule_job(1, "dev-a", [1], 3)
    add_calibration_job(1, [1], 22, 0)
    assert scheduler.get_job("1") is not None
    assert scheduler.get_job(calibration_job_id(1)) is not None
    remove_calibration_job(1)
    remove_schedule_job(1)


@pytest.mark.asyncio
@respx.mock
async def test_execute_schedule_resolves_targets_at_run_time(db_session, fleet, monkeypatch):
    """예약 시각에 대상을 다시 해석한다 — 예약 뒤에 추가된 카메라도 들어간다."""
    import app.database as database
    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    sched = CalibrationSchedule(days=json.dumps([1]), hour=22, minute=0, seconds=120,
                                target_mode="model", targets=json.dumps(["v10_320"]))
    db_session.add(sched)
    db_session.add(Camera(id="cam2", device_id="dev-b", port=8092, model_variant="v10_320"))
    db_session.commit()

    ra = _ok(A)
    rb2 = respx.post("http://192.168.1.102:8092/calibrate/start").mock(
        return_value=httpx.Response(200, json={"ok": True}))
    await calibration_service.execute_schedule(sched.id)

    assert ra.called and rb2.called
    assert rb2.calls.last.request.url.params["seconds"] == "120"
    runs = db_session.query(CalibrationRun).filter(CalibrationRun.schedule_id == sched.id).all()
    assert {(r.device_id, r.camera_id) for r in runs} == {("dev-a", "cam0"), ("dev-b", "cam2"), ("dev-c", "cam0")}


@pytest.mark.asyncio
async def test_disabled_schedule_does_nothing(db_session, fleet, monkeypatch):
    import app.database as database
    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    sched = CalibrationSchedule(days=json.dumps([1]), hour=22, minute=0, seconds=120,
                                target_mode="all", targets="[]", is_enabled=False)
    db_session.add(sched)
    db_session.commit()
    await calibration_service.execute_schedule(sched.id)
    assert db_session.query(CalibrationRun).count() == 0


def test_runs_are_pruned(db_session, fleet, monkeypatch):
    monkeypatch.setattr(calibration_service, "KEEP_RUNS", 3)
    for i in range(5):
        db_session.add(CalibrationRun(batch_id=str(i), device_id="dev-a", camera_id="cam0",
                                      seconds=60, ok=True))
    db_session.commit()
    calibration_service._prune(db_session)
    assert [r.batch_id for r in db_session.query(CalibrationRun).order_by(CalibrationRun.id)] == ["2", "3", "4"]


# ------------------------------------------------------------------ 진행 중 표시

@respx.mock
def test_active_asks_devices_and_skips_offline(client, auth, fleet):
    ra = respx.get(f"{A}/calibrate/status").mock(return_value=httpx.Response(
        200, json={"running": True, "remaining_sec": 42.0, "frames": 10}))
    rb = respx.get(f"{B}/calibrate/status").mock(side_effect=httpx.ConnectError("down"))
    rc = respx.get(f"{C}/calibrate/status").mock(return_value=httpx.Response(200, json={"running": True}))
    data = client.get("/api/calibration/active", headers=auth).json()["data"]
    assert data == {"dev-a": {"remaining_sec": 42.0, "cameras": 1}}
    assert ra.called and rb.called          # 응답 없는 카메라는 "수집 중 아님"으로 둔다
    assert not rc.called                    # 오프라인 기기는 묻지 않는다


@respx.mock
def test_active_cache_is_invalidated_by_start(client, auth, fleet):
    status = respx.get(f"{A}/calibrate/status").mock(return_value=httpx.Response(200, json={"running": False}))
    respx.get(f"{B}/calibrate/status").mock(return_value=httpx.Response(200, json={"running": False}))
    assert client.get("/api/calibration/active", headers=auth).json()["data"] == {}
    client.get("/api/calibration/active", headers=auth)
    assert status.call_count == 1           # TTL 안에서는 기기를 다시 두드리지 않는다

    respx.post(f"{A}/calibrate/start").mock(return_value=httpx.Response(200, json={"ok": True}))
    status.mock(return_value=httpx.Response(200, json={"running": True, "remaining_sec": 300}))
    client.post("/api/devices/dev-a/cameras/cam0/calibration/start", headers=auth, json={"seconds": 300})
    data = client.get("/api/calibration/active", headers=auth).json()["data"]
    assert data["dev-a"]["remaining_sec"] == 300   # 시작 직후 바로 "수집 중"으로 보인다
