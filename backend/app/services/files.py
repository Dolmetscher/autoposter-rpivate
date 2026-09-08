import mimetypes
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import MediaFile
from ..util import gen_file_id, today_folder, utcnow
from . import storage

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

TYPE_DIRS = {"photo": "photos", "video": "videos", "document": "documents"}


def detect_type(filename: str, content_type: str = "") -> str:
    ext = Path(filename or "").suffix.lower()
    if ext in PHOTO_EXT or content_type.startswith("image/"):
        return "photo"
    if ext in VIDEO_EXT or content_type.startswith("video/"):
        return "video"
    return "document"


async def save_upload(user_id: int, file: UploadFile, s: AsyncSession,
                      file_id_hint: str | None = None,
                      force_type: str | None = None) -> MediaFile:
    file_id = file_id_hint or gen_file_id()
    ftype = force_type or detect_type(file.filename, file.content_type or "")
    folder = today_folder()
    ext = Path(file.filename or "").suffix.lower() or mimetypes.guess_extension(file.content_type or "") or ".bin"
    rel_path = f"posts/{TYPE_DIRS[ftype]}/{folder}/{file_id}{ext}"

    max_bytes = settings.max_media_size_mb * 1024 * 1024
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(413, f"Файл больше {settings.max_media_size_mb} МБ")
        chunks.append(chunk)
    data = b"".join(chunks)
    await storage.save_file(rel_path, data)

    mf = MediaFile(
        id=file_id, user_id=user_id, type=ftype,
        name=Path(file.filename or file_id).name,
        folder=folder, file_path=rel_path, size=size,
        updated_date=str(int(utcnow().timestamp())),
    )
    s.add(mf)
    await s.flush()
    return mf


def media_out(mf: MediaFile) -> dict:
    """Ответ в форме hooppy (Photo/Video/Document)."""
    base = {
        "id": mf.id,
        "name": mf.name,
        "folder": mf.folder,
        "file_path": mf.file_path,
        "size": mf.size,
    }
    if mf.type == "photo":
        base.update({"type": "photo", "text": mf.text, "updated_date": mf.updated_date})
    elif mf.type == "video":
        base.update({"type": "video", "title": mf.title, "description": mf.description,
                     "file_thumbnail_path": mf.file_thumbnail_path, "seconds": mf.seconds})
    else:
        base.update({"type": "document"})
    return base


async def download_from_url(user_id: int, url: str, kind: str, s: AsyncSession) -> MediaFile:
    """Скачивание медиа из чужих постов (кросспостинг) в хранилище."""
    import hashlib

    import httpx

    ext = Path(httpx.URL(url).path).suffix.lower()
    if kind == "photo" and ext not in PHOTO_EXT:
        ext = ".jpg"
    if kind == "video" and ext not in VIDEO_EXT:
        ext = ".mp4"
    if kind == "document":
        ext = ext or ".bin"

    folder = today_folder()
    digest = hashlib.md5(url.encode()).hexdigest()[:12]
    rel_path = f"posts/{TYPE_DIRS[kind]}/{folder}/copied_{digest}{ext}"

    if await storage.file_exists(rel_path):
        existing = (await s.execute(
            MediaFile.__table__.select().where(MediaFile.file_path == rel_path))).first()
        if existing:
            return await s.get(MediaFile, existing.id)

    max_bytes = settings.crosspost_max_media_mb * 1024 * 1024
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes(1024 * 256):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError(
                            f"Медиа из источника больше {settings.crosspost_max_media_mb} МБ")
                    chunks.append(chunk)
    except httpx.HTTPError as e:
        raise ValueError(f"Не удалось скачать медиа: {e}") from e
    data = b"".join(chunks)
    await storage.save_file(rel_path, data)

    mf = MediaFile(
        id=gen_file_id(), user_id=user_id, type=kind,
        name=Path(rel_path).name, folder=folder, file_path=rel_path, size=size,
        external_url=url, updated_date=str(int(utcnow().timestamp())),
    )
    s.add(mf)
    await s.flush()
    return mf
