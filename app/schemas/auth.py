"""Authentication request/response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from app.core.security import validate_password_strength
from app.schemas.base import APIModel


class RegisterRequest(APIModel):
    """Payload for account registration."""

    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)

    @field_validator("password")
    @classmethod
    def check_password_strength(cls, v: str) -> str:
        """Validate password meets strength requirements."""
        validate_password_strength(v)
        return v


class LoginRequest(APIModel):
    """Payload for user login."""

    email: EmailStr = Field(max_length=255)
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
    refresh_token: str
    token_type: str = "bearer"
