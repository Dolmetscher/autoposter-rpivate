from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...deps import get_current_user
from ...models import User
from ...schemas import LoginIn, RegisterIn, UserOut
from ...security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201)
async def register(payload: RegisterIn, s: AsyncSession = Depends(get_session)):
    exists = (await s.execute(select(User).where(User.email == payload.email.lower()))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "Пользователь с таким email уже существует")
    user = User(email=payload.email.lower(), password_hash=hash_password(payload.password))
    s.add(user)
    await s.commit()
    await s.refresh(user)
    return user


@router.post("/login")
async def login(payload: LoginIn, s: AsyncSession = Depends(get_session)):
    user = (await s.execute(select(User).where(User.email == payload.email.lower()))).scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Неверный email или пароль")
    return {"access_token": create_access_token(user.id), "user": UserOut.model_validate(user)}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


class ProfileUpdateIn(BaseModel):
    timezone: str | None = None


@router.put("/me", response_model=UserOut)
async def update_me(payload: ProfileUpdateIn, user: User = Depends(get_current_user),
                    s: AsyncSession = Depends(get_session)):
    if payload.timezone:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(payload.timezone)
        except Exception:
            raise HTTPException(400, "Неизвестная таймзона")
        user.timezone = payload.timezone
    await s.commit()
    await s.refresh(user)
    return user
