from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from .api import api_router
from .config import settings
from .db import Base, SessionFactory, engine
from .services import storage

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

log = logging.getLogger("startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_columns(conn)
        await _cleanup_dangling(conn)

    tasks = []
    if settings.scheduler_mode == "inline":
        from . import scheduler
        tasks.append(asyncio.create_task(
            scheduler.inline_loop(SessionFactory, settings.tick_interval_seconds)))
        log.info("Inline scheduler started (interval=%ss)", settings.tick_interval_seconds)
    if settings.scheduler_mode in ("inline", "celery"):
        from .services import tgbot
        tasks.append(asyncio.create_task(tgbot.bot_loop(SessionFactory)))
    yield
    for t in tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


async def _migrate_columns(conn) -> None:
    """Мини-миграции для существующих БД (create_all не альтерит таблицы).
    Список: (таблица, колонка, DDL-тип)."""
    from sqlalchemy import text

    migrations = [
        ("users", "plan", "VARCHAR(32) DEFAULT 'free' NOT NULL"),
        ("social_accounts", "extra", "JSON"),
        ("crosspost_sources", "active", "BOOLEAN DEFAULT 1 NOT NULL"),
    ]
    for table, column, ddl in migrations:
        try:
            has = await conn.execute(text(
                f"SELECT {column} FROM {table} LIMIT 0"))
            has.close()
        except Exception:
            try:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            except Exception:
                pass


async def _cleanup_dangling(conn) -> None:
    """Убрать «осиротевшие» записи — ссылки на несуществующие строки.

    SQLite не проверяет внешние ключи, поэтому после удаления медиафайла или
    страницы оставались post_attachments/post_targets с битыми ссылками.
    Из-за этого сериализация постов падала с AttributeError (500 на /posts),
    а планировщик не мог опубликовать пост. Чистим мусор при старте.
    """
    from sqlalchemy import text

    # удаляем записи, ссылающиеся на несуществующие строки
    deletes = [
        ("post_attachments", "post_id NOT IN (SELECT id FROM posts)"),
        ("post_attachments", "media_file_id NOT IN (SELECT id FROM media_files)"),
        ("post_texts", "post_id NOT IN (SELECT id FROM posts)"),
        ("post_stats", "target_id NOT IN (SELECT id FROM post_targets)"),
        ("crosspost_seen", "source_row_id NOT IN (SELECT id FROM crosspost_sources)"),
        ("project_accounts", "project_id NOT IN (SELECT id FROM projects)"),
        ("project_accounts", "social_account_id NOT IN (SELECT id FROM social_accounts)"),
    ]
    for table, cond in deletes:
        try:
            await conn.execute(text(f"DELETE FROM {table} WHERE {cond}"))
        except Exception as e:  # таблицы могут отсутствовать в старой БД
            log.warning("cleanup %s: %s", table, e)

    # цели без поста — удаляем, цели без страницы — помечаем ошибкой:
    # пост остаётся в списке, но публиковать в удалённую страницу уже нельзя
    try:
        await conn.execute(text(
            "DELETE FROM post_targets WHERE post_id NOT IN (SELECT id FROM posts)"))
        res = await conn.execute(text(
            "UPDATE post_targets SET status='failed', error='Страница удалена' "
            "WHERE page_id NOT IN (SELECT id FROM social_pages) "
            "AND status NOT IN ('published', 'deleted')"))
        n = res.rowcount or 0
        if n:
            log.info("Очистка: %s целей публикации без существующей страницы помечены ошибкой", n)
    except Exception as e:
        log.warning("cleanup post_targets: %s", e)

    # посты без проекта/расписания — обнуляем ссылку
    for col, table in (("project_id", "projects"), ("schedule_id", "schedules")):
        try:
            await conn.execute(text(
                f"UPDATE posts SET {col}=NULL WHERE {col} IS NOT NULL "
                f"AND {col} NOT IN (SELECT id FROM {table})"))
        except Exception as e:
            log.warning("cleanup posts.%s: %s", col, e)


def create_app() -> FastAPI:
    app = FastAPI(title="Autoposter API", version="1.1.0",
                  description="Автопостинг в соцсети: публикация, расписание, "
                              "кросспостинг, рерайт, RSS, Telegram-ассистент. "
                              "Публичный API совместим с hooppy.",
                  lifespan=lifespan)
    app.add_middleware(CORSMiddleware,
                       allow_origins=["*"],
                       allow_credentials=True,
                       allow_methods=["*"],
                       allow_headers=["*"])
    app.include_router(api_router)

    @app.get("/media/{file_path:path}")
    async def media(file_path: str):
        """Раздача медиа: local — файл с диска, S3 — редирект на presigned/публичный URL."""
        if storage.is_s3():
            if not await storage.file_exists(file_path):
                raise HTTPException(404)
            return RedirectResponse(storage.public_url(file_path))
        try:
            p = storage.local_full(file_path)
        except ValueError:
            raise HTTPException(404)
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    return app


app = create_app()
