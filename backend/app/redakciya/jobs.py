"""Единая точка входа задач «Автоматизированной редакции»: вызывается каждый тик
планировщика (inline и celery). Поллинг и скоринг — каждый тик; дайджест,
health-check и retention проверяют своё расписание сами (метки в app_state)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AppState, User
from ..util import utcnow
from . import alerting, digest, health, ingest, retention, scoring
from .models import RedSource

log = logging.getLogger("red.jobs")


async def run_editorial_jobs(s: AsyncSession, now=None) -> dict:
    now = now or utcnow()
    result: dict = {}
    result["polled"] = await ingest.poll_due(s, now)
    result["scoring"] = await scoring.run_scoring_queue(s, now)
    result["digests"] = await digest.run_digests(s, now)
    result["health"] = await health.run_health(s, now)
    result["retention"] = await retention.run_retention(s, now)
    result["silence"] = await check_silence(s, now)
    return result


async def check_silence(s: AsyncSession, now=None) -> int:
    """«Пайплайн молчит»: ни одного нового материала за 60 минут → служебное
    сообщение владельцам активных источников (FR-3.3). 0 = выключено."""
    if not settings.red_silence_alert_min:
        return 0
    now = now or utcnow()

    row = await s.get(AppState, "red_silence")
    if row is None or not row.value.get("last_item_ts"):
        return 0
    try:
        last_item = datetime.fromisoformat(row.value["last_item_ts"])
    except ValueError:
        return 0
    if now - last_item < timedelta(minutes=settings.red_silence_alert_min):
        return 0

    # не спамим: повторное уведомление не чаще чем раз в 6 часов
    key = "red_silence_notified"
    notified = await s.get(AppState, key)
    if notified is not None and notified.value.get("ts"):
        try:
            if now - datetime.fromisoformat(notified.value["ts"]) < timedelta(hours=6):
                return 0
        except ValueError:
            pass

    user_ids = (await s.execute(
        select(RedSource.user_id).where(RedSource.status == "active").distinct()
    )).scalars().all()
    sent = 0
    for user_id in user_ids:
        await alerting.notify_plain(
            s, user_id, f"😴 Пайплайн молчит: ни одного нового материала за "
                        f"{settings.red_silence_alert_min} минут. Проверьте источники.")
        sent += 1
    if notified is not None:
        notified.value = {"ts": now.isoformat()}
        notified.updated_at = now
    else:
        s.add(AppState(key=key, value={"ts": now.isoformat()}, updated_at=now))
    return sent
