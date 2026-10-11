"""Хранилище медиафайлов: local (диск) или S3-совместимое (MinIO, Yandex Object Storage).
Переключается переменной MEDIA_STORAGE. Все потребители (загрузка файлов, кросспостинг,
адаптеры) работают только через этот модуль — путь хранения им не важен."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from ..config import settings

_s3 = None


def is_s3() -> bool:
    return settings.media_storage == "s3" and settings.s3_bucket


def _client():
    global _s3
    if _s3 is None:
        import boto3

        _s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key or None,
            aws_secret_access_key=settings.s3_secret_key or None,
            region_name=settings.s3_region or "us-east-1",
        )
    return _s3


def local_full(rel_path: str) -> Path:
    """Полный путь на диске (только режим local). Защищает от выход за пределы каталога."""
    root = Path(settings.storage_dir).resolve()
    p = (root / rel_path).resolve()
    if not str(p).startswith(str(root)):
        raise ValueError("Некорректный путь файла")
    return p


async def save_file(rel_path: str, data: bytes) -> None:
    if is_s3():
        await asyncio.to_thread(
            lambda: _client().put_object(Bucket=settings.s3_bucket, Key=rel_path, Body=data)
        )
        return
    p = local_full(rel_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(p.write_bytes, data)


async def save_stream(rel_path: str, src_path: str | Path) -> None:
    """Помещает готовый временный файл в хранилище, не читая его целиком в RAM.
    Исходный файл перемещается (local) или выгружается потоком (S3) и удаляется."""
    src = Path(src_path)
    if is_s3():
        def up() -> None:
            with open(src, "rb") as f:
                _client().upload_fileobj(f, settings.s3_bucket, rel_path)
        try:
            await asyncio.to_thread(up)
        finally:
            await asyncio.to_thread(src.unlink, missing_ok=True)
        return
    p = local_full(rel_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.move, str(src), str(p))


async def read_file(rel_path: str) -> bytes:
    if is_s3():
        obj = await asyncio.to_thread(
            _client().get_object, Bucket=settings.s3_bucket, Key=rel_path
        )
        return await asyncio.to_thread(obj["Body"].read)
    return await asyncio.to_thread(local_full(rel_path).read_bytes)


async def file_exists(rel_path: str) -> bool:
    if is_s3():
        def check() -> bool:
            try:
                _client().head_object(Bucket=settings.s3_bucket, Key=rel_path)
                return True
            except Exception:
                return False
        return await asyncio.to_thread(check)
    return local_full(rel_path).exists()


async def delete_file(rel_path: str) -> None:
    if is_s3():
        await asyncio.to_thread(
            lambda: _client().delete_object(Bucket=settings.s3_bucket, Key=rel_path)
        )
        return
    local_full(rel_path).unlink(missing_ok=True)


def public_url(rel_path: str) -> str:
    """Публичная ссылка на файл для сетей, качающих медиа по URL (Instagram, TikTok)."""
    if is_s3():
        if settings.s3_public_base:
            base = settings.s3_public_base.rstrip("/")
            if settings.s3_public_style == "virtual":
                return f"{base}/{rel_path}"
            return f"{base}/{settings.s3_bucket}/{rel_path}"
        return _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": rel_path},
            ExpiresIn=3600,
        )
    return f"{settings.public_base_url.rstrip('/')}/media/{rel_path.lstrip('/')}"
