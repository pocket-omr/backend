import asyncio
import io
import os
import uuid
from datetime import timedelta
from functools import lru_cache

from minio import Minio

from app.core.config import settings


@lru_cache(maxsize=1)
def _client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _ensure_bucket(client: Minio) -> None:
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)


def build_key(exam_id: uuid.UUID | str, filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower() or ".jpg"
    return f"exams/{exam_id}/{uuid.uuid4().hex}{ext}"


def _put_sync(key: str, data: bytes, content_type: str) -> str:
    client = _client()
    _ensure_bucket(client)
    client.put_object(
        settings.minio_bucket,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return key


def _get_sync(key: str) -> bytes:
    resp = _client().get_object(settings.minio_bucket, key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


def _presigned_sync(key: str, expires_seconds: int) -> str:
    return _client().presigned_get_object(
        settings.minio_bucket, key, expires=timedelta(seconds=expires_seconds)
    )


async def put_object(
    key: str, data: bytes, content_type: str = "application/octet-stream"
) -> str:
    """Store bytes under `key` in the configured bucket; returns the key."""
    return await asyncio.to_thread(_put_sync, key, data, content_type)


async def get_object(key: str) -> bytes:
    """Fetch the object bytes stored under `key`."""
    return await asyncio.to_thread(_get_sync, key)


async def presigned_url(key: str, expires_seconds: int = 3600) -> str:
    """Return a time-limited GET URL for `key`."""
    return await asyncio.to_thread(_presigned_sync, key, expires_seconds)
