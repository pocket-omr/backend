"""Authentication service logic."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    validate_password_strength,
    verify_password,
)
from app.models.user import RefreshToken, TokenBlacklist, User, UserRole
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.base import ServiceError


class AuthServiceError(ServiceError):
    """Auth service domain error."""


class AuthService:
    """Handles registration, login, token refresh, logout, and user resolution."""

    @staticmethod
    async def register(db: AsyncSession, payload: RegisterRequest) -> TokenResponse:
        """Create user (teacher role only) and issue access/refresh tokens."""
        email = payload.email.strip().lower()
        validate_password_strength(payload.password)

        existing_stmt = select(User).where(func.lower(User.email) == email)
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            raise AuthServiceError("Email is already registered")

        user = User(
            email=email,
            hashed_password=hash_password(payload.password),
            first_name=payload.first_name.strip(),
            last_name=payload.last_name.strip(),
            role=UserRole.TEACHER,
            is_active=True,
        )
        db.add(user)
        await db.flush()

        access_token = create_access_token(subject=str(user.id), email=user.email)
        refresh_token = create_refresh_token(subject=str(user.id), email=user.email)
        await AuthService._store_refresh_token(db, user.id, refresh_token)

        await db.commit()
        await db.refresh(user)

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=UserResponse.model_validate(user),
        )

    @staticmethod
    async def login(db: AsyncSession, payload: LoginRequest) -> TokenResponse:
        """Validate credentials and issue access/refresh tokens."""
        email = payload.email.strip().lower()

        stmt = select(User).where(func.lower(User.email) == email)
        user = (await db.execute(stmt)).scalar_one_or_none()
        if user is None or not verify_password(payload.password, user.hashed_password):
            raise AuthServiceError("Invalid email or password")

        if not user.is_active:
            raise AuthServiceError("User account is inactive")

        access_token = create_access_token(subject=str(user.id), email=user.email)
        refresh_token = create_refresh_token(subject=str(user.id), email=user.email)
        await AuthService._store_refresh_token(db, user.id, refresh_token)

        await db.commit()

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=UserResponse.model_validate(user),
        )

    @staticmethod
    async def get_current_user(db: AsyncSession, access_token: str) -> User:
        """Resolve active user from access token while enforcing blacklist."""
        claims = AuthService._decode_and_validate_token(access_token, token_type="access")
        jti = claims["jti"]

        blacklist_stmt = select(TokenBlacklist).where(TokenBlacklist.jti == jti)
        blacklisted = (await db.execute(blacklist_stmt)).scalar_one_or_none()
        if blacklisted is not None:
            raise AuthServiceError("Token has been revoked")

        user = await AuthService._get_active_user_from_sub(db, claims["sub"])
        return user

    @staticmethod
    async def refresh(db: AsyncSession, payload: RefreshRequest) -> RefreshResponse:
        """Issue a new access token from a valid stored refresh token."""
        claims = AuthService._decode_and_validate_token(payload.refresh_token, token_type="refresh")

        token_hash_value = hash_token(payload.refresh_token)
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash_value)
        refresh_row = (await db.execute(stmt)).scalar_one_or_none()
        if refresh_row is None:
            raise AuthServiceError("Refresh token is invalid or revoked")

        if refresh_row.expires_at <= datetime.now(timezone.utc):
            await db.execute(delete(RefreshToken).where(RefreshToken.id == refresh_row.id))
            await db.commit()
            raise AuthServiceError("Refresh token has expired")

        user = await AuthService._get_active_user_from_sub(db, claims["sub"])
        access_token = create_access_token(subject=str(user.id), email=user.email)
        return RefreshResponse(access_token=access_token)

    @staticmethod
    async def logout(db: AsyncSession, access_token: str, payload: LogoutRequest) -> None:
        """Invalidate refresh token and blacklist current access token jti."""
        access_claims = AuthService._decode_and_validate_token(access_token, token_type="access")
        AuthService._decode_and_validate_token(payload.refresh_token, token_type="refresh")

        refresh_hash = hash_token(payload.refresh_token)
        await db.execute(delete(RefreshToken).where(RefreshToken.token_hash == refresh_hash))

        exp_ts = int(access_claims["exp"])
        expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc)

        blacklist_row = TokenBlacklist(jti=access_claims["jti"], expires_at=expires_at)
        db.add(blacklist_row)

        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()

    @staticmethod
    async def _store_refresh_token(db: AsyncSession, user_id: UUID, refresh_token: str) -> None:
        claims = AuthService._decode_and_validate_token(refresh_token, token_type="refresh")
        exp_ts = int(claims["exp"])
        expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc)

        db.add(
            RefreshToken(
                user_id=user_id,
                token_hash=hash_token(refresh_token),
                expires_at=expires_at,
            )
        )

    @staticmethod
    async def _get_active_user_from_sub(db: AsyncSession, sub_claim: str) -> User:
        try:
            user_id = UUID(sub_claim)
        except ValueError as exc:
            raise AuthServiceError("Invalid token subject") from exc

        stmt = select(User).where(User.id == user_id)
        user = (await db.execute(stmt)).scalar_one_or_none()
        if user is None:
            raise AuthServiceError("User not found")
        if not user.is_active:
            raise AuthServiceError("User account is inactive")
        return user

    @staticmethod
    def _decode_and_validate_token(token: str, token_type: str) -> dict[str, str | int]:
        try:
            claims = decode_token(token)
        except ValueError as exc:
            raise AuthServiceError("Invalid or expired token") from exc

        if claims.get("type") != token_type:
            raise AuthServiceError(f"Invalid token type, expected {token_type}")

        sub = claims.get("sub")
        jti = claims.get("jti")
        exp = claims.get("exp")
        if not isinstance(sub, str) or not isinstance(jti, str) or exp is None:
            raise AuthServiceError("Token missing required claims")

        return {"sub": sub, "jti": jti, "exp": int(exp)}
