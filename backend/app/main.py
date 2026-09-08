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


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    tasks = []
    if settings.scheduler_mode == "inline":
        from . import scheduler
        tasks.append(asyncio.create_task(
            scheduler.inline_loop(SessionFactory, settings.tick_interval_seconds)))
        log = logging.getLogger("startup")
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
