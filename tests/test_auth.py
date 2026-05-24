import pytest

from tests.conftest import register_user, unique_email


@pytest.mark.asyncio
async def test_register_success(client):
    email = unique_email()
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPass1",
            "first_name": "John",
            "last_name": "Doe",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == email.lower()
    assert data["user"]["first_name"] == "John"
    assert data["user"]["role"] == "teacher"


@pytest.mark.asyncio
async def test_register_duplicate_email(client):
    email = unique_email()
    await register_user(client, email)
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPass1",
            "first_name": "A",
            "last_name": "B",
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_register_weak_password(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": unique_email(),
            "password": "weak",
            "first_name": "A",
            "last_name": "B",
        },
    )
    assert resp.status_code == 422  # Pydantic min_length validation


@pytest.mark.asyncio
async def test_login_success(client):
    email = unique_email()
    await register_user(client, email)
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "TestPass1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["user"]["email"] == email.lower()


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    email = unique_email()
    await register_user(client, email)
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "WrongPass1"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me(client):
    data = await register_user(client)
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert resp.status_code == 200
    user = resp.json()
    assert user["email"] == data["user"]["email"]
    assert "id" in user


@pytest.mark.asyncio
async def test_me_no_token(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token(client):
    data = await register_user(client)
    resp = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": data["refresh_token"]},
    )
    assert resp.status_code == 200
    new_data = resp.json()
    assert "access_token" in new_data
    assert "refresh_token" in new_data
    # Old refresh token should be revoked (rotation)
    resp2 = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": data["refresh_token"]},
    )
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_logout(client):
    data = await register_user(client)
    resp = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": data["refresh_token"]},
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert resp.status_code == 204
    # Access token should be blacklisted
    resp2 = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_update_profile(client):
    data = await register_user(client)
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    resp = await client.put(
        "/api/v1/auth/me",
        json={"first_name": "Updated", "last_name": "Name"},
        headers=headers,
    )
    assert resp.status_code == 200
    user = resp.json()
    assert user["first_name"] == "Updated"
    assert user["last_name"] == "Name"


@pytest.mark.asyncio
async def test_update_profile_email(client):
    data = await register_user(client)
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    new_email = unique_email()
    resp = await client.put(
        "/api/v1/auth/me",
        json={"email": new_email},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == new_email.lower()


@pytest.mark.asyncio
async def test_update_profile_duplicate_email(client):
    data1 = await register_user(client)
    data2 = await register_user(client)
    headers = {"Authorization": f"Bearer {data1['access_token']}"}
    resp = await client.put(
        "/api/v1/auth/me",
        json={"email": data2["user"]["email"]},
        headers=headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_send_reset_code(client):
    email = unique_email()
    await register_user(client, email)
    resp = await client.post(
        "/api/v1/auth/send-reset-code",
        json={"email": email},
    )
    assert resp.status_code == 200
    assert "message" in resp.json()


@pytest.mark.asyncio
async def test_send_reset_code_unknown_email(client):
    # Should not reveal whether email exists
    resp = await client.post(
        "/api/v1/auth/send-reset-code",
        json={"email": "nobody@example.com"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_reset_password_full_flow(client, db_session):
    email = unique_email()
    await register_user(client, email)

    # Send code
    await client.post("/api/v1/auth/send-reset-code", json={"email": email})

    # Retrieve code from DB directly
    from sqlalchemy import select
    from app.models.user import PasswordResetCode, User

    user_result = await db_session.execute(select(User).where(User.email == email.lower()))
    user = user_result.scalar_one()
    code_result = await db_session.execute(
        select(PasswordResetCode)
        .where(PasswordResetCode.user_id == user.id)
        .order_by(PasswordResetCode.created_at.desc())
    )
    code = code_result.scalar_one().code

    # Verify code
    resp = await client.post(
        "/api/v1/auth/verify-reset-code",
        json={"email": email, "code": code},
    )
    assert resp.status_code == 200

    # Reset password
    resp = await client.post(
        "/api/v1/auth/reset-password",
        json={"email": email, "code": code, "new_password": "NewStrong1"},
    )
    assert resp.status_code == 200

    # Login with new password
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "NewStrong1"},
    )
    assert resp.status_code == 200
