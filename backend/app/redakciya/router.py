"""REST API подсистемы «Автоматизированная редакция» (/api/web/red/*):
источники (CRUD + тестовый сбор, FR-1.15–1.16/5.8), гайдлайны (FR-2.7–2.9),
лента материалов с оценками, дайджесты с approve (FR-4.11–4.13),
агрегаты и сводка здоровья пайплайна."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import get_current_user
from ..integrations.base import AdapterError
from ..models import User
from ..schemas import RedDigestTextIn, RedGuidelineIn, RedSourceIn
from ..services import notify as notify_service
from ..util import utcnow
from . import alerting, digest as digest_service, fetchers, ingest, llm, scoring
from .ingest import DEFAULT_INTERVALS
from .models import (RedAlert, RedDailyStat, RedDigest, RedDigestItem, RedFeedback,
                     RedGuideline, RedItem, RedScore, RedSource, RedSourceCheck)

router = APIRouter(prefix="/red", tags=["redakciya"])


# ---------- источники ----------

def source_out(src: RedSource) -> dict:
    return {
        "id": src.id, "type": src.type, "identifier": src.identifier,
        "title": src.title or src.identifier, "tags": src.tags or [],
        "poll_interval_sec": src.poll_interval_sec, "jitter_pct": src.jitter_pct,
        "status": src.status, "fail_count": src.fail_count,
        "last_error": src.last_error,
        "last_ok_at": src.last_ok_at.isoformat() if src.last_ok_at else None,
        "next_poll_at": src.next_poll_at.isoformat() if src.next_poll_at else None,
        "items_24h": src.items_24h,
    }


@router.get("/sources")
async def list_sources(user: User = Depends(get_current_user),
                       s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(RedSource).where(RedSource.user_id == user.id)
                            .order_by(RedSource.id))).scalars().all()
    return {"list": [source_out(x) for x in rows]}


@router.post("/sources", status_code=201)
async def create_source(payload: RedSourceIn, user: User = Depends(get_current_user),
                        s: AsyncSession = Depends(get_session)):
    src_type = payload.type.strip().lower()
    if src_type not in DEFAULT_INTERVALS:
        raise HTTPException(400, f"Неизвестный тип источника: {payload.type}. "
                                 f"Доступны: {', '.join(DEFAULT_INTERVALS)}")
    try:  # немедленный тестовый сбор перед сохранением (FR-1.16, FR-5.8)
        await fetchers.fetch_items(src_type, payload.identifier, limit=3)
    except (ValueError, AdapterError) as e:
        raise HTTPException(400, str(e))
    src = RedSource(
        user_id=user.id, type=src_type, identifier=payload.identifier.strip(),
        title=payload.title, tags=[t.strip() for t in payload.tags if t.strip()],
        poll_interval_sec=payload.poll_interval_sec or DEFAULT_INTERVALS[src_type],
        status=payload.status if payload.status in ("active", "paused") else "active",
        next_poll_at=utcnow(),
    )
    s.add(src)
    await s.commit()
    await s.refresh(src)
    return source_out(src)


@router.post("/sources/preview")
async def preview_source(payload: RedSourceIn, user: User = Depends(get_current_user),
                         s: AsyncSession = Depends(get_session)):
    """Тестовый запрос + предпросмотр последних 3 материалов (FR-1.16)."""
    try:
        items = await fetchers.fetch_items(payload.type.strip().lower(),
                                           payload.identifier, limit=3)
    except (ValueError, AdapterError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "items": [
        {"title": (ingest.strip_html(i.text).split("\n") or [""])[0][:200] or i.url,
         "url": i.url, "media_count": len(i.media or [])}
        for i in items]}


@router.patch("/sources/{source_id}")
async def update_source(source_id: int, payload: RedSourceIn,
                        user: User = Depends(get_current_user),
                        s: AsyncSession = Depends(get_session)):
    src = (await s.execute(select(RedSource).where(
        RedSource.id == source_id, RedSource.user_id == user.id))).scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Источник не найден")
    if payload.title:
        src.title = payload.title
    src.tags = [t.strip() for t in payload.tags if t.strip()]
    if payload.poll_interval_sec:
        src.poll_interval_sec = max(60, payload.poll_interval_sec)
    if payload.status in ("active", "paused", "archived"):
        src.status = payload.status
        if payload.status == "active":
            src.fail_count = 0
            src.ok_streak = 0
            src.next_poll_at = utcnow()
    await s.commit()
    return source_out(src)


@router.delete("/sources/{source_id}")
async def archive_source(source_id: int, user: User = Depends(get_current_user),
                         s: AsyncSession = Depends(get_session)):
    src = (await s.execute(select(RedSource).where(
        RedSource.id == source_id, RedSource.user_id == user.id))).scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Источник не найден")
    src.status = "archived"
    await s.commit()
    return {"success": True}


@router.post("/sources/{source_id}/run")
async def run_source(source_id: int, user: User = Depends(get_current_user),
                     s: AsyncSession = Depends(get_session)):
    src = (await s.execute(select(RedSource).where(
        RedSource.id == source_id, RedSource.user_id == user.id))).scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Источник не найден")
    try:
        created = await ingest.poll_source(s, src)
    except Exception as e:  # noqa: BLE001 — ошибка сбора уже записана в last_error
        raise HTTPException(400, str(e))
    await s.commit()
    return {"created": created, "source": source_out(src)}


@router.get("/sources/{source_id}/diagnostic")
async def source_diagnostic(source_id: int, user: User = Depends(get_current_user),
                            s: AsyncSession = Depends(get_session)):
    """Диагностика: последние 20 сборов + последняя ошибка (FR-5.5)."""
    src = (await s.execute(select(RedSource).where(
        RedSource.id == source_id, RedSource.user_id == user.id))).scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Источник не найден")
    checks = (await s.execute(
        select(RedSourceCheck).where(RedSourceCheck.source_id == src.id)
        .order_by(RedSourceCheck.created_at.desc()).limit(20))).scalars().all()
    return {"source": source_out(src), "checks": [{
        "ok": c.ok, "error": c.error, "duration_ms": c.duration_ms,
        "created_at": c.created_at.isoformat()} for c in checks]}


# ---------- гайдлайны ----------

@router.get("/guidelines")
async def get_guidelines(user: User = Depends(get_current_user),
                         s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(RedGuideline).where(RedGuideline.user_id == user.id)
                            .order_by(RedGuideline.version.desc()).limit(20))).scalars().all()
    return {"list": [{
        "id": g.id, "version": g.version, "content": g.content, "tags": g.tags or [],
        "is_active": g.is_active,
        "created_at": g.created_at.isoformat()} for g in rows]}


@router.post("/guidelines", status_code=201)
async def save_guidelines(payload: RedGuidelineIn,
                          user: User = Depends(get_current_user),
                          s: AsyncSession = Depends(get_session)):
    if len(payload.content) > 5000:
        raise HTTPException(400, "Гайдлайны: максимум 5000 символов (FR-2.7)")
    last = (await s.execute(select(RedGuideline)
            .where(RedGuideline.user_id == user.id)
            .order_by(RedGuideline.version.desc()).limit(1))).scalars().first()
    version = (last.version + 1) if last else 1
    if last is not None:
        last.is_active = False
    g = RedGuideline(user_id=user.id, version=version, content=payload.content.strip(),
                     tags=[t.strip() for t in payload.tags if t.strip()], is_active=True)
    s.add(g)
    rescored = 0
    if payload.rescore:  # FR-2.9: фоновый перескоринг материалов за 72 ч
        cutoff = utcnow() - timedelta(hours=72)
        rows = (await s.execute(
            select(RedItem).where(
                RedItem.user_id == user.id, RedItem.fetched_at >= cutoff,
                RedItem.status.in_(("scored", "alerted", "digest", "filtered")))
        )).scalars().all()
        for item in rows:
            item.status = "new"
            item.red_attempts = 0
        rescored = len(rows)
    await s.commit()
    return {"version": version, "rescored": rescored}


# ---------- лента материалов ----------

def item_out(item: RedItem, score: RedScore | None, source: RedSource | None) -> dict:
    return {
        "id": item.id, "source_id": item.source_id,
        "source_title": (source.title or source.identifier) if source else "",
        "source_type": source.type if source else "",
        "title": item.title, "url": item.url,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "fetched_at": item.fetched_at.isoformat(),
        "status": item.status, "digest_flag": item.digest_flag,
        "media_count": len(item.media_urls or []),
        "score": ({
            "significance": score.significance,
            "matched_tags": score.matched_tags or [],
            "rationale": score.rationale,
            "digest_worthy": score.digest_worthy,
            "category": score.category,
            "headline_ru": score.headline_ru,
            "summary_ru": score.summary_ru,
            "model": score.model,
            "guideline_version": score.guideline_version,
        } if score else None),
    }


def _latest_score_subq():
    return (select(RedScore.item_id, func.max(RedScore.id).label("sid"))
            .group_by(RedScore.item_id).subquery())


@router.get("/items")
async def list_items(user: User = Depends(get_current_user),
                     s: AsyncSession = Depends(get_session),
                     significance: str = Query("", description="напр. gte:4"),
                     hours: int = Query(72), status: str = Query(""),
                     limit: int = Query(50, le=200)):
    cutoff = utcnow() - timedelta(hours=min(hours, 168))
    sub = _latest_score_subq()
    q = (select(RedItem, RedScore, RedSource)
         .join(sub, sub.c.item_id == RedItem.id)
         .join(RedScore, RedScore.id == sub.c.sid)
         .join(RedSource, RedSource.id == RedItem.source_id)
         .where(RedItem.user_id == user.id, RedItem.fetched_at >= cutoff)
         .order_by(RedItem.fetched_at.desc()).limit(limit))
    if significance.startswith("gte:"):
        try:
            q = q.where(RedScore.significance >= float(significance[4:]))
        except ValueError:
            pass
    if status:
        q = q.where(RedItem.status == status)
    rows = (await s.execute(q)).all()
    return {"list": [item_out(r[0], r[1], r[2]) for r in rows]}


@router.get("/items/{item_id}")
async def get_item(item_id: int, user: User = Depends(get_current_user),
                   s: AsyncSession = Depends(get_session)):
    item = (await s.execute(select(RedItem).where(
        RedItem.id == item_id, RedItem.user_id == user.id))).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Материал не найден")
    score = (await s.execute(select(RedScore).where(RedScore.item_id == item.id)
                             .order_by(RedScore.id.desc()).limit(1))).scalars().first()
    source = await s.get(RedSource, item.source_id)
    feedback = (await s.execute(select(RedFeedback).where(
        RedFeedback.item_id == item.id).limit(5))).scalars().all()
    out = item_out(item, score, source)
    out["feedback"] = [{"action": f.action, "note": f.note} for f in feedback]
    return out


@router.post("/items/{item_id}/digest")
async def flag_digest(item_id: int, user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    """«📅 В дайджест» из веб-интерфейса — гарантированное место (FR-3.3 таблица)."""
    item = (await s.execute(select(RedItem).where(
        RedItem.id == item_id, RedItem.user_id == user.id))).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Материал не найден")
    item.digest_flag = True
    item.status = "digest"
    await s.commit()
    return {"success": True}


@router.post("/items/{item_id}/ignore")
async def ignore_item(item_id: int, user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    """«🗑 Игнор» из веб-интерфейса: блок-лист кластера на 30 дней."""
    item = (await s.execute(select(RedItem).where(
        RedItem.id == item_id, RedItem.user_id == user.id))).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Материал не найден")
    from .models import RedBlocklist
    s.add(RedBlocklist(user_id=user.id, content_hash=item.content_hash,
                       until=utcnow() + timedelta(days=30)))
    item.status = "ignored"
    await s.commit()
    return {"success": True}


# ---------- дайджесты ----------

def digest_out(dg: RedDigest) -> dict:
    return {
        "id": dg.id, "dtype": dg.dtype,
        "period_start": dg.period_start.isoformat() if dg.period_start else None,
        "period_end": dg.period_end.isoformat() if dg.period_end else None,
        "draft_text": dg.draft_text, "draft_version": dg.draft_version,
        "status": dg.status, "post_id": dg.post_id,
        "published_at": dg.published_at.isoformat() if dg.published_at else None,
        "created_at": dg.created_at.isoformat(),
    }


@router.get("/digests")
async def list_digests(user: User = Depends(get_current_user),
                       s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(RedDigest).where(RedDigest.user_id == user.id)
                            .order_by(RedDigest.id.desc()).limit(30))).scalars().all()
    return {"list": [digest_out(d) for d in rows]}


@router.post("/digests/{digest_id}/approve")
async def approve_digest(digest_id: int, user: User = Depends(get_current_user),
                         s: AsyncSession = Depends(get_session)):
    dg = (await s.execute(select(RedDigest).where(
        RedDigest.id == digest_id, RedDigest.user_id == user.id))).scalar_one_or_none()
    if not dg:
        raise HTTPException(404, "Дайджест не найден")
    if dg.status != "draft":
        raise HTTPException(400, f"Дайджест уже обработан ({dg.status})")
    try:
        await digest_service.publish_digest(s, dg)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await s.commit()
    return digest_out(dg)


@router.post("/digests/{digest_id}/reject")
async def reject_digest(digest_id: int, user: User = Depends(get_current_user),
                        s: AsyncSession = Depends(get_session)):
    dg = (await s.execute(select(RedDigest).where(
        RedDigest.id == digest_id, RedDigest.user_id == user.id))).scalar_one_or_none()
    if not dg:
        raise HTTPException(404, "Дайджест не найден")
    if dg.status != "draft":
        raise HTTPException(400, f"Дайджест уже обработан ({dg.status})")
    dg.status = "rejected"
    await s.commit()
    return digest_out(dg)


@router.put("/digests/{digest_id}/text")
async def edit_digest_text(digest_id: int, payload: RedDigestTextIn,
                           user: User = Depends(get_current_user),
                           s: AsyncSession = Depends(get_session)):
    """Редактирование черновика: версия инкрементируется (FR-4.12)."""
    dg = (await s.execute(select(RedDigest).where(
        RedDigest.id == digest_id, RedDigest.user_id == user.id))).scalar_one_or_none()
    if not dg:
        raise HTTPException(404, "Дайджест не найден")
    if dg.status != "draft":
        raise HTTPException(400, f"Редактирование доступно только черновика ({dg.status})")
    dg.draft_text = payload.draft_text
    dg.draft_version += 1
    await s.commit()
    return digest_out(dg)


# ---------- настройки редакции ----------

@router.get("/settings")
async def get_red_settings(user: User = Depends(get_current_user),
                           s: AsyncSession = Depends(get_session)):
    cfg = await notify_service.get_settings(s, user.id)
    return {
        "red_channel_page_ids": cfg.get("red_channel_page_ids") or [],
        "red_auto_publish": bool(cfg.get("red_auto_publish")),
    }


@router.post("/settings")
async def save_red_settings(payload: dict, user: User = Depends(get_current_user),
                            s: AsyncSession = Depends(get_session)):
    """Куда публиковать алерты/дайджесты (id страниц) и авто-публикация (FR-4.14)."""
    cfg = await notify_service.get_settings(s, user.id)
    cfg = dict(cfg)
    if "red_channel_page_ids" in payload:
        cfg["red_channel_page_ids"] = [int(p) for p in payload["red_channel_page_ids"]]
    if "red_auto_publish" in payload:
        cfg["red_auto_publish"] = bool(payload["red_auto_publish"])
    await notify_service.save_settings(s, user.id, cfg)
    await s.commit()
    return {"ok": True}


# ---------- статистика и здоровье ----------

@router.get("/stats/daily")
async def daily_stats(user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session),
                      days: int = Query(14, le=90)):
    rows = (await s.execute(select(RedDailyStat).where(
        RedDailyStat.user_id == user.id,
        RedDailyStat.tag == "",
        RedDailyStat.day >= (utcnow() - timedelta(days=days)).strftime("%Y-%m-%d"))
        .order_by(RedDailyStat.day.desc()))).scalars().all()
    return {"list": [{
        "day": r.day, "source_id": r.source_id, "items_count": r.items_count,
        "alerts_count": r.alerts_count, "posts_count": r.posts_count,
        "avg_significance": r.avg_significance} for r in rows]}


@router.get("/health")
async def red_health(user: User = Depends(get_current_user),
                     s: AsyncSession = Depends(get_session)):
    """Сводка пайплайна редакции: источники по статусам, алерты, бюджет LLM."""
    by_status = dict((await s.execute(
        select(RedSource.status, func.count(RedSource.id))
        .where(RedSource.user_id == user.id).group_by(RedSource.status))).all())
    alerts_24h = int((await s.execute(
        select(func.count(RedAlert.id)).where(
            RedAlert.user_id == user.id,
            RedAlert.sent_at >= utcnow() - timedelta(hours=24)))).scalar() or 0)
    queue = int((await s.execute(
        select(func.count(RedItem.id)).where(
            RedItem.user_id == user.id, RedItem.status == "new"))).scalar() or 0)
    return {
        "sources": by_status,
        "alerts_24h": alerts_24h,
        "scoring_queue": queue,
        "llm_budget_left": await llm.budget_left(s, user.id),
        "llm_providers": llm.providers_summary(),
        "llm_personal": bool((user.llm_config or {}).get("base_url")
                             and (user.llm_config or {}).get("api_key")),
        "guideline": await scoring.active_guideline(s, user.id) is not None,
    }
