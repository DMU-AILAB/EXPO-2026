import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta
import hashlib
from app.main import app

@pytest.fixture
def test_device_api_key():
    # In a real test setup, you would create a device in DB and return its API key
    # For now, this is a skeleton test since we're mocking or relying on a test DB
    return "test-api-key-123"

def test_ingest_event_unauthorized(client):
    res = client.post("/api/events/ingest", json={}, headers={"X-API-Key": "invalid-key"})
    assert res.status_code == 401

def test_ingest_event_rate_limit(test_device_api_key):
    # Depending on test environment, creating a mock device in DB might be necessary
    # If the device exists, sending 601 requests should trigger a 429
    pass

def test_get_events_exceeds_90_days():
    # Attempting to fetch 100 days
    start = datetime.now(timezone.utc) - timedelta(days=100)
    end = datetime.now(timezone.utc)
    
    # Assuming auth is disabled or bypassed via fixture
    # res = client.get(f"/api/events?start={start.isoformat()}&end={end.isoformat()}")
    # assert res.status_code == 400
    pass
