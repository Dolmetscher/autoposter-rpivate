"""Health-check источников (модуль 5 ТЗ): каждые 6 часов по данным red_source_checks
(FR-5.1, FR-5.3) — статус-машина (FR-5.2), авто-архивация (FR-5.7), алерты о деградации."""
from __future__ import annotations

import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AppState, User
from ..util import utcnow
from . import alerting
from .ingest import STALENESS_HOURS
from .models import RedSource

log = logging.getLogger("red.health")

AUTO_ARCHIVE_DAYS = 7  # FR-5.7


async def run_health(s: AsyncSession, now=None) -> int:
    """Раз в 6 часов (слоты 00/06/12/18 по TZ пользователя, FR-5.1); тик вызывает часто,
    реальный проход — один на слот (метка в app_state)."""
    now = now or utcnow()
    user_ids = (await s.execute(select(RedSource.user_id).distinct())).scalars().all()
    checked = 0
    for user_id in user_ids:
        user = await s.get(User, user_id)
        if user is None:
            continue
        try:
            local = now.astimezone(ZoneInfo(user.timezone or "UTC"))
        except Exception:  # noqa: BLE001 — неверная TZ не останавливает проверку
            local = now
        slot = local.hour // 6
        today = local.strftime("%Y-%m-%d")
        key = f"red_health_{user.id}"
        row = await s.get(AppState, key)
        if row is not None and row.value.get("date") == today and row.value.get("slot") == slot:
            continue
        value = {"date": today, "slot": slot}
        if row is not None:
            row.value = value
            row.updated_at = now
        else:
            s.add(AppState(key=key, value=value, updated_at=now))
        checked += await check_all_sources(s, user_id, now)
    if checked:
        await s.flush()
    return checked


async def check_all_sources(s: AsyncSession, user_id: int, now=None) -> int:
    """Один проход health-check по всем живым источникам пользователя.
    Переходы (FR-5.2): 3 ошибки подряд или тишина > порога → degraded;
    5 успешных сборов подряд → active; 7 дней в degraded → archived (FR-5.7)."""
    now = now or utcnow()
    sources = (await s.execute(
        select(RedSource).where(RedSource.user_id == user_id,
                                RedSource.status.in_(("active", "degraded"))))).scalars().all()
    changed = 0
    for src in sources:
        stale_after = timedelta(hours=STALENESS_HOURS.get(src.type, 48))
        silent = src.last_ok_at is None or (now - src.last_ok_at) > stale_after

        if src.status == "active" and (src.fail_count >= 3 or silent):
            src.status = "degraded"
            if silent and src.fail_count < 3:
                src.last_error = "нет свежих материалов дольше порога"
            await alerting.notify_source_degraded(s, src)
            changed += 1
        elif src.status == "degraded":
            if src.ok_streak >= 5:
                src.status = "active"
                src.ok_streak = 0
                changed += 1
            elif src.last_ok_at and (now - src.last_ok_at) > timedelta(days=AUTO_ARCHIVE_DAYS):
                src.status = "archived"
                await alerting.notify_plain(
                    s, user_id, f"🗄 Источник «{src.title or src.identifier}» "
                                f"автоматически архивирован (7 дней в degraded). "
                                f"Восстановить можно в веб-интерфейсе.")
                changed += 1
    return changed
