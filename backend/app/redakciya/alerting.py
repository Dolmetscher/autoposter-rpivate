"""Алертинг горящих новостей в TG-бот владельца (модуль 3 ТЗ):
формат сообщения (FR-3.4–3.5), inline-кнопки (FR-3.6–3.7), анти-флуд (FR-3.8),
один алерт на кластер (FR-3.10), уведомления о деградации источников (FR-5.4)."""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import MediaFile, User
from ..services import notify
from ..services import storage
from ..util import utcnow
from .models import RedAlert, RedDigest, RedItem, RedScore, RedSource

log = logging.getLogger("red.alerting")

_AGE_UNITS = {
    "minute": ("минуту", "минуты", "минут"),
    "hour": ("час", "часа", "часов"),
    "day": ("день", "дня", "дней"),
}


def age_text(dt) -> str:
    """«12 минут назад» для возраста материала."""
    if dt is None:
        return "только что"
    secs = max(0, int((utcnow() - dt).total_seconds()))
    if secs < 60:
        return "только что"
    if secs < 3600:
        m = secs // 60
        return f"{m} {_plural(m, _AGE_UNITS['minute'])} назад"
    if secs < 86400:
        h = secs // 3600
        return f"{h} {_plural(h, _AGE_UNITS['hour'])} назад"
    d = secs // 86400
    return f"{d} {_plural(d, _AGE_UNITS['day'])} назад"


def _plural(n: int, forms: tuple[str, ...]) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


# К TokenType-независимая транслитерация (FR-3.5, FR-8.3: Bluesky без кириллицы)
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya"})


def hashtag(tag: str) -> str:
    """Валидный TG-хештег: транслит, без пробелов, буквы/цифры/подчёркивание."""
    t = (tag or "").strip().lower().translate(_TRANSLIT)
    t = re.sub(r"[^0-9a-z_]+", "_", t.replace(" ", "_")).strip("_")
    return f"#{t}" if t else ""


def domain_of(url: str) -> str:
    try:
        return (urlsplit(url).netloc or "").removeprefix("www.")
    except ValueError:
        return ""


def post_text_for_item(item: RedItem, score: RedScore | None) -> str:
    """Текст поста одного материала (FR-8.3): headline + summary + ссылка + хештеги."""
    headline = (score.headline_ru if score and score.headline_ru else item.title) or item.title
    summary = (score.summary_ru if score and score.summary_ru else item.body[:300]) or ""
    tags = " ".join(hashtag(t) for t in ((score.matched_tags if score else []) or [])[:5])
    parts = [headline.strip()]
    if summary.strip():
        parts.append(summary.strip())
    if item.url:
        parts.append(f"🔗 {item.url}")
    if tags:
        parts.append(tags)
    return "\n\n".join(p for p in parts if p.strip())


async def _tg(token: str, method: str, payload: dict | None = None,
              files: dict | None = None) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            if files:
                r = await client.post(f"https://api.telegram.org/bot{token}/{method}",
                                      data=payload, files=files)
            else:
                r = await client.post(f"https://api.telegram.org/bot{token}/{method}",
                                      json=payload)
            data = r.json()
            if not data.get("ok"):
                log.warning("tg %s failed: %s", method, data.get("description"))
                return None
            return data.get("result")
    except httpx.HTTPError as e:
        log.warning("tg %s error: %s", method, e)
        return None


async def _cfg(s: AsyncSession, user_id: int) -> dict:
    cfg = await notify.get_settings(s, user_id)
    if not cfg.get("bot_token") or not cfg.get("chat_id"):
        return {}
    return cfg


def _buttons(alert_id: int) -> list[list[dict]]:
    return [[
        {"text": "✅ В канал", "callback_data": f"red:post:{alert_id}"},
        {"text": "📅 В дайджест", "callback_data": f"red:digest:{alert_id}"},
    ], [
        {"text": "🗑 Игнор", "callback_data": f"red:ignore:{alert_id}"},
        {"text": "⚙️ Оценка неверна", "callback_data": f"red:wrong:{alert_id}"},
    ]]


def _alert_html(item: RedItem, score: RedScore | None, source: RedSource) -> str:
    sig = f"{score.significance:.1f}" if score else "?"
    headline = html.escape((score.headline_ru if score and score.headline_ru
                            else item.title) or item.title)
    summary = html.escape((score.summary_ru if score and score.summary_ru
                           else item.body[:300]) or "")
    tags = " ".join(hashtag(t) for t in ((score.matched_tags if score else []) or
                                         source.tags or [])[:5])
    lines = [f"🔥 Горячее · {sig}/5",
             f"🗞 {html.escape(source.title or source.identifier)}"]
    if tags:
        lines.append(tags)
    lines += ["", f"<b>{headline}</b>"]
    if summary:
        lines += ["", f"📄 {summary}"]
    if item.url:
        lines += ["", f"🔗 {html.escape(domain_of(item.url) or item.url)}"]
    lines.append(f"⏱ {age_text(item.published_at)}")
    return "\n".join(lines)


async def send_item_alert(s: AsyncSession, user: User, item: RedItem) -> int:
    """Алерт о горячем материале. 1 = отправлен, 0 = нет (бот не настроен/ошибка)."""
    cfg = await _cfg(s, user.id)
    if not cfg:
        log.info("red alert skipped: TG-бот не настроен (user %s)", user.id)
        return 0
    token, chat_id = cfg["bot_token"], cfg["chat_id"]

    # Анти-флуд (FR-3.8): >N алертов за 10 минут → сводное сообщение
    window_start = utcnow() - timedelta(minutes=10)
    recent = int((await s.execute(
        select(func.count(RedAlert.id)).where(
            RedAlert.user_id == user.id, RedAlert.kind == "item",
            RedAlert.sent_at >= window_start))).scalar() or 0)
    score = (await s.execute(
        select(RedScore).where(RedScore.item_id == item.id)
        .order_by(RedScore.id.desc()).limit(1))).scalars().first()
    source = await s.get(RedSource, item.source_id)

    alert = RedAlert(user_id=user.id, item_id=item.id, cluster_key=item.cluster_key,
                     kind="item", text=score.headline_ru if score else item.title[:300])
    s.add(alert)
    await s.flush()

    if recent >= settings.red_max_alerts_per_10min:
        await _append_batch(s, token, chat_id, user.id, item)
        await s.flush()
        return 1

    text = _alert_html(item, score, source)
    keyboard = {"inline_keyboard": _buttons(alert.id)}
    result = None
    if item.media_ids:
        mf = await s.get(MediaFile, item.media_ids[0])
        if mf is not None:
            try:
                data = await storage.read_file(mf.file_path)
                result = await _tg(token, "sendPhoto",
                                   {"chat_id": chat_id,
                                    "caption": text[:1024],
                                    "parse_mode": "HTML",
                                    "reply_markup": keyboard},
                                   files={"photo": (mf.name or "photo.jpg", data)})
            except Exception:  # noqa: BLE001 — пост без картинки (FR-8.13)
                result = None
    if result is None:
        result = await _tg(token, "sendMessage",
                           {"chat_id": chat_id, "text": text[:4000],
                            "parse_mode": "HTML", "reply_markup": keyboard,
                            "disable_web_page_preview": False})
    if result is None:
        await s.delete(alert)  # не блокируем кластер несостоявшимся алертом
        await s.flush()
        return 0
    alert.tg_chat_id = str(chat_id)
    alert.tg_message_id = result.get("message_id")
    item.status = "alerted"
    await s.flush()
    return 1


async def _append_batch(s: AsyncSession, token: str, chat_id, user_id: int,
                        item: RedItem) -> None:
    """Сводное сообщение «+ ещё N горячих материалов» (FR-3.8)."""
    from ..models import AppState
    key = f"red_batch_{user_id}"
    now = utcnow()
    row = await s.get(AppState, key)
    state = None
    if row is not None and row.value.get("ts"):
        try:
            if now - datetime.fromisoformat(row.value["ts"]) < timedelta(minutes=10):
                state = dict(row.value)
        except ValueError:
            pass
    state = state or {"titles": [], "message_id": None}
    state["ts"] = now.isoformat()
    titles = state["titles"] = (state.get("titles") or [])[-9:] + [item.title[:100]]
    text = (f"🔥 Горячих материалов: +{len(titles)} за последние 10 минут\n\n"
            + "\n".join(f"• {html.escape(t)}" for t in titles)
            + "\n\nПолный список с кнопками — в веб-интерфейсе, раздел «Редакция».")
    markup = {"inline_keyboard": [[{"text": "🗂 Открыть в интерфейсе",
                                    "callback_data": "red:ui"}]]}
    if state.get("message_id"):
        result = await _tg(token, "editMessageText",
                           {"chat_id": chat_id, "message_id": state["message_id"],
                            "text": text[:4000], "reply_markup": markup})
    else:
        result = await _tg(token, "sendMessage",
                           {"chat_id": chat_id, "text": text[:4000],
                            "reply_markup": markup})
    if result:
        state["message_id"] = result.get("message_id") or state.get("message_id")
    if row is not None:
        row.value = state
        row.updated_at = now
    else:
        s.add(AppState(key=key, value=state, updated_at=now))


async def notify_source_degraded(s: AsyncSession, src: RedSource) -> None:
    """Мгновенный алерт о деградации источника (FR-5.4) с кнопками действий."""
    user = await s.get(User, src.user_id)
    cfg = await _cfg(s, src.user_id)
    if not cfg:
        return
    text = (f"⚠️ <b>Источник деградировал</b>\n"
            f"{html.escape(src.title or src.identifier)} ({src.type})\n"
            f"Причина: {src.fail_count} неудачных сборов подряд"
            f"{'; последняя ошибка: ' + html.escape(src.last_error[:200]) if src.last_error else ''}\n"
            f"Последний успешный сбор: "
            f"{src.last_ok_at.strftime('%d.%m %H:%M') if src.last_ok_at else 'не было'}")
    markup = {"inline_keyboard": [[
        {"text": "⏸ Пауза", "callback_data": f"red:srcpause:{src.id}"},
        {"text": "🗑 Архивировать", "callback_data": f"red:srcarchive:{src.id}"},
    ]]}
    alert = RedAlert(user_id=src.user_id, kind="system", text="деградация источника")
    s.add(alert)
    await s.flush()
    result = await _tg(cfg["bot_token"], "sendMessage",
                       {"chat_id": cfg["chat_id"], "text": text, "parse_mode": "HTML",
                        "reply_markup": markup})
    if result:
        alert.tg_chat_id = str(cfg["chat_id"])
        alert.tg_message_id = result.get("message_id")
    await s.flush()


async def send_digest_draft(s: AsyncSession, user: User, digest: RedDigest) -> int | None:
    """Черновик дайджеста с approve-кнопками (FR-4.11). Возвращает message_id."""
    cfg = await _cfg(s, user.id)
    if not cfg:
        return None
    markup = {"inline_keyboard": [[
        {"text": "✅ Публиковать", "callback_data": f"red:dap:{digest.id}"},
        {"text": "✏️ Редактировать", "callback_data": f"red:dedit:{digest.id}"},
        {"text": "❌ Отклонить", "callback_data": f"red:drej:{digest.id}"},
    ]]}
    result = await _tg(cfg["bot_token"], "sendMessage",
                       {"chat_id": cfg["chat_id"], "text": digest.draft_text[:4000],
                        "reply_markup": markup, "disable_web_page_preview": True})
    if result is None:
        return None
    digest.tg_message_id = result.get("message_id")
    await s.flush()
    return digest.tg_message_id


async def notify_plain(s: AsyncSession, user_id: int, text: str) -> None:
    """Служебное уведомление владельцу (тишина пайплайна, недостаток материалов...)."""
    cfg = await _cfg(s, user_id)
    if cfg:
        await _tg(cfg["bot_token"], "sendMessage", {"chat_id": cfg["chat_id"],
                                                    "text": text[:4000]})
