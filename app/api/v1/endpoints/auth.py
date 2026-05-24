from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
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
from app.services.auth import AuthService
from app.services.base import AuthServiceError

router = APIRouter(prefix="/auth", tags=["auth"])


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    return authorization[7:]


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await AuthService.register(db, payload)
    except AuthServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await AuthService.login(db, payload)
    except AuthServiceError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e


@router.get("/me", response_model=UserResponse)
async def me(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    token = _extract_bearer(authorization)
    try:
        user = await AuthService.get_current_user(db, token)
        return UserResponse.model_validate(user)
    except AuthServiceError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await AuthService.refresh(db, payload)
    except AuthServiceError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e


@router.post("/logout", status_code=204)
async def logout(
    payload: LogoutRequest,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    token = _extract_bearer(authorization)
    try:
        await AuthService.logout(db, token, payload)
    except AuthServiceError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e


@router.put("/me", response_model=UserResponse)
async def update_profile(
    payload: UpdateProfileRequest,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    token = _extract_bearer(authorization)
    try:
        user = await AuthService.get_current_user(db, token)
        return await AuthService.update_profile(db, user, payload)
    except AuthServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/send-reset-code")
async def send_reset_code(
    payload: SendResetCodeRequest,
    db: AsyncSession = Depends(get_db),
):
    message = await AuthService.send_reset_code(db, payload)
    return {"message": message}


@router.post("/verify-reset-code")
async def verify_reset_code(
    payload: VerifyResetCodeRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        await AuthService.verify_reset_code(db, payload)
        return {"message": "Code is valid"}
    except AuthServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        await AuthService.reset_password(db, payload)
        return {"message": "Password has been reset successfully"}
    except AuthServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
