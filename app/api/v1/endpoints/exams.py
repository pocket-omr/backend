import uuid

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.exam import ExamCreate, ExamOut, ExamUpdate, HistoryExamOut, MobileExamOut
from app.services.auth import AuthService
from app.services.base import AuthServiceError, ServiceError
from app.services.exam import ExamService

router = APIRouter(prefix="/exams", tags=["exams"])


async def _get_current_user_id(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> tuple[uuid.UUID, AsyncSession]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    token = authorization[7:]
    try:
        user = await AuthService.get_current_user(db, token)
    except AuthServiceError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    return user.id, db


# --- Fixed-path routes MUST come before /{exam_id} to avoid UUID parsing conflicts ---


@router.post("", response_model=ExamOut, status_code=201)
async def create_exam(
    payload: ExamCreate,
    deps: tuple = Depends(_get_current_user_id),
):
    user_id, db = deps
    return await ExamService.create(db, user_id, payload)


@router.get("", response_model=list[ExamOut])
async def list_exams(deps: tuple = Depends(_get_current_user_id)):
    user_id, db = deps
    return await ExamService.list_by_user(db, user_id)


@router.get("/recent", response_model=list[MobileExamOut])
async def list_recent(deps: tuple = Depends(_get_current_user_id)):
    user_id, db = deps
    return await ExamService.list_recent(db, user_id)


@router.get("/to-correct", response_model=list[MobileExamOut])
async def list_to_correct(
    search: str | None = Query(default=None),
    deps: tuple = Depends(_get_current_user_id),
):
    user_id, db = deps
    return await ExamService.list_to_correct(db, user_id, search)


@router.get("/history", response_model=list[HistoryExamOut])
async def list_history(
    search: str | None = Query(default=None),
    deps: tuple = Depends(_get_current_user_id),
):
    user_id, db = deps
    return await ExamService.list_history(db, user_id, search)


# --- Parameterized routes ---


@router.get("/{exam_id}", response_model=ExamOut)
async def get_exam(exam_id: uuid.UUID, deps: tuple = Depends(_get_current_user_id)):
    user_id, db = deps
    try:
        return await ExamService.get(db, exam_id, user_id)
    except ServiceError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.put("/{exam_id}", response_model=ExamOut)
async def update_exam(
    exam_id: uuid.UUID,
    payload: ExamUpdate,
    deps: tuple = Depends(_get_current_user_id),
):
    user_id, db = deps
    try:
        return await ExamService.update(db, exam_id, user_id, payload)
    except ServiceError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/{exam_id}", status_code=204)
async def delete_exam(exam_id: uuid.UUID, deps: tuple = Depends(_get_current_user_id)):
    user_id, db = deps
    try:
        await ExamService.delete(db, exam_id, user_id)
    except ServiceError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{exam_id}/mobile", response_model=MobileExamOut)
async def get_exam_mobile(
    exam_id: uuid.UUID,
    deps: tuple = Depends(_get_current_user_id),
):
    user_id, db = deps
    try:
        return await ExamService.get_mobile(db, exam_id, user_id)
    except ServiceError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{exam_id}/upload-images", response_model=MobileExamOut)
async def upload_images(
    exam_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    deps: tuple = Depends(_get_current_user_id),
):
    """Upload scanned sheet images for an exam. ML processing is not yet implemented."""
    user_id, db = deps
    file_names = [f.filename or "unknown" for f in files]
    try:
        return await ExamService.upload_images(db, exam_id, user_id, file_names)
    except ServiceError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
