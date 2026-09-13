"""Сборка всех роутеров: веб-API (/api/web) и публичный API (/api).
Все веб-роуты, кроме auth, проходят проверку роли (deps.guard_role)."""
from fastapi import APIRouter, Depends

from ..deps import guard_role
from ..redakciya import router as red_router
from .public import hooppy
from .web import (accounts, ai, analytics, auth, crosspost, dashboard, files,
                  oauth, posts, projects, schedules, settings_api, sources,
                  team, templates)

api_router = APIRouter()

# без guard: регистрация/вход
auth_router = APIRouter()
auth_router.include_router(auth.router)

# под guard: роли viewer/editor/admin ограничивают мутации
guarded = APIRouter(dependencies=[Depends(guard_role)])
for r in (accounts.router, sources.router, files.router, posts.router,
          projects.router, schedules.router, crosspost.router, analytics.router,
          ai.router, templates.router, settings_api.router, dashboard.router,
          oauth.router, team.router, red_router.router):
    guarded.include_router(r)

api_router.include_router(auth_router, prefix="/api/web")
api_router.include_router(guarded, prefix="/api/web")
api_router.include_router(hooppy.router, prefix="/api")
