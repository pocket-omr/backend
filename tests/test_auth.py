from fastapi.testclient import TestClient
from uuid import uuid4


def unique_email(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}@example.com"


def register_user(client: TestClient, email: str | None = None) -> dict[str, object]:
    if email is None:
        email = unique_email("teacher")
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Password1",
            "first_name": "Teacher One",
            "last_name": "Teacher One",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_register_success(client: TestClient) -> None:
    email = unique_email("register_ok")
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Password1",
            "first_name": "Register",
            "last_name": "OK",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == email
    assert body["user"]["role"] == "teacher"


def test_register_duplicate_email(client: TestClient) -> None:
    email = unique_email("dup")
    payload = {
        "email": email,
        "password": "Password1",
        "first_name": "Dup",
        "last_name": "User",
    }
    first = client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201

    second = client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 400
    assert "already" in second.json()["detail"].lower()


def test_login_success(client: TestClient) -> None:
    email = unique_email("login_ok")
    register_user(client, email)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["user"]["email"] == email


def test_login_bad_credentials(client: TestClient) -> None:
    email = unique_email("bad_login")
    register_user(client, email)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "WrongPassword1"},
    )

    assert response.status_code == 401


def test_me_success(client: TestClient) -> None:
    email = unique_email("me_ok")
    registered = register_user(client, email)
    access_token = registered["access_token"]

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == email
    assert body["role"] == "teacher"


def test_me_missing_authorization_header(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_refresh_success(client: TestClient) -> None:
    registered = register_user(client, unique_email("refresh_ok"))
    refresh_token = registered["refresh_token"]

    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


def test_refresh_invalid_token(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "invalid-token"},
    )

    assert response.status_code == 401


def test_logout_success(client: TestClient) -> None:
    registered = register_user(client, unique_email("logout_ok"))
    access_token = registered["access_token"]
    refresh_token = registered["refresh_token"]

    response = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 204


def test_logout_missing_authorization_header(client: TestClient) -> None:
    registered = register_user(client, unique_email("logout_missing_header"))
    refresh_token = registered["refresh_token"]

    response = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh_token},
    )

    assert response.status_code == 401
