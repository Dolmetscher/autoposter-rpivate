"""Конвейер публикации: создание постов, расписание, повтор, автоудаление."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..integrations.base import AdapterError, PublishContent
from ..integrations.registry import get_adapter
from ..models import (MediaFile, Post, PostAttachment, PostTarget, PostText,
                      PublicationLog, Schedule, SocialAccount, SocialPage, User, Project)
from ..schemas import next_schedule_slot
from ..util import utcnow

log = logging.getLogger("publish")


def add_log(s: AsyncSession, user_id: int, message: str, level: str = "info",
            post_id: int | None = None, target_id: int | None = None,
            source_id: int | None = None) -> None:
    s.add(PublicationLog(user_id=user_id, post_id=post_id, target_id=target_id,
                         source_id=source_id, level=level, message=message[:2000]))


async def create_post(
    s: AsyncSession, user: User, *,
    texts: list[dict], media_ids: list[str] | None = None,
    document_ids: list[str] | None = None, settings: dict | None = None,
    page_ids: list[int] | None = None, when_type: int = 1,
    publication_date=None, schedule_id: int | None = None,
    project_id: int | None = None, status: str | None = None,
    repeat_type: int | None = None,
) -> Post:
    settings = dict(settings or {})
    if repeat_type is None:
        repeat_type = int(settings.get("repeat_type") or 0)
    media_ids = media_ids or []
    document_ids = document_ids or []
    page_ids = page_ids or []

    # Пост с project_id публикуется и на страницы аккаунтов проекта.
    # Жадная загрузка: обращение к lazy-relationship в async-сессии
    # вне flush роняет запрос с MissingGreenlet.
    if project_id is not None:
        project = (await s.execute(
            select(Project)
            .options(selectinload(Project.accounts).selectinload(SocialAccount.pages))
            .where(Project.id == project_id, Project.user_id == user.id)
        )).scalar_one_or_none()
        if project is None:
            raise ValueError(f"Project with id {project_id} not found")
        # Страницы аккаунтов проекта (только те, куда можно публиковать)
        project_page_ids = {
            page.id
            for account in project.accounts
            for page in account.pages
            if page.can_post
        }
        # Объединяем с явно выбранными страницами
        page_ids = list({*page_ids, *project_page_ids})

    post = Post(
        user_id=user.id,
        publication_when_type=when_type,
        schedule_id=schedule_id,
        project_id=project_id,
        settings=settings,
        repeat_type=repeat_type,
    )

    if when_type == 2:
        post.publication_date = publication_date
        post.status = status or "scheduled"
    elif when_type == 3:
        post.schedule_id = schedule_id
        post.publication_date = publication_date
        post.status = status or ("scheduled" if publication_date else "draft")
    elif when_type == 1:
        post.status = status or "scheduled"
        post.publication_date = publication_date or (None if status == "draft" else utcnow())
    else:
        post.status = status or "draft"

    # страницы
    pages: list[SocialPage] = []
    if page_ids:
        pages = (await s.execute(
            select(SocialPage).where(SocialPage.user_id == user.id, SocialPage.id.in_(page_ids))
        )).scalars().all()
        if len(pages) != len(set(page_ids)):
            found = {p.id for p in pages}
            missing = [pid for pid in page_ids if pid not in found]
            raise ValueError(f"Страницы не найдены: {missing}")

    # вложения
    att_ids = list(media_ids) + list(document_ids)
    if not any(t.get("text") for t in (texts or [])) and not att_ids:
        raise ValueError("Пустой пост: добавьте текст или вложения")
    files_map: dict[str, MediaFile] = {}
    if att_ids:
        rows = (await s.execute(
            select(MediaFile).where(MediaFile.user_id == user.id, MediaFile.id.in_(att_ids))
        )).scalars().all()
        files_map = {m.id: m for m in rows}
        missing = [mid for mid in att_ids if mid not in files_map]
        if missing:
            raise ValueError(f"Медиафайлы не найдены: {missing}")

    post.texts = [PostText(source_id=int(t.get("source_id") or 0), text=t["text"])
                  for t in texts or [] if t.get("text")]
    post.attachments = [
        PostAttachment(media_file_id=mid, media_file=files_map[mid], position=pos)
        for pos, mid in enumerate(att_ids)
    ]
    post.targets = [PostTarget(page=p, page_id=p.id, source_id=p.source_id) for p in pages]

    # расписания без даты: вычисляем ближайший слот
    if post.publication_date is None and post.schedule_id and when_type == 3:
        sched = await s.get(Schedule, post.schedule_id)
        if sched and sched.slots:
            post.publication_date = next_schedule_slot(sched.slots, user.timezone)

    s.add(post)
    await s.flush()
    return post


def resolve_text(post: Post, source_id: int) -> str:
    specific = next((t.text for t in post.texts if t.source_id == source_id and t.text), "")
    if specific:
        return specific
    return next((t.text for t in post.texts if t.source_id == 0), "")


def build_content(post: Post, source_id: int) -> PublishContent:
    media = [a.media_file for a in sorted(post.attachments, key=lambda a: a.position)
             if a.media_file.type in ("photo", "video")]
    documents = [a.media_file for a in post.attachments if a.media_file.type == "document"]
    return PublishContent(
        text=resolve_text(post, source_id),
        media=media,
        documents=documents,
        settings=post.settings or {},
        media_base_url=settings.public_base_url,
    )


async def publish_target(s: AsyncSession, post: Post, target: PostTarget) -> None:
    page = target.page
    if page is None:
        page = await s.get(SocialPage, target.page_id)
        target.page = page
    # s.get берёт объект из identity map — ленивой загрузки нет
    page.account = await s.get(SocialAccount, page.account_id)
    adapter = get_adapter(target.source_id)
    target.status = "publishing"
    target.attempts += 1
    await s.flush()
    try:
        content = build_content(post, target.source_id)
        if not adapter.handles_link:
            # Сеть не умеет публиковать ссылку сама — дописываем её в текст,
            # тогда превью (Open Graph) развернёт сама сеть.
            content.text = content.text_with_link()
        outcome = await adapter.publish(page, content)
    except AdapterError as e:
        outcome = type("Outcome", (), {"ok": False, "external_id": "", "external_url": "",
                                       "error": str(e)})
    except Exception as e:  # непредвиденная ошибка сети/кода адаптера
        log.exception("adapter crash")
        outcome = type("Outcome", (), {"ok": False, "external_id": "", "external_url": "",
                                       "error": f"Ошибка адаптера: {e}"})

    if outcome.ok:
        target.status = "published"
        target.external_id = outcome.external_id
        target.external_url = outcome.external_url
        target.published_at = utcnow()
        target.error = ""
        st = post.settings or {}
        days = int(st.get("delete_post_day") or 0)
        hours = int(st.get("delete_post_hour") or 0)
        if days or hours:
            target.delete_after_at = target.published_at + timedelta(days=days, hours=hours)
        add_log(s, post.user_id, f"Опубликовано в «{page.social_page_name}»: {outcome.external_url}",
                "success", post_id=post.id, target_id=target.id, source_id=target.source_id)
    else:
        target.status = "failed"
        target.error = outcome.error or "Неизвестная ошибка"
        add_log(s, post.user_id,
                f"Ошибка публикации в «{page.social_page_name}»: {target.error}",
                "error", post_id=post.id, target_id=target.id, source_id=target.source_id)
    await s.flush()


def finalize_post_status(post: Post) -> None:
    if not post.targets:
        post.status = "draft"
        return
    states = {t.status for t in post.targets}
    if states == {"published"}:
        post.status = "published"
    elif "published" in states:
        post.status = "partial"
    else:
        post.status = "failed"


def next_repeat_date(post: Post, user_tz: str) -> None:
    if post.repeat_type == 1:
        post.publication_date = (post.publication_date or utcnow()) + timedelta(days=1)
    elif post.repeat_type == 2:
        post.publication_date = (post.publication_date or utcnow()) + timedelta(days=7)
    elif post.repeat_type == 3:
        d = post.publication_date or utcnow()
        month = d.month + 1 if d.month < 12 else 1
        year = d.year + (1 if d.month == 12 else 0)
        day = min(d.day, 28)
        post.publication_date = d.replace(year=year, month=month, day=day)
    else:
        return
    post.status = "scheduled"
    for t in post.targets:
        t.status = "pending"
        t.external_id = ""
        t.external_url = ""
        t.published_at = None
        t.delete_after_at = None
        t.attempts = 0


async def load_post_for_publish(s: AsyncSession, post_id: int) -> Post | None:
    """Пост со всеми связями, нужными для публикации.

    Связи подгружаем явно: ленивая загрузка relationship в async-сессии
    вне flush падает с MissingGreenlet."""
    return (await s.execute(
        select(Post).options(
            selectinload(Post.targets).selectinload(PostTarget.page),
            selectinload(Post.texts),
            selectinload(Post.attachments).selectinload(PostAttachment.media_file),
        ).where(Post.id == post_id))).scalar_one_or_none()


async def publish_post_now(s: AsyncSession, post: Post) -> Post:
    post.status = "publishing"
    await s.flush()
    for target in post.targets:
        if target.status in ("published", "deleted"):
            continue
        await publish_target(s, post, target)
    finalize_post_status(post)
    await s.flush()
    try:
        from . import notify
        await notify.notify_publish_result(s, post)
    except Exception:
        log.warning("notify hook failed", exc_info=True)
    return post


async def run_due_posts(s: AsyncSession, now=None) -> int:
    """Публикация всех постов, срок которых наступил. Возвращает кол-во обработанных."""
    now = now or utcnow()
    due = (await s.execute(
        select(Post).options(selectinload(Post.targets).selectinload(PostTarget.page),
                             selectinload(Post.texts), selectinload(Post.attachments)
                             .selectinload(PostAttachment.media_file))
        .where(Post.status == "scheduled", Post.publication_date.is_not(None),
               Post.publication_date <= now)
        .order_by(Post.publication_date)
        .limit(50)
    )).scalars().all()

    processed = 0
    for post in due:
        await publish_post_now(s, post)
        if post.status in ("published", "partial") and post.repeat_type:
            user = await s.get(User, post.user_id)
            if post.schedule_id and post.publication_when_type == 3:
                sched = await s.get(Schedule, post.schedule_id)
                if sched and sched.slots:
                    post.publication_date = next_schedule_slot(sched.slots, user.timezone,
                                                               from_dt=utcnow())
                    post.status = "scheduled"
                    for t in post.targets:
                        t.status = "pending"
                        t.external_id = ""
                        t.external_url = ""
                        t.published_at = None
                        t.delete_after_at = None
                        t.attempts = 0
                else:
                    next_repeat_date(post, user.timezone)
            else:
                next_repeat_date(post, user.timezone)
        processed += 1
    if processed:
        await s.flush()
    return processed


async def process_deletes(s: AsyncSession, now=None) -> int:
    """Автоудаление опубликованных постов (settings.delete_post_day/hour)."""
    now = now or utcnow()
    targets = (await s.execute(
        select(PostTarget).options(selectinload(PostTarget.post), selectinload(PostTarget.page))
        .where(PostTarget.status == "published",
               PostTarget.delete_after_at.is_not(None),
               PostTarget.delete_after_at <= now)
        .limit(50)
    )).scalars().all()
    count = 0
    for target in targets:
        target.page.account = await s.get(SocialAccount, target.page.account_id)
        adapter = get_adapter(target.source_id)
        try:
            ok = await adapter.delete(target.page, target.external_id)
        except AdapterError as e:
            ok = False
            add_log(s, target.post.user_id,
                    f"Автоудаление не удалось ({target.page.social_page_name}): {e}",
                    "error", post_id=target.post_id, target_id=target.id)
        if ok:
            target.status = "deleted"
            add_log(s, target.post.user_id,
                    f"Пост удалён по таймеру из «{target.page.social_page_name}»",
                    "info", post_id=target.post_id, target_id=target.id)
            count += 1
    if count:
        await s.flush()
    return count
