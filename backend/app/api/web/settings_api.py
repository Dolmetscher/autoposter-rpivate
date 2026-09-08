from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...deps import get_current_user
from ...models import ApiToken, User
from ...schemas import ApiTokenIn, LlmConfigIn
from ...security import generate_api_token, hash_api_token
from ...util import utcnow

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings(user: User = Depends(get_current_user)):
    llm = dict(user.llm_config or {})
    if llm.get("api_key"):
        llm["api_key"] = "***"
    return {"timezone": user.timezone, "llm_config": llm}


class SettingsIn(BaseModel):
    timezone: str | None = None
    llm_config: LlmConfigIn | None = None


@router.put("")
async def update_settings(payload: SettingsIn, user: User = Depends(get_current_user),
                          s: AsyncSession = Depends(get_session)):
    if payload.timezone:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(payload.timezone)
        except Exception:
            raise HTTPException(400, "Неизвестная таймзона")
        user.timezone = payload.timezone
    if payload.llm_config is not None:
        cfg = user.llm_config or {}
        new = payload.llm_config.model_dump()
        if new.get("api_key") == "***":
            new["api_key"] = cfg.get("api_key", "")
        user.llm_config = new
    await s.commit()
    llm = dict(user.llm_config or {})
    if llm.get("api_key"):
        llm["api_key"] = "***"
    return {"timezone": user.timezone, "llm_config": llm}


@router.get("/tokens")
async def list_tokens(user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(ApiToken).where(ApiToken.user_id == user.id)
                            .order_by(ApiToken.id))).scalars().all()
    return {"list": [{"id": t.id, "name": t.name, "created_at": t.created_at.isoformat(),
                      "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None}
                     for t in rows]}


@router.post("/tokens", status_code=201)
async def create_token(payload: ApiTokenIn, user: User = Depends(get_current_user),
                       s: AsyncSession = Depends(get_session)):
    token = generate_api_token()
    row = ApiToken(user_id=user.id, name=payload.name, token_hash=hash_api_token(token))
    s.add(row)
    await s.commit()
    await s.refresh(row)
    return {"id": row.id, "name": row.name, "token": token,
            "created_at": row.created_at.isoformat()}


@router.delete("/tokens/{token_id}")
async def delete_token(token_id: int, user: User = Depends(get_current_user),
                       s: AsyncSession = Depends(get_session)):
    row = (await s.execute(select(ApiToken).where(ApiToken.id == token_id,
                                                  ApiToken.user_id == user.id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Токен не найден")
    await s.delete(row)
    await s.commit()
    return {"success": True}
