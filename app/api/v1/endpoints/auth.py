"""Authentication endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth import AuthService, AuthServiceError

router = APIRouter()
db_dependency = Annotated[AsyncSession, Depends(get_db)]


def extract_bearer_token(authorization: str | None) -> str:
    """Extract bearer token from Authorization header."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    parts = authorization.split(" ", maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format",
        )

    return parts[1].strip()


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: db_dependency) -> TokenResponse:
    """Register account and return token pair."""
    try:
        return await AuthService.register(db, payload)
    except AuthServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: db_dependency) -> TokenResponse:
    """Login and return token pair."""
    try:
        return await AuthService.login(db, payload)
    except AuthServiceError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.get("/me", response_model=UserResponse)
async def me(
    db: db_dependency,
    authorization: str | None = Header(default=None),
) -> UserResponse:
    """Return current user profile from access token."""
    access_token = extract_bearer_token(authorization)
    try:
        user = await AuthService.get_current_user(db, access_token)
        return UserResponse.model_validate(user)
    except AuthServiceError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(payload: RefreshRequest, db: db_dependency) -> RefreshResponse:
    """Refresh access token using refresh token body payload."""
    try:
        return await AuthService.refresh(db, payload)
    except AuthServiceError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest,
    db: db_dependency,
    authorization: str | None = Header(default=None),
) -> None:
    """Invalidate refresh token and blacklist current access token."""
    access_token = extract_bearer_token(authorization)
    try:
        await AuthService.logout(db, access_token, payload)
    except AuthServiceError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
