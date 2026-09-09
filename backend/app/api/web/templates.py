"""Шаблоны постов: сохранение текущего вида поста (тексты/медиа/настройки)
и быстрое применение в редакторе."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...deps import get_current_user
from ...models import MediaFile, PostTemplate, User
from ...util import utcnow

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateIn(BaseModel):
    name: str
    texts: list[dict] = []
    media_ids: list[str] = []
    settings: dict = {}


def _out(t: PostTemplate) -> dict:
    return {"id": t.id, "name": t.name, "texts": t.texts,
            "media_ids": t.media_ids, "settings": t.settings,
            "created_at": t.created_at.isoformat()}


@router.get("")
async def list_templates(user: User = Depends(get_current_user),
                         s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(PostTemplate).where(PostTemplate.user_id == user.id)
                            .order_by(PostTemplate.id.desc()))).scalars().all()
    return {"list": [_out(t) for t in rows]}


@router.post("", status_code=201)
async def create_template(payload: TemplateIn, user: User = Depends(get_current_user),
                          s: AsyncSession = Depends(get_session)):
    if not payload.name.strip():
        raise HTTPException(400, "Укажите название шаблона")
    # медиа должны принадлежать пользователю
    if payload.media_ids:
        found = (await s.execute(select(MediaFile.id).where(
            MediaFile.user_id == user.id, MediaFile.id.in_(payload.media_ids)))).scalars().all()
        missing = [m for m in payload.media_ids if m not in found]
        if missing:
            raise HTTPException(400, f"Файлы не найдены: {missing}")
    t = PostTemplate(user_id=user.id, name=payload.name.strip()[:255],
                     texts=payload.texts, media_ids=payload.media_ids,
                     settings=payload.settings)
    s.add(t)
    await s.commit()
    await s.refresh(t)
    return _out(t)


@router.delete("/{template_id}")
async def delete_template(template_id: int, user: User = Depends(get_current_user),
                          s: AsyncSession = Depends(get_session)):
    t = (await s.execute(select(PostTemplate).where(
        PostTemplate.id == template_id, PostTemplate.user_id == user.id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Шаблон не найден")
    await s.delete(t)
    await s.commit()
    return {"success": True}
