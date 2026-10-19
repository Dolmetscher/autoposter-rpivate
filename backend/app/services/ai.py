"""AI-помощник: генерация поста по теме, подбор хэштегов, «лучшее время публикации»
(по накопленной статистике просмотров из post_stats)."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import rewrite as rewrite_service
from ..models import Post, PostStat, PostTarget

GENERATE_PROMPT = (
    "Ты — SMM-копирайтер. Напиши готовый пост для соцсети на тему: «{topic}».{style}\n"
    "Требования: цепляющий первый абзац, 2–4 коротких абзаца, уместные эмодзи, "
    "в конце 3–5 релевантных хэштега. Верни только текст поста."
)

HASHTAGS_PROMPT = (
    "Подбери 5–8 релевантных хэштегов для этого поста. Верни одной строкой, "
    "хэштеги через пробел, каждый начинается с #. Только хэштеги, без пояснений.\n\nПост:\n"
)


async def generate_post(llm_config: dict, topic: str, style: str = "") -> str:
    style_line = f" Стиль: {style}." if style else ""
    prompt = GENERATE_PROMPT.format(topic=topic, style=style_line)
    data = await rewrite_service._post_chat(llm_config, prompt, topic)
    return (data["choices"][0]["message"]["content"] or "").strip()


async def suggest_hashtags(llm_config: dict, text: str) -> str:
    data = await rewrite_service._post_chat(llm_config, HASHTAGS_PROMPT, text[:2000])
    return (data["choices"][0]["message"]["content"] or "").strip()


async def best_hours(s: AsyncSession, user_id: int, tz_name: str = "UTC",
                     top: int = 3) -> list[int]:
    """Часы суток (0–23) с самым высоким средним числом просмотров.

    Часы считаются в таймзоне пользователя: published_at хранится в UTC."""
    from zoneinfo import ZoneInfo

    rows = (await s.execute(
        select(PostTarget.published_at, PostStat.views)
        .join(PostStat, PostStat.target_id == PostTarget.id)
        .join(Post, Post.id == PostTarget.post_id)
        .where(Post.user_id == user_id, PostTarget.published_at.is_not(None))
    )).all()
    if not rows:
        return []
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    buckets: dict[int, list[int]] = defaultdict(list)
    for published_at, views in rows:
        local = published_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        buckets[local.hour].append(views or 0)
    ranked = sorted(
        ((hour, sum(vs) / len(vs)) for hour, vs in buckets.items()),
        key=lambda x: x[1], reverse=True)
    return [hour for hour, _ in ranked[:top]]
