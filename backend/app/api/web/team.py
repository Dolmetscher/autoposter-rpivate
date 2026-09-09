"""Команда: приглашение пользователей с ролями admin/editor/viewer.
Владелец приглашает по email зарегистрированного пользователя; тот, войдя,
работает с данными владельца в рамках своей роли (см. deps.guard_role)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...deps import get_current_user
from ...models import TeamMember, User
from ...util import utcnow

router = APIRouter(prefix="/team", tags=["team"])

ROLES = {"admin": "Администратор", "editor": "Редактор", "viewer": "Наблюдатель"}


class InviteIn(BaseModel):
    email: str
    role: str = "editor"


class RoleIn(BaseModel):
    role: str


def _out(m: TeamMember, email: str) -> dict:
    return {"id": m.id, "member_user_id": m.member_user_id, "email": email,
            "role": m.role, "created_at": m.created_at.isoformat()}


@router.get("/my-role")
async def my_role(request: Request, user: User = Depends(get_current_user)):
    """Роль вошедшего относительно действующего аккаунта (для UI-кнопок согласования)."""
    return {"role": getattr(request.state, "role", "owner"),
            "acting_email": user.email,
            "identity_email": (getattr(request.state, "identity", None) or user).email}


@router.get("")
async def list_members(user: User = Depends(get_current_user),
                       s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(TeamMember, User.email)
                            .join(User, User.id == TeamMember.member_user_id)
                            .where(TeamMember.team_owner_id == user.id)
                            .order_by(TeamMember.id))).all()
    members = [_out(m, email) for m, email in rows]
    return {"owner_email": user.email, "roles": ROLES, "list": members}


@router.post("/invite", status_code=201)
async def invite(payload: InviteIn, user: User = Depends(get_current_user),
                 s: AsyncSession = Depends(get_session)):
    if payload.role not in ROLES:
        raise HTTPException(400, f"Роль должна быть одной из: {', '.join(ROLES)}")
    target = (await s.execute(select(User).where(
        User.email == payload.email.lower()))).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "Пользователь с таким email не зарегистрирован")
    if target.id == user.id:
        raise HTTPException(400, "Нельзя пригласить самого себя")
    existing = (await s.execute(select(TeamMember).where(
        TeamMember.member_user_id == target.id))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(400, "Пользователь уже состоит в команде")
    m = TeamMember(team_owner_id=user.id, member_user_id=target.id, role=payload.role)
    s.add(m)
    await s.commit()
    await s.refresh(m)
    return _out(m, target.email)


@router.put("/{member_id}")
async def change_role(member_id: int, payload: RoleIn,
                      user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    if payload.role not in ROLES:
        raise HTTPException(400, f"Роль должна быть одной из: {', '.join(ROLES)}")
    m = (await s.execute(select(TeamMember).where(
        TeamMember.id == member_id, TeamMember.team_owner_id == user.id))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "Участник не найден")
    m.role = payload.role
    await s.commit()
    return _out(m, (await s.get(User, m.member_user_id)).email)


@router.delete("/{member_id}")
async def remove_member(member_id: int, user: User = Depends(get_current_user),
                        s: AsyncSession = Depends(get_session)):
    m = (await s.execute(select(TeamMember).where(
        TeamMember.id == member_id, TeamMember.team_owner_id == user.id))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "Участник не найден")
    await s.delete(m)
    await s.commit()
    return {"success": True}


@router.post("/leave")
async def leave(request: Request, user: User = Depends(get_current_user),
                s: AsyncSession = Depends(get_session)):
    """Выход из команды (вызывает сам участник: acting = владелец,
    поэтому ищем членство по identity из request.state)."""
    identity = getattr(request.state, "identity", None)
    if identity is None or identity.id == user.id:
        raise HTTPException(400, "Вы не состоите в команде")
    m = (await s.execute(select(TeamMember).where(
        TeamMember.member_user_id == identity.id))).scalar_one_or_none()
    if m is None:
        raise HTTPException(400, "Вы не состоите в команде")
    await s.delete(m)
    await s.commit()
    return {"success": True}
