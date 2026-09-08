from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import ApiToken, SocialPage, User
from .security import decode_access_token, hash_api_token
from .util import utcnow

bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    s: AsyncSession = Depends(get_session),
) -> User:
    if creds is None:
        raise HTTPException(401, "Требуется авторизация")
    user_id = decode_access_token(creds.credentials)
    if user_id is None:
        raise HTTPException(401, "Токен недействителен")
    user = await s.get(User, user_id)
    if user is None:
        raise HTTPException(401, "Пользователь не найден")
    return user


async def get_token_user(
    request: Request,
    s: AsyncSession = Depends(get_session),
) -> User:
    """Авторизация публичного API по API-токену (Bearer ap_...)."""
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Требуется Authorization: Bearer <api_token>")
    token = auth[7:].strip()
    th = hash_api_token(token)
    row = (await s.execute(select(ApiToken).where(ApiToken.token_hash == th))).scalar_one_or_none()
    if row is None:
        raise HTTPException(401, "API-токен недействителен")
    user = await s.get(User, row.user_id)
    if user is None:
        raise HTTPException(401, "Пользователь не найден")
    row.last_used_at = utcnow()
    await s.commit()
    return user


async def get_page_checked(user: User, page_id: int, s: AsyncSession) -> SocialPage:
    page = (await s.execute(
        select(SocialPage).where(SocialPage.id == page_id, SocialPage.user_id == user.id)
    )).scalar_one_or_none()
    if page is None:
        raise HTTPException(404, f"Страница {page_id} не найдена")
    return page


get_session_dep = get_session
