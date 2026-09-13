"""AI-скоринг очереди новых материалов (FR-2.1, FR-2.5, FR-2.11–2.12):
префильтр по тегам → кэш по content_hash → вызов LLM → запись → алерт при ≥4.0."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import User
from ..util import utcnow
from . import alerting, llm
from .models import RedGuideline, RedItem, RedScore, RedSource

log = logging.getLogger("red.scoring")

QUEUE_LIMIT = 10  # материалов за тик


async def active_guideline(s: AsyncSession, user_id: int) -> RedGuideline | None:
    return (await s.execute(
        select(RedGuideline)
        .where(RedGuideline.user_id == user_id, RedGuideline.is_active.is_(True))
        .order_by(RedGuideline.version.desc()).limit(1)
    )).scalars().first()


async def run_scoring_queue(s: AsyncSession, now=None) -> dict:
    """Обработка очереди new → scored/alerted/filtered/review; вызывается каждый тик."""
    now = now or utcnow()
    items = (await s.execute(
        select(RedItem).where(RedItem.status == "new")
        .order_by(RedItem.fetched_at).limit(QUEUE_LIMIT)
    )).scalars().all()
    stats = {"scored": 0, "filtered": 0, "review": 0, "alerts": 0, "cached": 0}
    for item in items:
        try:
            result = await score_item(s, item)
            for k, v in result.items():
                stats[k] = stats.get(k, 0) + v
        except Exception:
            log.exception("scoring failed for item %s", item.id)
    if any(stats.values()):
        await s.flush()
    return stats


async def score_item(s: AsyncSession, item: RedItem) -> dict:
    user = await s.get(User, item.user_id)
    source = await s.get(RedSource, item.source_id)
    if user is None or source is None:
        item.status = "review"
        return {"review": 1}
    guideline = await active_guideline(s, item.user_id)
    if guideline is None:
        return {}  # гайдлайны не заданы — материал ждёт в очереди

    # Префильтр до LLM: ни одного тега в тексте → без вызова LLM (FR-2.12)
    prefilter_tags = guideline.tags or source.tags or []
    if prefilter_tags:
        text = f"{item.title}\n{item.body}".lower()
        if not any(str(t).strip().lower() in text for t in prefilter_tags):
            item.status = "filtered"
            return {"filtered": 1}

    # Кэш: тот же контент уже скорен по этой версии гайдлайнов (FR-2.11)
    cached = (await s.execute(
        select(RedScore)
        .join(RedItem, RedItem.id == RedScore.item_id)
        .where(RedItem.content_hash == item.content_hash,
               RedScore.guideline_version == guideline.version,
               RedScore.significance.is_not(None),
               RedScore.item_id != item.id)
        .order_by(RedScore.id.desc()).limit(1)
    )).scalars().first()
    if cached is not None:
        s.add(RedScore(
            item_id=item.id, user_id=item.user_id,
            guideline_version=cached.guideline_version,
            significance=cached.significance, matched_tags=cached.matched_tags,
            rationale=cached.rationale, digest_worthy=cached.digest_worthy,
            category=cached.category, headline_ru=cached.headline_ru,
            summary_ru=cached.summary_ru, model=f"{cached.model} (кэш)",
        ))
        item.status = "scored"
        alerts = await _maybe_alert(s, user, item, cached.significance, f"{cached.model} (кэш)")
        return {"scored": 1, "cached": 1, "alerts": alerts}

    score, model, latency = await llm.score_material(s, user, source, item, guideline)
    if score is None:
        item.red_attempts += 1
        if item.red_attempts >= 3:  # FR-2.5: не осилили за 3 раунда → ручная проверка
            item.status = "review"
            return {"review": 1}
        return {}  # останется в очереди на следующий тик

    s.add(RedScore(
        item_id=item.id, user_id=item.user_id, guideline_version=guideline.version,
        significance=score["significance"], matched_tags=score["matched_tags"],
        rationale=score["rationale"], digest_worthy=score["digest_worthy"],
        category=score["category"], headline_ru=score["headline_ru"],
        summary_ru=score["summary_ru"], model=model, latency_ms=latency,
    ))
    item.status = "scored"
    alerts = 0
    if score["significance"] is not None:
        alerts = await _maybe_alert(s, user, item, score["significance"], model)
    return {"scored": 1, "alerts": alerts}


async def _maybe_alert(s: AsyncSession, user: User, item: RedItem,
                       significance: float, model: str) -> int:
    """Горячий материал → алерт сразу после скоринга (латентность, FR-3.2).
    Rule-based режим алерты подавляет (§2.2.5)."""
    if significance < settings.red_alert_threshold or model == "rule-based":
        return 0
    from .models import RedAlert
    already = await s.scalar(select(RedAlert.id).where(
        RedAlert.cluster_key == item.cluster_key, RedAlert.kind == "item"))
    if already is not None:  # FR-3.10: один кластер = один алерт
        return 0
    return await alerting.send_item_alert(s, user, item)
