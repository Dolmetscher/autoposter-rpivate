"""Telegram-бот-ассистент: long-polling getUpdates по всем настроенным ботам.

- /start — привязывает чат к пользователю (сохраняет chat_id в notify-конфиг);
- текст, фото, видео, документ или пересланное сообщение — превращает в пост:
  при заданных inbox_page_ids публикует сразу в них, иначе создаёт черновик.

Цикл запускается в процессе API (см. main.lifespan) при SCHEDULER_MODE != off."""
from __future__ import annotations

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from . import files as files_service
from . import notify
from . import publish as publish_service

log = logging.getLogger("tgbot")

_offsets: dict[str, int] = {}


async def bot_loop(session_factory) -> None:
    log.info("Telegram bot assistant started (poll=%ss)", settings.tg_bot_poll_seconds)
    while True:
        try:
            async with session_factory() as s:
                bots = await notify.users_with_bots(s)
                for user_id, token, cfg in bots:
                    try:
                        await _poll_one(s, user_id, token, cfg)
                    except Exception:
                        log.exception("bot poll failed for user %s", user_id)
                await s.commit()
        except Exception:
            log.exception("bot loop iteration failed")
        import asyncio
        await asyncio.sleep(settings.tg_bot_poll_seconds)


async def _poll_one(s: AsyncSession, user_id: int, token: str, cfg: dict) -> None:
    offset = _offsets.get(token, 0)
    async with httpx.AsyncClient(timeout=35) as client:
        r = await client.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": offset, "timeout": 0, "allowed_updates": '["message"]'})
        data = r.json()
    if not data.get("ok"):
        log.warning("getUpdates failed: %s", data.get("description"))
        return
    for upd in data.get("result", []):
        _offsets[token] = upd["update_id"] + 1
        msg = upd.get("message")
        if msg:
            await _handle_message(s, user_id, token, cfg, msg)


async def _handle_message(s: AsyncSession, user_id: int, token: str, cfg: dict,
                          msg: dict) -> None:
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or msg.get("caption") or "").strip()

    if text.startswith("/start"):
        if cfg.get("chat_id") != chat_id:
            cfg["chat_id"] = chat_id
            await notify.save_settings(s, user_id, cfg)
            await s.commit()
            await notify.send(token, chat_id,
                              "✅ Чат привязан. Пришлите текст, фото, видео или "
                              "пересланный пост — создам черновик"
                              + (" или опубликую в выбранные группы."
                                 if cfg.get("inbox_page_ids") else
                                 " (Выберите страницы «Входящие» в настройках, "
                                 "чтобы публиковать сразу.)"))
        else:
            await notify.send(token, chat_id, "Этот чат уже привязан ✅")
        return

    if cfg.get("chat_id") != chat_id:
        return  # незнакомый чат — игнорируем

    media_ids: list[str] = []
    try:
        for kind, entry in _extract_media(msg):
            mf = await _download_media(s, user_id, token, entry)
            if mf:
                media_ids.append(mf.id)
    except ValueError as e:
        await notify.send(token, chat_id, f"⚠️ Не удалось скачать медиа: {e}")
        return

    if not text and not media_ids:
        return

    page_ids = [int(p) for p in (cfg.get("inbox_page_ids") or [])]
    try:
        post = await publish_service.create_post(
            s, await notify.get_user(s, user_id),
            texts=[{"text": text, "source_id": 0}],
            media_ids=media_ids,
            settings={"source": "tgbot"},
            page_ids=page_ids if page_ids else [],
            when_type=1,
            status=None if page_ids else "draft",
        )
    except ValueError as e:
        await notify.send(token, chat_id, f"⚠️ {e}")
        return

    if page_ids:
        post = await publish_service.publish_post_now(s, post)
        ok = [t for t in post.targets if t.status == "published"]
        bad = [t for t in post.targets if t.status == "failed"]
        if bad:
            lines = [f"⚠️ Пост #{post.id}: не удалось опубликовать:"]
            lines += [f"• {t.page.social_page_name}: {t.error[:120]}" for t in bad[:3]]
            if ok:
                lines.append(f"Успешно: {len(ok)}")
            await notify.send(token, chat_id, "\n".join(lines))
        else:
            await notify.send(token, chat_id,
                              f"✅ Опубликовано в {len(ok)} шт.: "
                              + ", ".join(t.page.social_page_name for t in ok[:5]))
    else:
        await notify.send(token, chat_id,
                          f"📝 Черновик #{post.id} создан. Опубликуйте его "
                          f"из веб-интерфейса или задайте «Входящие» в настройках бота.")


def _extract_media(msg: dict) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    if msg.get("photo"):
        out.append(("photo", max(msg["photo"], key=lambda p: p.get("file_size", 0))))
    if msg.get("video"):
        out.append(("video", msg["video"]))
    if msg.get("document"):
        out.append(("document", msg["document"]))
    return out


async def _download_media(s: AsyncSession, user_id: int, token: str, entry: dict):
    file_id = entry.get("file_id")
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        r = await client.get(f"https://api.telegram.org/bot{token}/getFile",
                             params={"file_id": file_id})
        data = r.json()
        if not data.get("ok"):
            raise ValueError(data.get("description", "getFile failed"))
        file_path = data["result"].get("file_path")
        if not file_path:
            return None
        url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        lower = (file_path or "").lower()
        if lower.endswith((".jpg", ".jpeg", ".png", ".webp")):
            kind = "photo"
        elif lower.endswith((".mp4", ".mov", ".webm")):
            kind = "video"
        else:
            kind = "document"
        return await files_service.download_from_url(user_id, url, kind, s)
