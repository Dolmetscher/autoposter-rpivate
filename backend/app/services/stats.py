"""Сбор метрик опубликованных постов: раз в час обходим цели, опубликованные
за последние 30 дней, и складываем снимки в post_stats."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..integrations.registry import get_adapter
from ..models import AppState, PostStat, PostTarget, SocialAccount
from ..util import utcnow

log = logging.getLogger("stats")

STATS_INTERVAL_MIN = 60
STATS_PERIOD_DAYS = 30
STATS_LIMIT = 100


async def update_stats_if_due(s: AsyncSession, now: datetime | None = None) -> int:
    """Вызывается из тика планировщика; реально собирает не чаще раза в час."""
    now = now or utcnow()
    row = await s.get(AppState, "stats_run")
    if row and row.value.get("ts"):
        try:
            last = datetime.fromisoformat(row.value["ts"])
            if now - last < timedelta(minutes=STATS_INTERVAL_MIN):
                return 0
        except ValueError:
            pass
    count = await update_stats(s, now)
    if row is not None:
        row.value = {"ts": now.isoformat()}
        row.updated_at = now
    else:
        s.add(AppState(key="stats_run", value={"ts": now.isoformat()}, updated_at=now))
    await s.flush()
    return count


async def update_stats(s: AsyncSession, now: datetime | None = None) -> int:
    now = now or utcnow()
    since = now - timedelta(days=STATS_PERIOD_DAYS)
    targets = (await s.execute(
        select(PostTarget)
        .options(selectinload(PostTarget.page))
        .where(PostTarget.status == "published",
               PostTarget.external_id != "",
               PostTarget.published_at >= since)
        .order_by(PostTarget.published_at.desc())
        .limit(STATS_LIMIT)
    )).scalars().all()

    collected = 0
    for target in targets:
        # статистика с последнего снимка ещё не устарела — пропускаем
        last = (await s.execute(
            select(PostStat).where(PostStat.target_id == target.id)
            .order_by(PostStat.fetched_at.desc()).limit(1)
        )).scalar_one_or_none()
        if last and now - last < timedelta(minutes=STATS_INTERVAL_MIN):
            continue
        if target.page is None:
            # страницу удалили — метрики собрать уже не для чего
            continue
        target.page.account = await s.get(SocialAccount, target.page.account_id)
        try:
            stats = await get_adapter(target.source_id).fetch_stats(target.page, target.external_id)
        except Exception as e:
            log.debug("stats failed for target %s: %s", target.id, e)
            continue
        if not stats:
            continue
        s.add(PostStat(
            target_id=target.id,
            views=int(stats.get("views", 0)),
            likes=int(stats.get("likes", 0)),
            reposts=int(stats.get("reposts", 0)),
            comments=int(stats.get("comments", 0)),
            fetched_at=now,
        ))
        collected += 1
    await s.flush()
    return collected
