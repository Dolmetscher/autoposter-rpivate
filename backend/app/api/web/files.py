from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db import get_session
from ...deps import get_current_user
from ...models import MediaFile, User
from ...schemas import MediaOut
from ...services import files as files_service
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
    path = files_service.storage_path(mf.file_path)
    await s.delete(mf)
    await s.commit()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return {"success": True}


@router.get("/{file_id}/raw")
async def raw_file(file_id: str, user: User = Depends(get_current_user),
                   s: AsyncSession = Depends(get_session)):
    mf = await s.get(MediaFile, file_id)
    if not mf or mf.user_id != user.id:
        raise HTTPException(404, "Файл не найден")
    path = files_service.storage_path(mf.file_path)
    if not path.exists():
        raise HTTPException(404, "Файл отсутствует на диске")
    return FileResponse(path, filename=mf.name)
