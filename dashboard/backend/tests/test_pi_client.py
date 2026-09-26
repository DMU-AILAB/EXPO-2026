import json

import httpx
import respx

from app.services.pi_client import PiClient


@respx.mock
async def test_post_identity_sends_current_key_without_pairing_token():
    route = respx.post("http://192.168.1.50:5000/api/identity").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    result = await PiClient("192.168.1.50").post_identity(
        device_id="pi-01",
        api_key="new-api-key",
        server_url="http://pc:8000",
        current_key="old-device-key",
    )

    assert result == {"ok": True}
    request = route.calls[0].request
    assert request.headers["X-Device-Key"] == "old-device-key"
    assert json.loads(request.read()) == {
        "device_id": "pi-01",
        "api_key": "new-api-key",
        "server_url": "http://pc:8000",
        "name": "",
        "location": "",
        "registered_at": "",
    }


@respx.mock
async def test_scan_cameras_reads_hardware_discovery_endpoint():
    route = respx.get("http://192.168.1.50:5000/api/cameras/scan").mock(
        return_value=httpx.Response(200, json={
            "cameras": [{"num": 0, "model": "imx708_wide_noir"}]
        })
    )

    result = await PiClient("192.168.1.50").scan_cameras()

    assert route.called
    assert result == [{"num": 0, "model": "imx708_wide_noir"}]
