"""Очистка хранилища: MediaFile, не привязанные ни к постам, ни к шаблонам,
старше media_retention_days. Раз в сутки из тика планировщика.
На 10 ГБ диске несущественно — держим лимиты и один проход по метаданным."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AppState, MediaFile, PostAttachment, PostTemplate
from ..util import utcnow
from . import storage

log = logging.getLogger("cleanup")

_CLEANUP_INTERVAL_HOURS = 20
_BATCH = 5000


async def cleanup_if_due(s: AsyncSession, now=None) -> int:
    """Гейт частоты: реальная очистка не чаще раза в сутки."""
    now = now or utcnow()
    if settings.media_retention_days <= 0:
        return 0
    state = await s.get(AppState, "cleanup_run")
    if state and state.value.get("ts"):
        try:
            if now - datetime.fromisoformat(state.value["ts"]) < timedelta(
                    hours=_CLEANUP_INTERVAL_HOURS):
                return 0
        except ValueError:
            pass
    count = await run_cleanup(s, now)
    if state is not None:
        state.value = {"ts": now.isoformat()}
        state.updated_at = now
    else:
        s.add(AppState(key="cleanup_run", value={"ts": now.isoformat()}, updated_at=now))
    await s.flush()
    return count


async def run_cleanup(s: AsyncSession, now=None) -> int:
    """Удаляет неприкреплённые к постам/шаблонам MediaFile старше N дней."""
    now = now or utcnow()
    cutoff_ts = int((now - timedelta(days=settings.media_retention_days)).timestamp())

    attached: set[str] = set((await s.execute(
        select(PostAttachment.media_file_id))).scalars().all())
    for ids in (await s.execute(select(PostTemplate.media_ids))).scalars().all():
        attached.update(ids or [])

    rows = (await s.execute(
        select(MediaFile).order_by(MediaFile.updated_date).limit(_BATCH))).scalars().all()
    deleted = 0
    for mf in rows:
        if mf.id in attached:
            continue
        try:
            age_ok = int(mf.updated_date) < cutoff_ts
        except (TypeError, ValueError):
            age_ok = False  # неизвестен возраст — не трогаем
        if not age_ok:
            continue
        try:
            await storage.delete_file(mf.file_path)
        except OSError:
            log.warning("Не удалось удалить файл %s", mf.file_path)
        await s.delete(mf)
        deleted += 1
    await s.flush()
    if deleted:
        log.info("Очистка хранилища: удалено %s медиафайлов старше %s дн.",
                 deleted, settings.media_retention_days)
    return deleted
