import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
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
from app.models.user import PasswordResetCode, RefreshToken, TokenBlacklist, User, UserRole
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    ResetPasswordRequest,
    SendResetCodeRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserResponse,
    VerifyResetCodeRequest,
)
from app.services.base import AuthServiceError


class AuthService:
    @staticmethod
    async def register(db: AsyncSession, payload: RegisterRequest) -> TokenResponse:
        try:
            validate_password_strength(payload.password)
        except ValueError as e:
            raise AuthServiceError(str(e)) from e

        email = payload.email.strip().lower()
        existing = await db.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none():
            raise AuthServiceError("Email already registered")

        user = User(
            email=email,
            hashed_password=hash_password(payload.password),
            first_name=payload.first_name.strip(),
            last_name=payload.last_name.strip(),
            role=UserRole.teacher,
        )
        db.add(user)
        await db.flush()

        access_token = create_access_token(str(user.id), user.email)
        refresh_token = create_refresh_token(str(user.id), user.email)
        await AuthService._store_refresh_token(db, user.id, refresh_token)
        await db.commit()

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=UserResponse.model_validate(user),
        )

    @staticmethod
    async def login(db: AsyncSession, payload: LoginRequest) -> TokenResponse:
        email = payload.email.strip().lower()
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user or not verify_password(payload.password, user.hashed_password):
            raise AuthServiceError("Invalid email or password")
        if not user.is_active:
            raise AuthServiceError("Account is inactive")

        access_token = create_access_token(str(user.id), user.email)
        refresh_token = create_refresh_token(str(user.id), user.email)
        await AuthService._store_refresh_token(db, user.id, refresh_token)
        await db.commit()

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=UserResponse.model_validate(user),
        )

    @staticmethod
    async def get_current_user(db: AsyncSession, access_token: str) -> User:
        claims = AuthService._decode_and_validate_token(access_token, "access")

        # Check blacklist
        result = await db.execute(
            select(TokenBlacklist).where(TokenBlacklist.jti == claims["jti"])
        )
        if result.scalar_one_or_none():
            raise AuthServiceError("Token has been revoked")

        return await AuthService._get_active_user_from_sub(db, claims["sub"])

    @staticmethod
    async def refresh(db: AsyncSession, payload: RefreshRequest) -> RefreshResponse:
        claims = AuthService._decode_and_validate_token(payload.refresh_token, "refresh")
        token_hash = hash_token(payload.refresh_token)

        result = await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        stored = result.scalar_one_or_none()
        if not stored:
            raise AuthServiceError("Refresh token not found or already revoked")

        if stored.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
            await db.delete(stored)
            await db.commit()
            raise AuthServiceError("Refresh token expired")

        user = await AuthService._get_active_user_from_sub(db, claims["sub"])

        # Token rotation
        await db.delete(stored)

        new_access = create_access_token(str(user.id), user.email)
        new_refresh = create_refresh_token(str(user.id), user.email)
        await AuthService._store_refresh_token(db, user.id, new_refresh)
        await db.commit()

        return RefreshResponse(access_token=new_access, refresh_token=new_refresh)

    @staticmethod
    async def logout(
        db: AsyncSession, access_token: str, payload: LogoutRequest
    ) -> None:
        access_claims = AuthService._decode_and_validate_token(access_token, "access")
        refresh_claims = AuthService._decode_and_validate_token(payload.refresh_token, "refresh")

        # Delete refresh token
        token_hash = hash_token(payload.refresh_token)
        result = await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        stored = result.scalar_one_or_none()
        if stored:
            await db.delete(stored)

        # Blacklist access token
        exp = datetime.fromtimestamp(access_claims["exp"], tz=UTC)
        blacklist_entry = TokenBlacklist(jti=access_claims["jti"], expires_at=exp)
        try:
            db.add(blacklist_entry)
            await db.commit()
        except IntegrityError:
            await db.rollback()  # Already blacklisted, idempotent

        # Suppress unused variable warning — we validate refresh claims but
        # only need the access token's jti for blacklisting.
        _ = refresh_claims

    @staticmethod
    async def update_profile(
        db: AsyncSession, user: User, payload: UpdateProfileRequest
    ) -> UserResponse:
        if payload.first_name is not None:
            user.first_name = payload.first_name.strip()
        if payload.last_name is not None:
            user.last_name = payload.last_name.strip()
        if payload.email is not None:
            new_email = payload.email.strip().lower()
            if new_email != user.email:
                existing = await db.execute(select(User).where(User.email == new_email))
                if existing.scalar_one_or_none():
                    raise AuthServiceError("Email already in use")
                user.email = new_email
        await db.commit()
        await db.refresh(user)
        return UserResponse.model_validate(user)

    @staticmethod
    async def send_reset_code(db: AsyncSession, payload: SendResetCodeRequest) -> str:
        email = payload.email.strip().lower()
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if not user:
            # Don't reveal whether email exists — return silently
            return "If the email exists, a reset code has been sent."

        code = str(random.randint(1000, 9999))
        reset_entry = PasswordResetCode(
            user_id=user.id,
            code=code,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        db.add(reset_entry)
        await db.commit()

        import contextlib

        from app.services.email import send_reset_code_email

        with contextlib.suppress(Exception):
            send_reset_code_email(email, code)

        return "If the email exists, a reset code has been sent."

    @staticmethod
    async def verify_reset_code(db: AsyncSession, payload: VerifyResetCodeRequest) -> None:
        email = payload.email.strip().lower()
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if not user:
            raise AuthServiceError("Invalid email or code")

        code_result = await db.execute(
            select(PasswordResetCode)
            .where(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.code == payload.code,
                PasswordResetCode.used == False,  # noqa: E712
            )
            .order_by(PasswordResetCode.created_at.desc())
        )
        reset_code = code_result.scalar_one_or_none()
        if not reset_code:
            raise AuthServiceError("Invalid email or code")
        if reset_code.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
            raise AuthServiceError("Code has expired")

    @staticmethod
    async def reset_password(db: AsyncSession, payload: ResetPasswordRequest) -> None:
        try:
            validate_password_strength(payload.new_password)
        except ValueError as e:
            raise AuthServiceError(str(e)) from e

        email = payload.email.strip().lower()
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if not user:
            raise AuthServiceError("Invalid email or code")

        code_result = await db.execute(
            select(PasswordResetCode)
            .where(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.code == payload.code,
                PasswordResetCode.used == False,  # noqa: E712
            )
            .order_by(PasswordResetCode.created_at.desc())
        )
        reset_code = code_result.scalar_one_or_none()
        if not reset_code:
            raise AuthServiceError("Invalid email or code")
        if reset_code.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
            raise AuthServiceError("Code has expired")

        user.hashed_password = hash_password(payload.new_password)
        reset_code.used = True
        await db.commit()

    @staticmethod
    async def _store_refresh_token(
        db: AsyncSession, user_id: uuid.UUID, refresh_token: str
    ) -> None:
        claims = decode_token(refresh_token)
        exp = datetime.fromtimestamp(claims["exp"], tz=UTC)
        db.add(
            RefreshToken(
                user_id=user_id,
                token_hash=hash_token(refresh_token),
                expires_at=exp,
            )
        )

    @staticmethod
    async def _get_active_user_from_sub(db: AsyncSession, sub: str) -> User:
        try:
            user_id = uuid.UUID(sub)
        except ValueError as e:
            raise AuthServiceError("Invalid token subject") from e

        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise AuthServiceError("User not found")
        if not user.is_active:
            raise AuthServiceError("Account is inactive")
        return user

    @staticmethod
    def _decode_and_validate_token(token: str, expected_type: str) -> dict:
        try:
            claims = decode_token(token)
        except ValueError as e:
            raise AuthServiceError(str(e)) from e

        if claims.get("type") != expected_type:
            raise AuthServiceError(f"Expected {expected_type} token")

        for field in ("sub", "jti", "exp"):
            if field not in claims:
                raise AuthServiceError(f"Token missing required claim: {field}")

        return claims
