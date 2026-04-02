"""Authentication request/response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.base import APIModel


class RegisterRequest(APIModel):
    """Payload for account registration."""

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)


class LoginRequest(APIModel):
    """Payload for user login."""

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(APIModel):
    """Payload for refreshing access token."""

    refresh_token: str = Field(min_length=1)


class LogoutRequest(APIModel):
    """Payload for logout."""

    refresh_token: str = Field(min_length=1)


class UserResponse(APIModel):
    """Serialized user profile."""

    id: UUID
    email: str
    first_name: str
    last_name: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TokenResponse(APIModel):
    """Token response for register/login."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class RefreshResponse(APIModel):
    """Token response for refresh endpoint."""

    access_token: str
    token_type: str = "bearer"
