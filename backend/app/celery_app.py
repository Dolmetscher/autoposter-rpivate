"""Celery-режим для продакшена: beat запускает tick каждую минуту,
publish_post_task публикует посты, созданные API-процессом.
Запуск: celery -A app.celery_app worker -B  (или worker и beat отдельно)."""
from __future__ import annotations

import asyncio
import os

from celery import Celery
from celery.schedules import crontab

broker_url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
celery_app = Celery("autoposter", broker=broker_url, backend=broker_url)
celery_app.conf.beat_schedule = {
    "tick-every-minute": {"task": "app.celery_app.tick", "schedule": 60.0},
}
celery_app.conf.timezone = "UTC"


def _run(coro):
    return asyncio.run(coro)


@celery_app.task(name="app.celery_app.tick")
def tick():
    from .db import SessionFactory
    from .scheduler import tick_once

    return _run(tick_once(SessionFactory))


@celery_app.task(name="app.celery_app.publish_post_task",
                 bind=True, max_retries=3, default_retry_delay=60)
def publish_post_task(self, post_id: int):  # noqa: ANN001
    from sqlalchemy import select

    from .db import SessionFactory
    from .models import Post
    from .services import publish as publish_service

    async def job():
        async with SessionFactory() as session:
            post = (await session.execute(
                select(Post).where(Post.id == post_id))).scalar_one_or_none()
            if post is None or post.status == "published":
                return
            await publish_service.publish_post_now(session, post)
            await session.commit()

    try:
        return _run(job())
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc)
