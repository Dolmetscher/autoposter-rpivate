"""Сборка всех роутеров: веб-API (/api/web) и публичный API (/api).
Все веб-роуты, кроме auth и oauth, проходят проверку роли (deps.guard_role);
вебхук ЮKassa — публичный, без guard."""
from fastapi import APIRouter, Depends

from ..deps import guard_role
from .public import hooppy, yookassa
from .web import (accounts, ai, analytics, auth, billing, crosspost, dashboard,
                  files, oauth, posts, projects, schedules, settings_api,
                  sources, team, templates)

api_router = APIRouter()

# без guard: регистрация/вход
auth_router = APIRouter()
auth_router.include_router(auth.router)

# без guard: OAuth (колбэк приходит от провайдера без заголовка Authorization,
# авторизация подтверждается подписанным state-токеном, роли проверяет сам роут)
oauth_router = APIRouter()
oauth_router.include_router(oauth.router)

# под guard: роли viewer/editor/admin ограничивают мутации
guarded = APIRouter(dependencies=[Depends(guard_role)])
for r in (accounts.router, sources.router, files.router, posts.router,
          projects.router, schedules.router, crosspost.router, analytics.router,
          ai.router, templates.router, billing.router, settings_api.router,
          dashboard.router, team.router):
    guarded.include_router(r)

api_router.include_router(auth_router, prefix="/api/web")
api_router.include_router(oauth_router, prefix="/api/web")
api_router.include_router(guarded, prefix="/api/web")
api_router.include_router(hooppy.router, prefix="/api")
api_router.include_router(yookassa.router, prefix="/api")
