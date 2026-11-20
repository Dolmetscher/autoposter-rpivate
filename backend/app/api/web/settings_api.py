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
from ...services import crypto
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
    llm = user.llm_config if crypto.is_failed(user.llm_config) else dict(user.llm_config or {})
    # llm_config не расшифровался — показываем пустым и подсвечиваем в Настройках:
    # пользователь введёт ключ заново и сохранит
    llm_broken = crypto.is_failed(user.llm_config)
    if not llm_broken and llm.get("api_key"):
        llm["api_key"] = "***"
    notify_raw = await notify_service.get_settings(s, user.id)
    notify_broken = crypto.is_failed(notify_raw)
    notify = _mask_notify(notify_raw)
    # Флаги по конкретным полям (маркер FailedSecrets у записи), а не глобальный
    # счётчик ошибок: он «лип» и поднимал предупреждение, даже когда все ключи,
    # влияющие на работу, были в порядке
    return {"timezone": user.timezone, "llm_config": llm, "notify": notify,
            "llm_secrets_broken": llm_broken,
            "notify_secrets_broken": notify_broken}


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
        cfg = user.llm_config if not crypto.is_failed(user.llm_config) else {}
        new = payload.llm_config.model_dump()
        if new.get("api_key") == "***":
            new["api_key"] = cfg.get("api_key", "")
        user.llm_config = new
    if payload.notify is not None:
        current = await notify_service.get_settings(s, user.id)
        current = {} if crypto.is_failed(current) else current
        new = payload.notify.model_dump()
        if new.get("bot_token") == "***":
            new["bot_token"] = current.get("bot_token", "")
        # chat_id привязывается только через /start в боте — из payload не меняем
        new["chat_id"] = current.get("chat_id", "")
        await notify_service.save_settings(s, user.id, new)
    await s.commit()
    llm = user.llm_config if crypto.is_failed(user.llm_config) else dict(user.llm_config or {})
    llm_broken = crypto.is_failed(user.llm_config)
    if not llm_broken and llm.get("api_key"):
        llm["api_key"] = "***"
    notify_raw = await notify_service.get_settings(s, user.id)
    notify_broken = crypto.is_failed(notify_raw)
    return {"timezone": user.timezone, "llm_config": llm,
            "notify": _mask_notify(notify_raw),
            "llm_secrets_broken": llm_broken,
            "notify_secrets_broken": notify_broken}


@router.post("/notify/test")
async def test_notify(user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    cfg = await notify_service.get_settings(s, user.id)
    if crypto.is_failed(cfg):
        raise HTTPException(400, "Настройки бота не расшифровались (сменился "
                                "SECRET_KEY/CREDENTIALS_SECRET) — введите токен "
                                "заново в разделе «Настройки» и сохраните")
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
