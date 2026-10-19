from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...deps import get_current_user
from ...models import MediaFile, User
from ...schemas import MediaOut
from ...services import files as files_service
from ...services import storage
from ...util import utcnow

router = APIRouter(prefix="/files", tags=["files"])


@router.post("/media/upload")
async def upload_media(file: UploadFile = File(...), file_id: str | None = Form(None),
                       user: User = Depends(get_current_user),
                       s: AsyncSession = Depends(get_session)):
    mf = await files_service.save_upload(user.id, file, s, file_id_hint=file_id)
    await s.commit()
    return files_service.media_out(mf)


@router.post("/documents/upload")
async def upload_document(file: UploadFile = File(...), file_id: str | None = Form(None),
                          user: User = Depends(get_current_user),
                          s: AsyncSession = Depends(get_session)):
    mf = await files_service.save_upload(user.id, file, s, file_id_hint=file_id,
                                         force_type="document")
    await s.commit()
    return files_service.media_out(mf)


@router.get("")
async def list_files(type: str | None = None, page: int = 1,
                     user: User = Depends(get_current_user),
                     s: AsyncSession = Depends(get_session)):
    q = select(MediaFile).where(MediaFile.user_id == user.id)
    count_q = select(func.count(MediaFile.id)).where(MediaFile.user_id == user.id)
    if type:
        q = q.where(MediaFile.type == type)
        count_q = count_q.where(MediaFile.type == type)
    total = (await s.execute(count_q)).scalar_one()
    rows = (await s.execute(q.order_by(MediaFile.id.desc())
                            .offset((page - 1) * 40).limit(40))).scalars().all()
    return {"list": [files_service.media_out(m) for m in rows],
            "total_rows": total, "rows_limit": 40}


class MediaUpdateIn(BaseModel):
    text: str | None = None
    title: str | None = None
    description: str | None = None


class TranscodeIn(BaseModel):
    mode: str = "vertical"  # vertical | square | compress


@router.put("/{file_id}")
async def update_file(file_id: str, payload: MediaUpdateIn,
                      user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    mf = await s.get(MediaFile, file_id)
    if not mf or mf.user_id != user.id:
        raise HTTPException(404, "Файл не найден")
    if payload.text is not None:
        mf.text = payload.text
    if payload.title is not None:
        mf.title = payload.title
    if payload.description is not None:
        mf.description = payload.description
    mf.updated_date = str(int(utcnow().timestamp()))
    await s.commit()
    return files_service.media_out(mf)


@router.delete("/{file_id}")
async def delete_file(file_id: str, user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    mf = await s.get(MediaFile, file_id)
    if not mf or mf.user_id != user.id:
        raise HTTPException(404, "Файл не найден")
    await s.delete(mf)
    await s.commit()
    try:
        await storage.delete_file(mf.file_path)
    except OSError:
        pass
    return {"success": True}


@router.post("/{file_id}/transcode")
async def transcode_file(file_id: str, payload: TranscodeIn,
                         user: User = Depends(get_current_user),
                         s: AsyncSession = Depends(get_session)):
    """Создаёт копию видео в выбранном формате (9:16 / 1:1 / сжатие). Требует ffmpeg."""
    from ...config import settings
    if not settings.transcode_enabled:
        raise HTTPException(503, "Транскодинг отключён на этом сервере "
                                 "(TRANSCODE_ENABLED=false) — готовьте видео заранее "
                                 "или включите настройку")
    mf = await s.get(MediaFile, file_id)
    if not mf or mf.user_id != user.id:
        raise HTTPException(404, "Файл не найден")
    if mf.type != "video":
        raise HTTPException(400, "Транскодинг доступен только для видео")
    from ...services import transcode as transcode_service
    from ...util import gen_file_id, today_folder
    try:
        data, suffix = await transcode_service.transcode_video(
            await storage.read_file(mf.file_path), mf.name, payload.mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    rel = f"posts/videos/{today_folder()}/{gen_file_id()}_{suffix}.mp4"
    await storage.save_file(rel, data)
    new = MediaFile(
        id=gen_file_id(), user_id=user.id, type="video",
        name=f"{Path(mf.name).stem}_{suffix}.mp4",
        folder=today_folder(), file_path=rel, size=len(data),
        seconds=mf.seconds, updated_date=str(int(utcnow().timestamp())),
    )
    s.add(new)
    await s.commit()
    return files_service.media_out(new)


@router.get("/{file_id}/raw")
async def raw_file(file_id: str, user: User = Depends(get_current_user),
                   s: AsyncSession = Depends(get_session)):
    mf = await s.get(MediaFile, file_id)
    if not mf or mf.user_id != user.id:
        raise HTTPException(404, "Файл не найден")
    if not await storage.file_exists(mf.file_path):
        raise HTTPException(404, "Файл отсутствует в хранилище")
    media_type = "image/png" if mf.file_path.endswith(".png") else \
        "image/jpeg" if mf.file_path.endswith((".jpg", ".jpeg")) else \
        "video/mp4" if mf.file_path.endswith(".mp4") else "application/octet-stream"
    # отдаём потоком: видео не должно целиком попадать в память процесса
    return StreamingResponse(storage.iter_file(mf.file_path), media_type=media_type)
