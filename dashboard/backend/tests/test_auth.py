import pytest
from fastapi.testclient import TestClient
from app.models import User

def test_login_success(client: TestClient, admin_user: User):
    response = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "test_password"
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

def test_login_failure_and_lockout(client: TestClient, admin_user: User):
    # Try 5 times with wrong password
    for _ in range(5):
        response = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "wrong_password"
        })
        assert response.status_code in [401, 423]
    
    # The 6th try should be locked
    response = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "wrong_password"
    })
    assert response.status_code == 423
    assert response.json()["detail"]["error"] == "ACCOUNT_LOCKED"

def test_get_me(client: TestClient, admin_user: User):
    # Login first
    login_resp = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "test_password"
    })
    token = login_resp.json()["access_token"]
    
    # Get me
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "admin"
    assert data["display_name"] == "Test Admin"

def test_logout(client: TestClient, admin_user: User):
    login_resp = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "test_password"
    })
    token = login_resp.json()["access_token"]
    
    # Logout
    logout_resp = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout_resp.status_code == 204
    
    # Try get me again -> should fail
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Token has been revoked"
