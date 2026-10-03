"""Встроенный планировщик: выполняет тик каждые N секунд внутри процесса API.
Используется, когда нет redis/celery (локальная разработка, простые VPS)."""
import asyncio
import logging

log = logging.getLogger("scheduler")


async def tick_once(session_factory) -> dict:
    from .redakciya.jobs import run_editorial_jobs
    from .services import billing, crosspost, publish, stats

    result = {}
    async with session_factory() as session:
        try:
            result["due_posts"] = await publish.run_due_posts(session)
            result["deleted"] = await publish.process_deletes(session)
            result["sources"] = await crosspost.poll_due(session)
            result["stats"] = await stats.update_stats_if_due(session)
            result["billing"] = await billing.expire_if_due(session)
            await session.commit()
        except Exception:
            await session.rollback()
            log.exception("tick failed")
            result["error"] = True
    # подсистема «Автоматизированная редакция» — отдельная транзакция,
    # чтобы её сбой не откатил обычный постинг
    async with session_factory() as session:
        try:
            result["redakciya"] = await run_editorial_jobs(session)
            await session.commit()
        except Exception:
            await session.rollback()
            log.exception("redakciya jobs failed")
            result.setdefault("redakciya", {})["error"] = True
    return result


async def inline_loop(session_factory, interval_seconds: int) -> None:
    log.info("Inline scheduler started (interval=%ss)", interval_seconds)
    while True:
        await tick_once(session_factory)
        await asyncio.sleep(interval_seconds)


def start(session_factory) -> asyncio.Task | None:
    from .config import settings

    if settings.scheduler_mode != "inline":
        return None
    return asyncio.create_task(
        inline_loop(session_factory, settings.tick_interval_seconds))
