"""Обработка callback_query от inline-кнопок «Автоматизированной редакции»
(§2.3.3, FR-3.6–3.7, FR-4.11, FR-5.4): вызывается из цикла tgbot-ассистента."""
from __future__ import annotations

import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User
from ..services import publish as publish_service
from ..util import utcnow
from . import alerting, digest as digest_service
from .models import (RedAlert, RedBlocklist, RedDigest, RedFeedback, RedItem,
                     RedScore, RedSource)

log = logging.getLogger("red.callbacks")

TTL_HOURS = 48  # FR-3.7: кнопки активны 48 часов


async def _answer(token: str, cb_id: str, text: str = "", show: bool = False) -> None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                              json={"callback_query_id": cb_id, "text": text[:190],
                                    "show_alert": show})
    except httpx.HTTPError as e:
        log.warning("answerCallbackQuery failed: %s", e)


async def _confirm(token: str, message: dict, chat_id, message_id: int, line: str) -> None:
    """Подтверждение редактированием сообщения: кнопки убраны, строка добавлена (FR-3.6)."""
    import httpx
    base = message.get("text") or message.get("caption") or ""
    text = (base + f"\n\n{line}").strip()
    field = "text" if message.get("text") else "caption"
    payload = {"chat_id": chat_id, "message_id": message_id,
               field: text[:3900], "reply_markup": {"inline_keyboard": []}}
    if field == "text":
        payload["parse_mode"] = ""
    try:
        method = "editMessageText" if message.get("text") else "editMessageCaption"
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(f"https://api.telegram.org/bot{token}/{method}", json=payload)
    except httpx.HTTPError as e:
        log.warning("confirm edit failed: %s", e)


def _now_local(user: User) -> str:
    try:
        tz = ZoneInfo(user.timezone or "UTC")
        return utcnow().astimezone(tz).strftime("%H:%M")
    except Exception:  # noqa: BLE001 — некорректная TZ не роняем обработчик
        return utcnow().strftime("%H:%M")


async def handle_callback(s: AsyncSession, user_id: int, token: str, cfg: dict,
                          cb: dict) -> bool:
    """True = callback относится к редакции и обработан."""
    data = cb.get("data") or ""
    if not data.startswith("red:"):
        return False
    user = await s.get(User, user_id)
    cb_id = cb.get("id", "")
    message = cb.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    ref = parts[2] if len(parts) > 2 and parts[2].isdigit() else None

    # TTL кнопок (FR-3.7)
    if action != "ui" and message.get("date"):
        age = utcnow().timestamp() - float(message["date"])
        if age > TTL_HOURS * 3600:
            await _answer(token, cb_id, "Материал устарел — используйте веб-интерфейс")
            return True

    try:
        if action == "ui":
            await _answer(token, cb_id, "Раздел «Редакция» в веб-интерфейсе")
        elif action in ("post", "digest", "ignore", "wrong") and ref:
            await _handle_item_action(s, user, token, cfg, cb_id, action, int(ref),
                                      message, chat_id, message_id)
        elif action in ("srcpause", "srcarchive") and ref:
            await _handle_source_action(s, user, token, cb_id, action, int(ref))
        elif action in ("dap", "drej", "dedit") and ref:
            await _handle_digest_action(s, user, token, cfg, cb_id, action, int(ref),
                                        message, chat_id, message_id)
        else:
            await _answer(token, cb_id)
        await s.commit()
    except ValueError as e:
        await s.rollback()
        await _answer(token, cb_id, str(e), show=True)
    except Exception:  # noqa: BLE001
        log.exception("red callback failed: %s", data)
        await s.rollback()
        await _answer(token, cb_id, "Ошибка обработки, попробуйте позже", show=True)
    return True


async def _handle_item_action(s: AsyncSession, user: User, token: str, cfg: dict,
                              cb_id: str, action: str, alert_id: int,
                              message: dict, chat_id, message_id: int | None) -> None:
    alert = await s.get(RedAlert, alert_id)
    if alert is None or alert.user_id != user.id:
        await _answer(token, cb_id, "Алерт не найден")
        return
    item = await s.get(RedItem, alert.item_id) if alert.item_id else None
    if item is None:
        await _answer(token, cb_id, "Материал не найден (удалён по retention)")
        return

    if action == "post":  # ✅ В канал → очередь публикации (FR-3.3 таблица действий)
        page_ids = [int(p) for p in (cfg.get("red_channel_page_ids") or [])]
        if not page_ids:
            raise ValueError("Не выбран TG-канал/страницы для публикации — "
                             "укажите их в настройках редакции")
        score = (await s.execute(select(RedScore).where(RedScore.item_id == item.id)
                                 .order_by(RedScore.id.desc()).limit(1))).scalars().first()
        post = await publish_service.create_post(
            s, user,
            texts=[{"text": alerting.post_text_for_item(item, score), "source_id": 0}],
            media_ids=list(item.media_ids or []),
            settings={"source": "redakciya", "red_item_id": item.id},
            page_ids=page_ids, when_type=1)
        await publish_service.publish_post_now(s, post)
        item.status = "posted"
        alert.action = "queued"
        await _confirm(token, message, chat_id, message_id,
                       f"✅ Добавлено в очередь публикации · {_now_local(user)}")
        await _answer(token, cb_id, "Опубликовано в очередь")
        return

    if action == "digest":  # 📅 В дайджест
        item.digest_flag = True
        item.status = "digest"
        alert.action = "digest"
        await _confirm(token, message, chat_id, message_id,
                       f"📅 Взято в ближайший дайджест · {_now_local(user)}")
        await _answer(token, cb_id, "В дайджесте")
        return

    if action == "ignore":  # 🗑 Игнор → блок-лист кластера на 30 дней
        s.add(RedBlocklist(user_id=user.id, content_hash=item.content_hash,
                           until=utcnow() + timedelta(days=30)))
        item.status = "ignored"
        alert.action = "ignored"
        await _confirm(token, message, chat_id, message_id,
                       f"🚫 Материал и похожие больше не алертятся · {_now_local(user)}")
        await _answer(token, cb_id, "В блок-листе")
        return

    if action == "wrong":  # ⚙️ Оценка неверна → feedback, алерт удаляется (FR-2.15)
        s.add(RedFeedback(user_id=user.id, item_id=item.id, action="wrong_score",
                          note=f"score={alert.text[:200]}"))
        item.status = "ignored"
        alert.action = "wrong_score"
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.post(f"https://api.telegram.org/bot{token}/deleteMessage",
                                  json={"chat_id": chat_id, "message_id": message_id})
        except httpx.HTTPError:
            pass
        await _answer(token, cb_id, "Спасибо, учтём в гайдлайнах")


async def _handle_source_action(s: AsyncSession, user: User, token: str, cb_id: str,
                                action: str, source_id: int) -> None:
    src = await s.get(RedSource, source_id)
    if src is None or src.user_id != user.id:
        await _answer(token, cb_id, "Источник не найден")
        return
    if action == "srcpause":
        src.status = "paused"
        await _answer(token, cb_id, f"«{src.title or src.identifier}» на паузе")
    else:
        src.status = "archived"
        await _answer(token, cb_id, f"«{src.title or src.identifier}» в архиве")


async def _handle_digest_action(s: AsyncSession, user: User, token: str, cfg: dict,
                                cb_id: str, action: str, digest_id: int,
                                message: dict, chat_id, message_id: int | None) -> None:
    dg = await s.get(RedDigest, digest_id)
    if dg is None or dg.user_id != user.id:
        await _answer(token, cb_id, "Дайджест не найден")
        return
    if dg.status != "draft":
        await _answer(token, cb_id, f"Дайджест уже обработан ({dg.status})")
        return
    if action == "dap":  # ✅ Публиковать (FR-4.13)
        await digest_service.publish_digest(s, dg)
        await _confirm(token, message, chat_id, message_id,
                       f"✅ Дайджест отправлен в публикацию · {_now_local(user)}")
        await _answer(token, cb_id, "Публикуется")
    elif action == "drej":  # ❌ Отклонить
        dg.status = "rejected"
        await _confirm(token, message, chat_id, message_id, "❌ Дайджест отклонён")
        await _answer(token, cb_id, "Отклонён")
    else:  # ✏️ Редактировать → веб (FR-4.12)
        await _answer(token, cb_id,
                      "Редактирование — в веб-интерфейсе, раздел «Редакция» → Дайджесты")
