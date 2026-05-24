import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from app.schemas.base import APIModel


class RegisterRequest(APIModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)


class LoginRequest(APIModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(APIModel):
    refresh_token: str


class LogoutRequest(APIModel):
    refresh_token: str


class UserResponse(APIModel):
    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TokenResponse(APIModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class RefreshResponse(APIModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class SendResetCodeRequest(APIModel):
    email: EmailStr = Field(max_length=255)


class VerifyResetCodeRequest(APIModel):
    email: EmailStr = Field(max_length=255)
    code: str = Field(min_length=4, max_length=6)


class ResetPasswordRequest(APIModel):
    email: EmailStr = Field(max_length=255)
    code: str = Field(min_length=4, max_length=6)
    new_password: str = Field(min_length=8, max_length=128)


class UpdateProfileRequest(APIModel):
    first_name: str | None = Field(default=None, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)
