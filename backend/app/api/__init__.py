"""Сборка всех роутеров: веб-API (/api/web) и публичный API (/api)."""
from fastapi import APIRouter

from .public import hooppy
from .web import (accounts, analytics, auth, crosspost, dashboard, files, oauth,
                  posts, projects, schedules, settings_api, sources)

api_router = APIRouter()

web_router = APIRouter()
web_router.include_router(auth.router)
web_router.include_router(accounts.router)
web_router.include_router(sources.router)
web_router.include_router(files.router)
web_router.include_router(posts.router)
web_router.include_router(projects.router)
web_router.include_router(schedules.router)
web_router.include_router(crosspost.router)
web_router.include_router(analytics.router)
web_router.include_router(settings_api.router)
web_router.include_router(dashboard.router)
web_router.include_router(oauth.router)

api_router.include_router(web_router, prefix="/api/web")
api_router.include_router(hooppy.router, prefix="/api")
