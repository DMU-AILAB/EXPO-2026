import hashlib
import uuid

from app.models.device import Device


def _new_pi(db_session):
    device_id = f"esp32-test-{uuid.uuid4().hex[:8]}"
    api_key = f"vg-test-{uuid.uuid4().hex}"
    db_session.add(Device(
        id=device_id, name="ESP32 test Pi", ip="192.0.2.10",
        api_key_hash=hashlib.sha256(api_key.encode()).hexdigest(),
    ))
    db_session.commit()
    return device_id, api_key


def test_dashboard_approval_and_wifi_command_lifecycle(client, auth_headers, db_session):
    device_id, api_key = _new_pi(db_session)
    esp32_id = "VG-ESP32-0123456789AB"
    heartbeat = client.patch(
        "/api/devices/me/heartbeat",
        json={"esp32": {"state": "unpaired", "candidates": [
            {"device_id": esp32_id, "name": "VisionGuide ESP32", "rssi": -48},
        ]}},
        headers={"X-API-Key": api_key},
    )
    assert heartbeat.status_code == 200

    bind = client.post(
        f"/api/devices/{device_id}/esp32/binding",
        json={"esp32_id": esp32_id}, headers=auth_headers,
    )
    assert bind.status_code == 200

    queued = client.post(
        f"/api/devices/{device_id}/esp32/wifi",
        json={"ssid": "Expo-Net", "password": "test-password"},
        headers=auth_headers,
    )
    assert queued.status_code == 202
    command_id = queued.json()["data"]["id"]
    assert "password" not in queued.json()["data"]

    control = client.get("/api/devices/me/esp32/control", headers={"X-API-Key": api_key})
    assert control.status_code == 200
    assert control.json()["data"]["binding"] == esp32_id
    assert control.json()["data"]["command"]["password"] == "test-password"

    result = client.post(
        f"/api/devices/me/esp32/commands/{command_id}/result",
        json={"state": "ok", "message": "Wi-Fi 연결 완료"},
        headers={"X-API-Key": api_key},
    )
    assert result.status_code == 200

    status = client.get(f"/api/devices/{device_id}/esp32", headers=auth_headers)
    assert status.status_code == 200
    assert status.json()["data"]["command"]["state"] == "ok"
    assert "password" not in status.text


def test_dashboard_cannot_bind_an_unreported_esp32(client, auth_headers, db_session):
    device_id, _api_key = _new_pi(db_session)
    response = client.post(
        f"/api/devices/{device_id}/esp32/binding",
        json={"esp32_id": "VG-ESP32-NOT-SEEN"}, headers=auth_headers,
    )
    assert response.status_code == 409


def test_one_esp32_cannot_be_bound_to_two_pis(client, auth_headers, db_session):
    first_id, first_key = _new_pi(db_session)
    second_id, second_key = _new_pi(db_session)
    esp32_id = "VG-ESP32-0123456789AB"
    for api_key in (first_key, second_key):
        heartbeat = client.patch(
            "/api/devices/me/heartbeat",
            json={"esp32": {"state": "unpaired", "candidates": [
                {"device_id": esp32_id, "name": "VisionGuide ESP32", "rssi": -48},
            ]}},
            headers={"X-API-Key": api_key},
        )
        assert heartbeat.status_code == 200

    first = client.post(
        f"/api/devices/{first_id}/esp32/binding",
        json={"esp32_id": esp32_id}, headers=auth_headers,
    )
    second = client.post(
        f"/api/devices/{second_id}/esp32/binding",
        json={"esp32_id": esp32_id}, headers=auth_headers,
    )
    assert first.status_code == 200
    assert second.status_code == 409
