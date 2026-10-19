from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import ApiToken, SocialPage, TeamMember, User
from .security import decode_access_token, hash_api_token
from .util import utcnow

bearer = HTTPBearer(auto_error=False)


async def _acting_user(identity: User, s: AsyncSession) -> tuple[User, str]:
    """Действующий пользователь и его роль: член команды работает от имени
    владельца команды (роль — owner | admin | editor | viewer)."""
    member = (await s.execute(
        select(TeamMember).where(TeamMember.member_user_id == identity.id)
    )).scalar_one_or_none()
    if member is not None:
        owner = await s.get(User, member.team_owner_id)
        if owner is not None:
            return owner, member.role
    return identity, "owner"


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    s: AsyncSession = Depends(get_session),
) -> User:
    """Возвращает ДЕЙСТВУЮЩЕГО пользователя: если вошедший — член команды,
    все операции выполняются от имени владельца команды (роль — в request.state.role:
    owner | admin | editor | viewer)."""
    if creds is None:
        raise HTTPException(401, "Требуется авторизация")
    user_id = decode_access_token(creds.credentials)
    if user_id is None:
        raise HTTPException(401, "Токен недействителен")
    identity = await s.get(User, user_id)
    if identity is None:
        raise HTTPException(401, "Пользователь не найден")

    user, role = await _acting_user(identity, s)
    request.state.role = role
    request.state.identity = identity
    return user


async def get_user_or_token(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    s: AsyncSession = Depends(get_session),
) -> User:
    """Пользователь по заголовку Authorization ИЛИ по query-параметру ?token=.

    Нужен там, где браузер сам не может добавить заголовок: <img src>,
    background-image, скачивание файла. Заголовок имеет приоритет.
    """
    raw = creds.credentials if creds is not None else ""
    raw = raw or request.query_params.get("token", "")
    user_id = decode_access_token(raw.strip())
    if user_id is None:
        raise HTTPException(401, "Требуется авторизация")
    identity = await s.get(User, user_id)
    if identity is None:
        raise HTTPException(401, "Пользователь не найден")
    user, role = await _acting_user(identity, s)
    request.state.role = role
    request.state.identity = identity
    return user


async def get_actor(request: Request, s: AsyncSession) -> tuple[User, str]:
    """Действующий пользователь и роль для сценариев без заголовка Authorization:
    токен принимается либо из заголовка, либо из query-параметра ?token=
    (нужно для перехода браузера на страницу авторизации OAuth-провайдера)."""
    auth = request.headers.get("Authorization", "")
    raw = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    raw = raw or request.query_params.get("token", "")
    user_id = decode_access_token(raw)
    if user_id is None:
        raise HTTPException(401, "Требуется авторизация")
    identity = await s.get(User, user_id)
    if identity is None:
        raise HTTPException(401, "Пользователь не найден")
    user, role = await _acting_user(identity, s)
    request.state.role = role
    request.state.identity = identity
    return user, role


def _role(request: Request) -> str:
    return getattr(request.state, "role", "owner")


async def guard_role(request: Request, user: User = Depends(get_current_user)) -> None:
    """Права по ролям: viewer — только чтение; editor — контент без настроек/команды/тарифа;
    admin/owner — всё, кроме управления командой и тарифом (только владелец)."""
    role = _role(request)
    mutating = request.method in ("POST", "PUT", "DELETE", "PATCH")
    if role == "viewer" and mutating:
        raise HTTPException(403, "Роль «наблюдатель» — доступен только просмотр")
    path = request.url.path
    if mutating and "/api/web/billing" in path and role not in ("owner", "admin"):
        raise HTTPException(403, "Оплата тарифа доступна администратору или владельцу")
    is_team_or_plan = "/api/web/team" in path or path.endswith("/settings/plan")
    if is_team_or_plan and mutating and role != "owner":
        raise HTTPException(403, "Управление командой и тарифом доступно только владельцу")
    elif role == "editor" and mutating and ("/api/web/settings" in path
                                             or "/api/web/accounts" in path):
        raise HTTPException(403, "Роли «редактор» недоступны настройки и подключение аккаунтов")


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
