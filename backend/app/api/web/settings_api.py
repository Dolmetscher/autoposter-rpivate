from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...deps import get_current_user
from ...models import ApiToken, User
from ...schemas import ApiTokenIn, LlmConfigIn
from ...security import generate_api_token, hash_api_token
from ...services import notify as notify_service
from ...util import utcnow

router = APIRouter(prefix="/settings", tags=["settings"])


class NotifyIn(BaseModel):
    bot_token: str = ""
    chat_id: str | int | None = ""
    on_error: bool = True
    on_success: bool = False
    inbox_page_ids: list[int] = []


class SettingsIn(BaseModel):
    timezone: str | None = None
    llm_config: LlmConfigIn | None = None
    notify: NotifyIn | None = None


def _mask_notify(cfg: dict) -> dict:
    out = dict(cfg or {})
    if out.get("bot_token"):
        out["bot_token"] = "***"
    return out


@router.get("")
async def get_settings(user: User = Depends(get_current_user),
                       s: AsyncSession = Depends(get_session)):
    llm = dict(user.llm_config or {})
    if llm.get("api_key"):
        llm["api_key"] = "***"
    notify = _mask_notify(await notify_service.get_settings(s, user.id))
    return {"timezone": user.timezone, "llm_config": llm, "notify": notify}


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
    if payload.notify is not None:
        current = await notify_service.get_settings(s, user.id)
        new = payload.notify.model_dump()
        if new.get("bot_token") == "***":
            new["bot_token"] = current.get("bot_token", "")
        # chat_id привязывается только через /start в боте — из payload не меняем
        new["chat_id"] = current.get("chat_id", "")
        await notify_service.save_settings(s, user.id, new)
    await s.commit()
    llm = dict(user.llm_config or {})
    if llm.get("api_key"):
        llm["api_key"] = "***"
    notify = _mask_notify(await notify_service.get_settings(s, user.id))
    return {"timezone": user.timezone, "llm_config": llm, "notify": notify}


@router.post("/notify/test")
async def test_notify(user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    cfg = await notify_service.get_settings(s, user.id)
    if not cfg.get("bot_token") or not cfg.get("chat_id"):
        raise HTTPException(400, "Бот не настроен: добавьте токен и отправьте /start боту")
    sent, err = await notify_service.send(cfg["bot_token"], cfg["chat_id"],
                                          "✅ Autoposter: тестовое уведомление")
    if not sent:
        raise HTTPException(400, f"Не отправлено: {err}")
    return {"success": True}


@router.get("/plans")
async def get_plans(user: User = Depends(get_current_user),
                    s: AsyncSession = Depends(get_session)):
    from ...services import limits
    return {"plans": limits.plans(), "current": user.plan,
            "usage": await limits.usage(s, user)}


class PlanIn(BaseModel):
    plan: str


@router.post("/plan")
async def set_plan(payload: PlanIn, user: User = Depends(get_current_user),
                   s: AsyncSession = Depends(get_session)):
    from ...services import limits
    if payload.plan not in limits.plans():
        raise HTTPException(400, "Неизвестный тариф")
    user.plan = payload.plan
    await s.commit()
    return {"current": user.plan, "usage": await limits.usage(s, user)}


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
