from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import api_router
from .config import settings
from .db import Base, SessionFactory, engine

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.storage_dir).parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    task = None
    if settings.scheduler_mode == "inline":
        from . import scheduler
        task = scheduler.start(SessionFactory)
    yield
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def create_app() -> FastAPI:
    app = FastAPI(title="Autoposter API", version="1.0.0",
                  description="Автопостинг в соцсети: публикация, расписание, "
                              "кросспостинг, рерайт. Публичный API совместим с hooppy.",
                  lifespan=lifespan)
    app.add_middleware(CORSMiddleware,
                       allow_origins=["*"],
                       allow_credentials=True,
                       allow_methods=["*"],
                       allow_headers=["*"])
    app.include_router(api_router)

    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=settings.storage_dir), name="media")

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    return app


app = create_app()
