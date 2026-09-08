from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...deps import get_current_user
from ...integrations.base import AdapterError
from ...models import CrosspostSeen, CrosspostSource, User
from ...schemas import CrosspostSourceIn
from ...services import crosspost as crosspost_service
from ...util import utcnow

router = APIRouter(prefix="/crosspost", tags=["crosspost"])


def out(src: CrosspostSource) -> dict:
    return {
        "id": src.id, "source_id": src.source_id, "name": src.name,
        "source_ref": src.source_ref, "target_page_ids": src.target_page_ids,
        "rewrite_enabled": src.rewrite_enabled, "only_with_media": src.only_with_media,
        "keyword_filter": src.keyword_filter, "poll_interval_min": src.poll_interval_min,
        "active": src.active,
        "last_polled_at": src.last_polled_at.isoformat() if src.last_polled_at else None,
    }


@router.get("")
async def list_sources(user: User = Depends(get_current_user),
                       s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(CrosspostSource).where(CrosspostSource.user_id == user.id)
                            .order_by(CrosspostSource.id))).scalars().all()
    return {"list": [out(x) for x in rows]}


@router.post("", status_code=201)
async def create_source(payload: CrosspostSourceIn, user: User = Depends(get_current_user),
                        s: AsyncSession = Depends(get_session)):
    src = CrosspostSource(user_id=user.id, **payload.model_dump())
    s.add(src)
    await s.commit()
    await s.refresh(src)
    return out(src)


@router.put("/{source_id}")
async def update_source(source_id: int, payload: CrosspostSourceIn,
                        user: User = Depends(get_current_user),
                        s: AsyncSession = Depends(get_session)):
    src = (await s.execute(select(CrosspostSource).where(
        CrosspostSource.id == source_id, CrosspostSource.user_id == user.id))).scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Источник не найден")
    for k, v in payload.model_dump().items():
        setattr(src, k, v)
    await s.commit()
    return out(src)


@router.delete("/{source_id}")
async def delete_source(source_id: int, user: User = Depends(get_current_user),
                        s: AsyncSession = Depends(get_session)):
    src = (await s.execute(select(CrosspostSource).where(
        CrosspostSource.id == source_id, CrosspostSource.user_id == user.id))).scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Источник не найден")
    await s.delete(src)
    await s.commit()
    return {"success": True}


@router.post("/{source_id}/run")
async def run_source(source_id: int, user: User = Depends(get_current_user),
                     s: AsyncSession = Depends(get_session)):
    src = (await s.execute(select(CrosspostSource).where(
        CrosspostSource.id == source_id, CrosspostSource.user_id == user.id))).scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Источник не найден")
    try:
        created = await crosspost_service.poll_source(s, src)
    except AdapterError as e:
        raise HTTPException(400, str(e))
    src.last_polled_at = utcnow()
    await s.commit()
    return {"created": created}
