from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...deps import get_current_user
from ...models import User
from ...services import ai as ai_service

router = APIRouter(prefix="/ai", tags=["ai"])


class GenerateIn(BaseModel):
    topic: str
    style: str = ""


class HashtagsIn(BaseModel):
    text: str


@router.post("/generate")
async def generate(payload: GenerateIn, user: User = Depends(get_current_user),
                   s: AsyncSession = Depends(get_session)):
    if not payload.topic.strip():
        raise HTTPException(400, "Укажите тему поста")
    cfg = user.llm_config or {}
    if not cfg.get("base_url") or not cfg.get("api_key"):
        raise HTTPException(400, "AI не настроен: заполните Base URL и ключ в Настройках")
    try:
        text = await ai_service.generate_post(cfg, payload.topic.strip(), payload.style.strip())
    except Exception as e:
        raise HTTPException(502, f"Провайдер AI недоступен: {e}")
    return {"text": text}


@router.post("/hashtags")
async def hashtags(payload: HashtagsIn, user: User = Depends(get_current_user),
                   s: AsyncSession = Depends(get_session)):
    cfg = user.llm_config or {}
    if not cfg.get("base_url") or not cfg.get("api_key"):
        raise HTTPException(400, "AI не настроен: заполните Base URL и ключ в Настройках")
    try:
        tags = await ai_service.suggest_hashtags(cfg, payload.text)
    except Exception as e:
        raise HTTPException(502, f"Провайдер AI недоступен: {e}")
    return {"hashtags": tags}


@router.get("/best-hours")
async def best_hours(user: User = Depends(get_current_user),
                     s: AsyncSession = Depends(get_session)):
    hours = await ai_service.best_hours(s, user.id, tz_name=user.timezone)
    return {"hours": hours,
            "hint": ("Лучшие часы по средней статистике просмотров ваших постов"
                     if hours else "Пока мало данных — опубликуйте несколько постов")}
