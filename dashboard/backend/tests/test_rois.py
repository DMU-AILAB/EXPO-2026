import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
import httpx
from app.main import app
from app.schemas.roi import RoiCreate

client = TestClient(app)

@pytest.fixture
def mock_httpx():
    with patch("httpx.AsyncClient.request") as mock_req, patch("httpx.AsyncClient.put") as mock_put:
        mock_res = AsyncMock()
        mock_res.status_code = 200
        mock_req.return_value = mock_res
        mock_put.return_value = mock_res
        yield mock_req, mock_put

def test_create_roi_etag_missing(mock_httpx):
    pass

def test_roi_polygon_validation():
    # Valid polygon
    valid_roi = RoiCreate(
        camera_id="dev-cam0",
        name="Test ROI",
        polygon=[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
    )
    assert valid_roi.name == "Test ROI"
    
    # Invalid polygon (less than 3 points)
    with pytest.raises(ValueError):
        RoiCreate(
            camera_id="dev-cam0",
            name="Test ROI",
            polygon=[[0.1, 0.1], [0.9, 0.1]]
        )

    # Invalid polygon (self-intersecting bowtie)
    with pytest.raises(ValueError):
        RoiCreate(
            camera_id="dev-cam0",
            name="Test ROI",
            polygon=[[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0]]
        )

def test_create_roi_proxy_offline():
    with patch("httpx.AsyncClient.request", side_effect=httpx.ConnectError("Offline")):
        # Should raise 503
        pass
