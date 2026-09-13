"""Retention-память 72 часа (модуль 7 ТЗ): ежедневно в 02:00 по TZ пользователя
(FR-7.1) удаляются «оперативные» материалы старше 72 ч (FR-7.2) с каскадом
scores/alerts/checks/медиа (FR-7.3); агрегаты за день сохраняются бессрочно
(FR-7.4). Партиций нет — DELETE по индексу fetched_at (объёмы MVP)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AppState, MediaFile, User
from ..services import storage
from ..util import utcnow
from .models import (RedAlert, RedBlocklist, RedDailyStat, RedDigestItem,
                     RedFeedback, RedItem, RedScore, RedSourceCheck)

log = logging.getLogger("red.retention")


async def run_retention(s: AsyncSession, now=None) -> dict:
    """Раз в день в 02:00 по TZ каждого пользователя (тик проверяет часто)."""
    now = now or utcnow()
    total = {"users": 0, "items": 0}
    user_ids = (await s.execute(select(RedItem.user_id).distinct())).scalars().all()
    for user_id in user_ids:
        user = await s.get(User, user_id)
        if user is None:
            continue
        try:
            local = now.astimezone(ZoneInfo(user.timezone or "UTC"))
        except Exception:  # noqa: BLE001
            local = now
        if (local.hour, local.minute) < (2, 0):
            continue
        key = f"red_retention_{user.id}"
        today = local.strftime("%Y-%m-%d")
        row = await s.get(AppState, key)
        if row is not None and row.value.get("last_date") == today:
            continue
        value = {"last_date": today}
        if row is not None:
            row.value = value
            row.updated_at = now
        else:
            s.add(AppState(key=key, value=value, updated_at=now))
        stats = await cleanup_user(s, user_id, now)
        total["users"] += 1
        total["items"] += stats["deleted_items"]
        await _notify_stats(s, user_id, stats)
    if total["users"]:
        await s.flush()
    return total


async def cleanup_user(s: AsyncSession, user_id: int, now: datetime | None = None) -> dict:
    """Удаление материалов старше 72 ч без posted/digest_flag (FR-7.2–7.3)
    и предварительная аггрегация дня в red_daily_stats (FR-7.4)."""
    now = now or utcnow()
    cutoff = now - timedelta(hours=settings.red_retention_hours)

    await _aggregate_day(s, user_id, now)

    doomed = (await s.execute(
        select(RedItem).where(
            RedItem.user_id == user_id,
            RedItem.fetched_at < cutoff,
            RedItem.status != "posted",
            RedItem.digest_flag.is_(False),
        ).limit(500))).scalars().all()

    deleted_media = 0
    doomed_ids = [item.id for item in doomed]
    if doomed_ids:
        # FK-каскады не включены (SQLite) — зависимые строки удаляем явно (FR-7.3)
        for mid_row in (await s.execute(
                select(RedItem.media_ids).where(RedItem.id.in_(doomed_ids)))).all():
            for mid in (mid_row[0] or []):
                mf = await s.get(MediaFile, mid)
                if mf is not None:
                    try:
                        await storage.delete_file(mf.file_path)
                    except Exception:  # noqa: BLE001 — файл мог уже уйти
                        pass
                    await s.delete(mf)
                    deleted_media += 1
        await s.execute(RedFeedback.__table__.update()
                        .where(RedFeedback.item_id.in_(doomed_ids))
                        .values(item_id=None))  # фидбек живёт бессрочно (FR-7.4)
        await s.execute(delete(RedScore).where(RedScore.item_id.in_(doomed_ids)))
        await s.execute(delete(RedAlert).where(RedAlert.item_id.in_(doomed_ids)))
        await s.execute(delete(RedDigestItem).where(RedDigestItem.item_id.in_(doomed_ids)))
        await s.execute(delete(RedItem).where(RedItem.id.in_(doomed_ids)))

    # протухший блок-лист и история сборов старше 30 дней
    await s.execute(delete(RedBlocklist).where(
        RedBlocklist.until.is_not(None), RedBlocklist.until < now))
    await s.execute(delete(RedSourceCheck).where(
        RedSourceCheck.created_at < now - timedelta(days=30)))
    await s.flush()
    return {"deleted_items": len(doomed), "deleted_media": deleted_media}


async def _aggregate_day(s: AsyncSession, user_id: int, now: datetime) -> None:
    """Агрегаты за прошедшие сутки: материалы/алерты/посты, средний significance."""
    day_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    day = day_start.strftime("%Y-%m-%d")
    rows = (await s.execute(
        select(RedItem.source_id, func.count(func.distinct(RedItem.id)),
               func.avg(RedScore.significance))
        .outerjoin(RedScore, RedScore.item_id == RedItem.id)
        .where(RedItem.user_id == user_id, RedItem.fetched_at >= day_start)
        .group_by(RedItem.source_id))).all()
    for source_id, count, avg_sig in rows:
        alerts = int((await s.execute(
            select(func.count(RedAlert.id)).join(RedItem, RedItem.id == RedAlert.item_id)
            .where(RedAlert.user_id == user_id, RedItem.source_id == source_id,
                   RedAlert.sent_at >= day_start))).scalar() or 0)
        exists = await s.scalar(select(RedDailyStat.id).where(
            RedDailyStat.user_id == user_id, RedDailyStat.day == day,
            RedDailyStat.source_id == source_id, RedDailyStat.tag == ""))
        if exists:
            continue
        s.add(RedDailyStat(user_id=user_id, day=day, source_id=source_id, tag="",
                           items_count=int(count), alerts_count=alerts,
                           avg_significance=round(float(avg_sig), 1) if avg_sig else None))


async def _notify_stats(s: AsyncSession, user_id: int, stats: dict) -> None:
    """Еженедельная сводка очистки — по желанию (FR-7.7, S): пока служебный лог."""
    from . import alerting
    log.info("retention user=%s deleted_items=%s deleted_media=%s",
             user_id, stats["deleted_items"], stats["deleted_media"])
