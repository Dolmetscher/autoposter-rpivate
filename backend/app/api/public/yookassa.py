"""Публичный вебхук ЮKassa (без авторизации). Тело не доверяем:
статус платежа перепроверяется запросом к API ЮKassa."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...services import billing

log = logging.getLogger("yookassa")

router = APIRouter(tags=["yookassa"])


@router.post("/yookassa/webhook")
async def yookassa_webhook(request: Request, s: AsyncSession = Depends(get_session)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Ожидается JSON")
    event = body.get("event", "")
    ext_id = (body.get("object") or {}).get("id", "")
    if not ext_id:
        raise HTTPException(400, "Нет id платежа")
    try:
        result = await billing.handle_webhook(s, event, ext_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        log.exception("Ошибка обработки вебхука ЮKassa (%s %s)", event, ext_id)
        raise HTTPException(502, "Не удалось обработать уведомление")
    await s.commit()
    return {"ok": True, **result}
