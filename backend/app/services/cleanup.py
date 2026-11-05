"""Агрессивная экономия диска: тяжёлые медиафайлы не копятся в хранилище.

Два механизма:

1. Немедленный: когда пост полностью опубликован (все цели отработали),
   файлы его вложений стираются с диска — сети уже забрали медиа.
   Запись о посте и тексты остаются в базе (см. purge_post_files, вызов
   в publish.publish_post_now). Повторные посты файлы сохраняют: они
   понадобятся при следующей публикации.

2. Фоновый: раз в сутки из тика планировщика (inline и celery — оба идут
   через scheduler.tick_once) удаляются файлы старше media_retention_days:
   неприкреплённые ни к чему, а также прикреплённые только к черновикам
   и ошибочным постам старше retention. Файлы запланированных, публикуемых
   и повторных постов, а также шаблонов не трогаются никогда.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AppState, MediaFile, Post, PostAttachment, PostTemplate
from ..util import utcnow
from . import storage

log = logging.getLogger("cleanup")

_CLEANUP_INTERVAL_HOURS = 20
_BATCH = 5000

# Статусы постов, чьи файлы можно удалять по истечении retention:
# публикация не состоялась и, скорее всего, уже не потребуется медиа.
_PURGEABLE_POST_STATUSES = ("draft", "failed", "partial")


async def cleanup_if_due(s: AsyncSession, now=None) -> int:
    """Гейт частоты: реальная очистка не чаще раза в сутки."""
    now = now or utcnow()
    if settings.media_retention_days <= 0:
        return 0
    state = await s.get(AppState, "cleanup_run")
    if state and state.value.get("ts"):
        try:
            if now - datetime.fromisoformat(state.value["ts"]) < timedelta(
                    hours=_CLEANUP_INTERVAL_HOURS):
                return 0
        except ValueError:
            pass
    count = await run_cleanup(s, now)
    if state is not None:
        state.value = {"ts": now.isoformat()}
        state.updated_at = now
    else:
        s.add(AppState(key="cleanup_run", value={"ts": now.isoformat()}, updated_at=now))
    await s.flush()
    return count


async def run_cleanup(s: AsyncSession, now=None) -> int:
    """Удаляет старые файлы: неприкреплённые и файлы черновиков/ошибок."""
    now = now or utcnow()
    cutoff = now - timedelta(days=settings.media_retention_days)
    cutoff_ts = int(cutoff.timestamp())

    template_ids: set[str] = set()
    for ids in (await s.execute(select(PostTemplate.media_ids))).scalars().all():
        template_ids.update(ids or [])

    # посты, владеющие файлами: media_file_id → [(статус поста, возрастная дата)]
    post_refs: dict[str, list[tuple[str, datetime | None]]] = {}
    for mid, status, pub, created in (await s.execute(
            select(PostAttachment.media_file_id, Post.status,
                   Post.publication_date, Post.created_at)
            .join(Post, Post.id == PostAttachment.post_id))).all():
        post_refs.setdefault(mid, []).append((status, pub or created))

    rows = (await s.execute(
        select(MediaFile).order_by(MediaFile.updated_date).limit(_BATCH))).scalars().all()
    deleted = 0
    for mf in rows:
        if mf.id in template_ids:
            continue
        refs = post_refs.get(mf.id)
        if refs is None:
            # неприкреплённый файл — возраст берём из updated_date файла
            try:
                age_ok = int(mf.updated_date) < cutoff_ts
            except (TypeError, ValueError):
                age_ok = False  # неизвестен возраст — не трогаем
            if not age_ok:
                continue
        else:
            # файл прикреплён к постам: удаляем, только если ВСЕ посты-
            # владельцы — черновики/ошибки старше retention. Запланированные,
            # публикуемые и повторные посты ещё потребуют медиа.
            if not all(st in _PURGEABLE_POST_STATUSES and age is not None and age < cutoff
                       for st, age in refs):
                continue
        deleted += await _remove_media(s, mf)
    await s.flush()
    if deleted:
        log.info("Очистка хранилища: удалено %s файлов старше %s дн.",
                 deleted, settings.media_retention_days)
    return deleted


async def _remove_media(s: AsyncSession, mf: MediaFile) -> int:
    """Стирает файл и превью с диска и удаляет запись MediaFile."""
    for rel in dict.fromkeys((mf.file_path, mf.file_thumbnail_path)):
        if not rel:
            continue
        try:
            await storage.delete_file(rel)
        except OSError:
            log.warning("Не удалось удалить файл %s", rel)
    await s.delete(mf)
    return 1


async def purge_post_files(s: AsyncSession, post: Post) -> tuple[int, int]:
    """Файлы полностью опубликованного поста: стереть с диска.

    Запись о посте, тексты и цели остаются; вложения и MediaFile удаляются.
    Файл не трогается, пока он нужен другому посту или шаблону.
    Возвращает (кол-во удалённых файлов, освобождено байт)."""
    ids = {a.media_file_id for a in post.attachments}
    if not ids:
        return 0, 0
    files = {mf.id: mf for mf in (await s.execute(
        select(MediaFile).where(MediaFile.id.in_(ids)))).scalars().all()}
    protected: set[str] = set((await s.execute(
        select(PostAttachment.media_file_id).where(
            PostAttachment.media_file_id.in_(ids),
            PostAttachment.post_id != post.id))).scalars().all())
    for tids in (await s.execute(select(PostTemplate.media_ids))).scalars().all():
        protected.update(set(tids or []) & ids)

    freed = 0
    purged: list[MediaFile] = []
    seen: set[str] = set()
    for att in list(post.attachments):
        mid = att.media_file_id
        if mid in protected or mid in seen or mid not in files:
            continue
        seen.add(mid)
        mf = files[mid]
        for rel in dict.fromkeys((mf.file_path, mf.file_thumbnail_path)):
            if not rel:
                continue
            try:
                await storage.delete_file(rel)
            except OSError:
                log.warning("Не удалось удалить файл %s", rel)
        freed += max(mf.size or 0, 0)
        purged.append(mf)
    for mf in purged:
        # удаляем из коллекции: каскад delete-orphan уберёт строку вложения,
        # а пост в памяти останется консистентным (без «старых» media_ids)
        for att in [a for a in post.attachments if a.media_file_id == mf.id]:
            post.attachments.remove(att)
        await s.delete(mf)
    if purged:
        await s.flush()
    if purged:
        log.info("Пост %s: удалено файлов %s, освобождено %s байт",
                 post.id, len(purged), freed)
    return len(purged), freed
