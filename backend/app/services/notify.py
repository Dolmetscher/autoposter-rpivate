"""Уведомления о результатах публикаций через Telegram-бота.
Конфиг хранится в user_settings (JSON, шифруется): data["notify"] =
{"bot_token", "chat_id", "on_error": bool, "on_success": bool, "inbox_page_ids": []}."""
from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Post, User, UserSettings

log = logging.getLogger("notify")


async def get_settings(s: AsyncSession, user_id: int) -> dict:
    row = await s.get(UserSettings, user_id)
    return (row.data if row else {}).get("notify", {})


async def save_settings(s: AsyncSession, user_id: int, notify: dict) -> None:
    row = await s.get(UserSettings, user_id)
    # новый dict — иначе SQLAlchemy не увидит изменение того же объекта
    data = dict(row.data) if row else {}
    data["notify"] = notify
    if row is None:
        s.add(UserSettings(user_id=user_id, data=data))
    else:
        row.data = data
    await s.flush()


async def send(bot_token: str, chat_id, text: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                                  json={"chat_id": chat_id, "text": text[:3500]})
            data = r.json()
            if not data.get("ok"):
                return False, data.get("description", "ошибка Telegram")
            return True, ""
    except httpx.HTTPError as e:
        return False, str(e)


async def notify_publish_result(s: AsyncSession, post: Post) -> None:
    """Отправить сводку по результатам публикации (если настроено и не отключено)."""
    if (post.settings or {}).get("source") == "tgbot":
        return  # бот-ассистент отвечает сам
    cfg = await get_settings(s, post.user_id)
    bot_token = cfg.get("bot_token")
    chat_id = cfg.get("chat_id")
    if not bot_token or not chat_id:
        return
    failed = [t for t in post.targets if t.status == "failed"]
    ok = [t for t in post.targets if t.status in ("published", "deleted")]
    if failed and cfg.get("on_error", True):
        lines = [f"⚠️ Пост #{post.id}: ошибки публикации"]
        for t in failed[:5]:
            lines.append(f"• {_page_name(t)}: {t.error[:150]}")
        if ok:
            lines.append(f"Успешно: {len(ok)}")
        sent, err = await send(bot_token, chat_id, "\n".join(lines))
        if not sent:
            log.warning("notify failed: %s", err)
    elif ok and not failed and cfg.get("on_success"):
        pages = ", ".join(_page_name(t) for t in ok[:5])
        await send(bot_token, chat_id, f"✅ Пост #{post.id} опубликован: {pages}")


def _page_name(target) -> str:
    """Имя страницы цели; страницу могли удалить — тогда вернём заглушку."""
    page = getattr(target, "page", None)
    return page.social_page_name if page is not None else "удалённая страница"


async def notify_custom(s: AsyncSession, user_id: int, text: str) -> None:
    cfg = await get_settings(s, user_id)
    if cfg.get("bot_token") and cfg.get("chat_id"):
        await send(cfg["bot_token"], cfg["chat_id"], text)


async def users_with_bots(s: AsyncSession) -> list[tuple[int, str, dict]]:
    """[(user_id, bot_token, notify_cfg)] для всех настроенных ботов."""
    rows = (await s.execute(select(UserSettings))).scalars().all()
    out = []
    for row in rows:
        cfg = (row.data or {}).get("notify", {})
        if cfg.get("bot_token"):
            out.append((row.user_id, cfg["bot_token"], cfg))
    return out


async def get_user(s: AsyncSession, user_id: int) -> User | None:
    return await s.get(User, user_id)
