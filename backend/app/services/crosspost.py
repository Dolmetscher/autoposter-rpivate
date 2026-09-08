"""Кросспостинг: опрос источников чужого контента, скачивание медиа,
опциональный рерайт и публикация к себе."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations.base import AdapterError, SourceItem
from ..integrations.registry import get_adapter
from ..config import settings
from ..models import (CrosspostSeen, CrosspostSource, MediaFile, SocialAccount,
                      User)
from ..services import files as files_service
from ..services import publish as publish_service
from ..services import rewrite as rewrite_service
from ..util import utcnow

log = logging.getLogger("crosspost")


async def _account_for(session: AsyncSession, user_id: int, source_id: int):
    return (await session.execute(
        select(SocialAccount).where(SocialAccount.user_id == user_id,
                                    SocialAccount.source_id == source_id,
                                    SocialAccount.status == "active")
        .order_by(SocialAccount.id)
    )).scalars().first()


async def poll_due(session: AsyncSession, now=None) -> int:
    now = now or utcnow()
    sources = (await session.execute(
        select(CrosspostSource).where(CrosspostSource.active.is_(True))
    )).scalars().all()
    processed = 0
    for src in sources:
        due_at = (src.last_polled_at + timedelta(minutes=src.poll_interval_min)
                  if src.last_polled_at else now)
        if due_at > now:
            continue
        src.last_polled_at = now
        try:
            await poll_source(session, src)
        except Exception:
            log.exception("crosspost poll failed for source %s", src.id)
        processed += 1
    if processed:
        await session.flush()
    return processed


def _passes_filters(src: CrosspostSource, item: SourceItem) -> bool:
    if src.only_with_media and not item.media:
        return False
    kw = (src.keyword_filter or "").strip()
    if kw:
        words = [w.strip().lower() for w in kw.split(",") if w.strip()]
        text = (item.text or "").lower()
        if words and not any(w in text for w in words):
            return False
    return True


async def _is_seen(session: AsyncSession, src: CrosspostSource, external_id: str) -> bool:
    row = (await session.execute(
        select(CrosspostSeen).where(CrosspostSeen.source_row_id == src.id,
                                    CrosspostSeen.external_id == external_id)
    )).scalar_one_or_none()
    return row is not None


async def poll_source(session: AsyncSession, src: CrosspostSource) -> int:
    user = await session.get(User, src.user_id)
    adapter = get_adapter(src.source_id)
    account = await _account_for(session, src.user_id, src.source_id)
    try:
        items = await adapter.fetch_source_items(src.source_ref,
                                                 limit=settings.crosspost_max_items_per_poll * 2,
                                                 account=account)
    except AdapterError as e:
        publish_service.add_log(session, src.user_id,
                                f"Кросспостинг «{src.name or src.source_ref}»: {e}",
                                "error", source_id=src.source_id)
        return 0

    created = 0
    for item in items:
        if created >= settings.crosspost_max_items_per_poll:
            break
        if await _is_seen(session, src, item.external_id):
            continue
        # помечаем сразу, чтобы дубликат из параллельного тика не создал второй пост
        session.add(CrosspostSeen(source_row_id=src.id, external_id=item.external_id))
        if not _passes_filters(src, item):
            continue
        try:
            created += await _copy_item(session, user, src, item)
        except (ValueError, AdapterError) as e:
            publish_service.add_log(session, src.user_id,
                                    f"Кросспостинг «{src.name or src.source_ref}»: {e}",
                                    "error", source_id=src.source_id)
    await session.flush()
    return created


async def _copy_item(session: AsyncSession, user: User, src: CrosspostSource,
                     item: SourceItem) -> int:
    media_ids: list[str] = []
    for m in item.media:
        kind = m.get("kind", "photo")
        try:
            mf: MediaFile = await files_service.download_from_url(user.id, m["url"], kind, session)
        except ValueError as e:
            publish_service.add_log(session, user.id,
                                    f"Кросспостинг «{src.name or src.source_ref}»: {e}",
                                    "error", source_id=src.source_id)
            continue
        media_ids.append(mf.id)

    text = item.text or ""
    if src.rewrite_enabled and text:
        text, warn = await rewrite_service.rewrite_text(user.llm_config or {}, text)
        if warn:
            publish_service.add_log(session, user.id, f"Кросспостинг «{src.name or src.source_ref}»: {warn}",
                                    "info", source_id=src.source_id)
    if not text and not media_ids:
        return 0

    if src.target_page_ids:
        post = await publish_service.create_post(
            session, user,
            texts=[{"text": text, "source_id": 0}],
            media_ids=media_ids, settings={"crosspost_source_id": src.id},
            page_ids=src.target_page_ids, when_type=1,
        )
        await publish_service.publish_post_now(session, post)
        publish_service.add_log(session, user.id,
                                f"Кросспостинг: скопирован пост {item.external_id} из «{src.name or src.source_ref}»",
                                "info", post_id=post.id, source_id=src.source_id)
        await session.flush()
        return 1
    return 0
