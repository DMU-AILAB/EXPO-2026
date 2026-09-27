import json

import httpx
import pytest
import respx

from app.models.camera import Camera
from app.models.device import Device


PI = "http://192.168.1.101:5000"


@pytest.fixture
def auth(client, admin_user):
    token = client.post("/api/auth/login", json={
        "username": "admin", "password": "test_password"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def device(db_session):
    item = Device(
        id="cam-entrance-01", name="Entrance", ip="192.168.1.101",
        location="Entrance", api_key_hash="x", config_etag="etag-1",
    )
    db_session.add(item)
    db_session.commit()
    return item


@respx.mock
def test_legacy_camera_model_update_materializes_profile(client, auth, db_session, device):
    db_session.add(Camera(
        id="legacy", device_id=device.id, port=8080,
        capture_preset="auto", model_variant="v10_320", rotation=0,
        require_person=True, is_active=True,
    ))
    db_session.commit()

    respx.get(f"{PI}/api/model-variants").mock(return_value=httpx.Response(
        200, json={"variants": [{"key": "v10_320"}, {"key": "v11_yolo26n_320"}]}))
    respx.get(f"{PI}/api/cameras").mock(return_value=httpx.Response(
        200, json={"cameras": []}))
    post = respx.post(f"{PI}/api/cameras").mock(return_value=httpx.Response(
        200, json={"ok": True}))

    response = client.patch(
        f"/api/devices/{device.id}/cameras/legacy",
        json={"model_variant": "v11_yolo26n_320"},
        headers={**auth, "If-Match": "etag-1"},
    )

    assert response.status_code == 200, response.text
    payload = json.loads(post.calls[0].request.content)
    assert payload["cameras"][0]["id"] == "legacy"
    assert payload["cameras"][0]["model_variant"] == "v11_yolo26n_320"
