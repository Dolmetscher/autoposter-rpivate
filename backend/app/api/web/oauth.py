"""Серверный OAuth-флоу: /api/web/oauth/{slug}/start → редирект на сеть,
callback → обмен кода на токен → подключение аккаунта → редирект в UI."""
import secrets

import jwt as pyjwt

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db import get_session
from ...constants import SOURCE_BY_SLUG
from ...deps import get_current_user
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


@router.get("/{slug}/start")
async def oauth_start(slug: str, user: User = Depends(get_current_user)):
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
        return RedirectResponse(f"{frontend}/accounts?error={error}")
    info = SOURCE_BY_SLUG.get(slug)
    if not info or not info.oauth:
        return RedirectResponse(f"{frontend}/accounts?error=Неизвестная+сеть")
    try:
        uid = pyjwt.decode(state, settings.secret_key, algorithms=["HS256"])["uid"]
    except pyjwt.PyJWTError:
        return RedirectResponse(f"{frontend}/accounts?error=Истёк+state+OAuth")
    user = await s.get(User, uid)
    if not user:
        return RedirectResponse(f"{frontend}/accounts?error=Пользователь+не+найден")
    adapter = get_adapter(info.id)
    try:
        creds = await adapter.oauth_exchange(code)
        await accounts_service.connect_account(s, user, info.id, creds)
        await s.commit()
    except AdapterError as e:
        return RedirectResponse(f"{frontend}/accounts?error={str(e)[:150]}")
    return RedirectResponse(f"{frontend}/accounts?connected={slug}")
