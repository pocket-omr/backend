"""Security utilities for password hashing and JWT handling."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plaintext password against bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


def validate_password_strength(password: str) -> None:
    """Enforce minimum password policy."""
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not any(char.isupper() for char in password):
        raise ValueError("Password must contain at least one uppercase letter")
    if not any(char.isdigit() for char in password):
        raise ValueError("Password must contain at least one digit")


def hash_token(token: str) -> str:
    """Store refresh tokens as SHA-256 hashes, never as raw values."""
    return sha256(token.encode("utf-8")).hexdigest()


def _encode_token(payload: dict[str, Any], expires_delta: timedelta) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {**payload, "exp": expire}
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str, email: str) -> str:
    """Create signed JWT access token with jti claim."""
    payload = {
        "sub": subject,
        "email": email,
        "type": "access",
        "jti": str(uuid4()),
    }
    return _encode_token(payload, timedelta(minutes=settings.access_token_expire_minutes))


def create_refresh_token(subject: str, email: str) -> str:
    """Create signed JWT refresh token with jti claim."""
    payload = {
        "sub": subject,
        "email": email,
        "type": "refresh",
        "jti": str(uuid4()),
    }
    return _encode_token(payload, timedelta(days=settings.refresh_token_expire_days))


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate JWT, raising ValueError on failure."""
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc
