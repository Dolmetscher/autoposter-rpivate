"""Серверный OAuth-флоу: /api/web/oauth/{slug}/start → редирект на сеть,
callback → обмен кода на токен → подключение аккаунта → редирект в UI.

Роуты вынесены из общей группы с проверкой ролей: колбэк приходит от
провайдера без заголовка Authorization и должен быть доступен без авторизации
(доступ подтверждается подписанным state-токеном)."""
import secrets
from urllib.parse import quote

import jwt as pyjwt

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db import get_session
from ...constants import SOURCE_BY_SLUG
from ...deps import get_actor
from ...integrations.base import AdapterError
from ...integrations.registry import get_adapter
from ...models import User
from ...services import accounts as accounts_service
from ...util import utcnow

router = APIRouter(prefix="/oauth", tags=["oauth"])


def _state_token(user_id: int) -> str:
    payload = {"uid": user_id, "nonce": secrets.token_hex(8),
               "exp": int(utcnow().timestamp()) + 600}
    return pyjwt.encode(payload, settings.secret_key, algorithm="HS256")


def _back(frontend: str, **params: str) -> RedirectResponse:
    """Редирект в UI с параметрами (значения экранируются)."""
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items() if v)
    return RedirectResponse(f"{frontend}/accounts?{query}")


@router.get("/{slug}/start")
async def oauth_start(slug: str, request: Request,
                       s: AsyncSession = Depends(get_session)):
    """Начало OAuth. Токен принимается из заголовка Authorization или из
    query-параметра ?token= — второй случай нужен веб-интерфейсу: браузер
    уходит на страницу провайдера и не может задать собственные заголовки."""
    user, role = await get_actor(request, s)
    if role not in ("owner", "admin"):
        raise HTTPException(403, "Подключение аккаунтов доступно администратору или владельцу")
    info = SOURCE_BY_SLUG.get(slug)
    if not info or not info.oauth:
        raise HTTPException(404, "OAuth не поддерживается для этой сети")
    adapter = get_adapter(info.id)
    url = adapter.oauth_authorize_url(_state_token(user.id))
    if not url:
        raise HTTPException(400, "OAuth приложения не настроены на сервере "
                                 "(задайте ключи в config/.env)")
    return RedirectResponse(url)


@router.get("/{slug}/callback")
async def oauth_callback(slug: str, code: str = "", state: str = "", error: str = "",
                         s: AsyncSession = Depends(get_session)):
    frontend = settings.public_base_url
    if error:
        return _back(frontend, error=error)
    info = SOURCE_BY_SLUG.get(slug)
    if not info or not info.oauth:
        return _back(frontend, error="Неизвестная сеть")
    try:
        uid = pyjwt.decode(state, settings.secret_key, algorithms=["HS256"])["uid"]
    except (pyjwt.PyJWTError, KeyError):
        return _back(frontend, error="Истёк или неверен state OAuth")
    user = await s.get(User, uid)
    if not user:
        return _back(frontend, error="Пользователь не найден")
    adapter = get_adapter(info.id)
    try:
        creds = await adapter.oauth_exchange(code)
        await accounts_service.connect_account(s, user, info.id, creds)
        await s.commit()
    except AdapterError as e:
        return _back(frontend, error=str(e)[:150])
    return _back(frontend, connected=slug)
