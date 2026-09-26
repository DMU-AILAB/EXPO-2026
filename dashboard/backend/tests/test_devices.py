import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.models.device import Device

@pytest.fixture
def auth_headers(client: TestClient, admin_user):
    login_resp = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "test_password"
    })
    token = login_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

def test_create_and_get_device(client: TestClient, auth_headers):
    # 1. Create Device
    create_resp = client.post("/api/devices", json={
        "id": "test-cam-01",
        "name": "Test Camera",
        "ip": "192.168.1.50",
        "location": "Lobby"
    }, headers=auth_headers)
    
    assert create_resp.status_code == 201
    data = create_resp.json()
    assert data["ok"] is True
    assert data["data"]["id"] == "test-cam-01"
    assert "api_key" in data["data"]
    
    # 2. Get Device List
    list_resp = client.get("/api/devices", headers=auth_headers)
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert list_data["total"] == 1
    assert list_data["data"][0]["id"] == "test-cam-01"
    
    # 3. Get Device Detail
    detail_resp = client.get("/api/devices/test-cam-01", headers=auth_headers)
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert detail_data["data"]["id"] == "test-cam-01"
    assert detail_data["data"]["name"] == "Test Camera"
    assert "cameras" in detail_data["data"]


@respx.mock
def test_create_device_persists_control_key_after_provisioning(client: TestClient, auth_headers, db_session):
    pi = "http://192.168.1.51:5000"
    respx.post(f"{pi}/api/identity").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    respx.get(f"{pi}/api/cameras").mock(
        return_value=httpx.Response(200, json={"cameras": []})
    )

    response = client.post("/api/devices", json={
        "id": "pi-01",
        "name": "Pi 01",
        "ip": "192.168.1.51",
    }, headers=auth_headers)

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["provisioned"] is True
    stored = db_session.query(Device).filter(Device.id == "pi-01").one()
    assert stored.control_key == body["api_key"]
    identity_request = respx.calls[0].request
    assert "x-device-key" not in identity_request.headers
    assert "pairing_token" not in identity_request.read().decode()
