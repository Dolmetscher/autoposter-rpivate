"""Адаптивный дайджест (модуль 4 ТЗ): 19:00 ежедневно + условный 13:00 (FR-4.1–4.3),
выборка ≤72 ч и ранжирование (FR-4.4–4.5), LLM-генерация вводки/заключения одним
вызовом (FR-4.9), approve-flow через TG-бота и веб (FR-4.11–4.13)."""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AppState, User
from ..services import notify as notify_service
from ..services import publish as publish_service
from ..util import utcnow
from . import alerting, llm
from .models import RedDigest, RedDigestItem, RedItem, RedScore

log = logging.getLogger("red.digest")

DIGEST_SIZE = 10
ALSO_SIZE = 5
MIN_ITEMS = 2

CATEGORY_EMOJI = {"новость": "📰", "аналитика": "📊", "мнение": "💬",
                  "инструменты": "🧰", "инфоповод": "⚡"}
CATEGORY_ORDER = ("новость", "аналитика", "мнение", "инструменты", "инфоповод")
DIGITS = "1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣8️⃣9️⃣🔟"


def _local_now(user: User):
    try:
        return utcnow().astimezone(ZoneInfo(user.timezone or "UTC"))
    except Exception:  # noqa: BLE001
        return utcnow().astimezone(ZoneInfo("UTC"))


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        h, m = value.split(":")
        return int(h) % 24, int(m) % 60
    except (ValueError, AttributeError):
        return 19, 0


async def _once_per_day(s: AsyncSession, user_id: int, key: str, today: str) -> bool:
    """True, если сегодня эта задача ещё не выполнялась (AppState last_date)."""
    row = await s.get(AppState, key)
    if row is not None and row.value.get("last_date") == today:
        return False
    value = {"last_date": today}
    if row is not None:
        row.value = value
        row.updated_at = utcnow()
    else:
        s.add(AppState(key=key, value=value, updated_at=utcnow()))
    return True


async def run_digests(s: AsyncSession, now=None) -> list[int]:
    """Проверка расписаний дайджестов по TZ каждого пользователя; вызывается каждый тик."""
    now = now or utcnow()
    user_ids = (await s.execute(
        select(RedItem.user_id).where(RedItem.fetched_at >= now - timedelta(hours=72))
        .distinct())).scalars().all()
    built: list[int] = []
    for user_id in user_ids:
        user = await s.get(User, user_id)
        if user is None:
            continue
        local = _local_now(user)
        today = local.strftime("%Y-%m-%d")
        dh, dm = _parse_hhmm(settings.red_digest_time)
        past_digest_time = (local.hour, local.minute) >= (dh, dm)

        if past_digest_time and await _once_per_day(s, user.id, f"red_digest19_{user.id}", today):
            dg = await build_digest(s, user, "daily19", now)
            if dg is not None:
                built.append(dg.id)

        if settings.red_noon_digest and not past_digest_time:
            noon_passed = (local.hour, local.minute) >= (13, 0)
            if noon_passed and await _once_per_day(
                    s, user.id, f"red_digest13_{user.id}", today):
                since_morning = local.replace(hour=7, minute=0, second=0, microsecond=0)
                worthy = await _digest_count(s, user.id, since_morning)
                if worthy >= 3:  # FR-4.2: условный выпуск
                    dg = await build_digest(s, user, "noon13", now)
                    if dg is not None:
                        built.append(dg.id)

    for dg_id in await mark_stale(s, now):
        built.append(dg_id)
    if built:
        await s.flush()
    return built


async def _digest_count(s: AsyncSession, user_id: int, since) -> int:
    rows = await _candidate_items(s, user_id, since)
    return len(rows)


async def _candidate_items(s: AsyncSession, user_id: int, since) -> list[tuple[RedItem, RedScore | None]]:
    """Материалы-кандидаты: digest_worthy/digest_flag, ≤72 ч, не опубликованы,
    не игнор, не входили в прошлые дайджесты (FR-4.4)."""
    cutoff = utcnow() - timedelta(hours=settings.red_retention_hours)
    latest = (select(RedScore.item_id, func.max(RedScore.id).label("sid"))
              .group_by(RedScore.item_id).subquery())
    in_digest = select(RedDigestItem.item_id)
    q = (select(RedItem, RedScore)
         .join(latest, latest.c.item_id == RedItem.id)
         .join(RedScore, RedScore.id == latest.c.sid)
         .where(RedItem.user_id == user_id,
                RedItem.fetched_at >= cutoff,
                RedItem.status.notin_(("ignored", "posted", "filtered")),
                RedItem.id.notin_(in_digest),
                (RedScore.digest_worthy.is_(True)) | (RedItem.digest_flag.is_(True)))
         .order_by(RedItem.fetched_at.desc())
         .limit(100))
    if since:  # материал за окно периода; явно отмеченные — независимо от окна
        q = q.where((RedItem.fetched_at >= since) | (RedItem.digest_flag.is_(True)))
    return [(row[0], row[1]) for row in (await s.execute(q)).all()]


def _rank(pairs: list[tuple[RedItem, RedScore | None]], now) -> list[tuple[RedItem, RedScore | None, float]]:
    """score = 0.7 × significance + 0.3 × свежесть (FR-4.5)."""
    ranked = []
    for item, score in pairs:
        sig = float(score.significance or 0) if score else 0.0
        age_h = max(0.0, (now - item.fetched_at).total_seconds() / 3600)
        freshness = max(0.0, 1.0 - age_h / settings.red_retention_hours)
        ranked.append((item, score, 0.7 * sig + 0.3 * freshness))
    ranked.sort(key=lambda x: (-x[2], -x[0].fetched_at.timestamp()))
    return ranked


def _domain(url: str) -> str:
    return alerting.domain_of(url)


def _render(ranked, intro: str, outro: str, title: str) -> str:
    """Текст дайджеста по шаблону FR-4.8 (LLM пишет только вводку и заключение).
    Plain text — удобно и для TG-черновика, и для постинга в любые сети."""
    lines = [title, ""]
    if intro:
        lines += [intro, ""]
    top = ranked[:DIGEST_SIZE]
    ordered = sorted(top, key=lambda x: CATEGORY_ORDER.index(
        x[1].category if x[1] and x[1].category in CATEGORY_ORDER else "новость"))
    current, num = None, 0
    for item, score, _ in ordered:
        cat = score.category if score and score.category in CATEGORY_ORDER else "новость"
        if cat != current:
            current = cat
            num = 0
            lines.append(f"{CATEGORY_EMOJI[cat]} {cat.capitalize()}")
        num += 1
        icon = DIGITS[num - 1] if num <= len(DIGITS) else f"{num}."
        headline = (score.headline_ru if score and score.headline_ru else item.title)
        summary = (score.summary_ru if score and score.summary_ru else (item.body or "")[:300])
        lines.append(f"{icon} {headline}")
        if summary.strip():
            lines.append(summary.strip())
        if item.url:
            dom = _domain(item.url)
            lines.append(f"🔗 {dom or item.url}")
        lines.append("")
    also = ranked[DIGEST_SIZE:DIGEST_SIZE + ALSO_SIZE]
    if also:
        lines.append("📌 Также в ленте: " + " · ".join(
            ((sc.headline_ru if sc and sc.headline_ru else it.title)[:80])
            for it, sc, _ in also))
    if outro:
        lines += ["", outro]
    return "\n".join(lines)


async def _llm_intro_outro(s: AsyncSession, user: User,
                           ranked) -> tuple[str, str]:
    """Один LLM-вызов на весь дайджест: вводка по топ-3 и заключение (FR-4.9)."""
    top3 = "\n".join(
        f"- {s.headline_ru if s and s.headline_ru else it.title}"
        for it, s, _ in ranked[:3])
    prompt = ("Ты — редактор новостного канала. По трём главным материалам напиши "
              "вводку (1-2 предложения, живой тон) и заключение с вопросом для "
              "вовлечения подписчиков. Верни СТРОГО JSON: "
              '{"intro": "...", "outro": "..."}\n\nМатериалы:\n' + top3)
    chain = llm._effective_chain(user)
    link = chain[0] if chain else llm._ollama_link()
    if link is None or await llm.budget_left(s, user.id) <= 0:
        return "", ""
    try:
        content = await llm._chat(link, prompt, "Напиши intro и outro.")
        await llm._spend(s, user.id)
        m = json.loads((json_search(content) or "{}"))
        return str(m.get("intro") or "")[:500], str(m.get("outro") or "")[:500]
    except Exception as e:  # noqa: BLE001 — дайджест не зависит от LLM
        log.info("digest LLM intro failed: %s", e)
    return "", ""


def json_search(content: str) -> str | None:
    m = re.search(r"\{.*\}", content, re.S)
    return m.group(0) if m else None


async def build_digest(s: AsyncSession, user: User, dtype: str, now=None):
    """Сборка черновика дайджеста; None — материалов < 2 (FR-4.3)."""
    now = now or utcnow()
    hours = 6 if dtype == "noon13" else 24
    pairs = await _candidate_items(s, user.id, now - timedelta(hours=hours))
    ranked = _rank(pairs, now)
    if len(ranked) < MIN_ITEMS:
        if dtype == "daily19":
            await alerting.notify_plain(
                s, user.id, "📭 Материалов недостаточно (<2 за 72 ч) — "
                            "дайджест пропущен")
        return None

    intro, outro = await _llm_intro_outro(s, user, ranked)
    if not intro:
        intro = "Сегодня главное: " + "; ".join(
            (sc.headline_ru if sc and sc.headline_ru else it.title)
            for it, sc, _ in ranked[:3]).rstrip(".") + "."
    if not outro:
        outro = "Что из этого показалось вам самым интересным? Делитесь мнением в комментариях 👇"

    title = f"📰 Дайджест · {now.strftime('%d.%m')}" + (" · день" if dtype == "noon13" else "")
    text = _render(ranked, intro, outro, title)
    dg = RedDigest(user_id=user.id, dtype=dtype, period_start=now - timedelta(hours=hours),
                   period_end=now, draft_text=text, status="draft")
    s.add(dg)
    await s.flush()
    for pos, (item, _score, _rank_score) in enumerate(ranked[:DIGEST_SIZE]):
        s.add(RedDigestItem(digest_id=dg.id, item_id=item.id, position=pos))
        if not item.digest_flag:
            item.status = "digest"

    cfg = await notify_service.get_settings(s, user.id)
    if cfg.get("red_auto_publish"):  # FR-4.14 (S): без approve
        await publish_digest(s, dg)
    else:
        await alerting.send_digest_draft(s, user, dg)
    return dg


async def publish_digest(s: AsyncSession, dg: RedDigest) -> None:
    """Публикация одобренного дайджеста через общий пайплайн autoposter (FR-4.13)."""
    user = await s.get(User, dg.user_id)
    cfg = await notify_service.get_settings(s, dg.user_id)
    page_ids = [int(p) for p in (cfg.get("red_channel_page_ids") or [])]
    if not page_ids:
        raise ValueError("Не выбраны страницы для публикации дайджеста — "
                         "укажите их в настройках редакции")
    first_item = (await s.execute(
        select(RedItem).join(RedDigestItem, RedDigestItem.item_id == RedItem.id)
        .where(RedDigestItem.digest_id == dg.id)
        .order_by(RedDigestItem.position).limit(1))).scalars().first()
    # медиа только для короткого текста: caption Telegram ограничен 1024 символами
    media_ids = []
    if first_item and first_item.media_ids and len(dg.draft_text) <= 1000:
        media_ids = [first_item.media_ids[0]]
    post = await publish_service.create_post(
        s, user, texts=[{"text": dg.draft_text, "source_id": 0}],
        media_ids=media_ids, settings={"source": "redakciya", "red_digest_id": dg.id},
        page_ids=page_ids, when_type=1)
    await publish_service.publish_post_now(s, post)
    dg.status = "published"
    dg.published_at = utcnow()
    dg.post_id = post.id
    items = (await s.execute(
        select(RedItem).join(RedDigestItem, RedDigestItem.item_id == RedItem.id)
        .where(RedDigestItem.digest_id == dg.id))).scalars().all()
    for item in items:
        item.status = "posted"
    await s.flush()


async def mark_stale(s: AsyncSession, now=None) -> list[int]:
    """Не одобренные за 24 часа черновики → stale (FR-4.13)."""
    now = now or utcnow()
    stale = (await s.execute(
        select(RedDigest).where(RedDigest.status == "draft",
                                RedDigest.created_at < now - timedelta(hours=24))
        .limit(20))).scalars().all()
    out = []
    for dg in stale:
        dg.status = "stale"
        out.append(dg.id)
    return out
