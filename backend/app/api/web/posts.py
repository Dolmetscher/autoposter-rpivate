from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...config import settings
from ...db import get_session
from ...deps import get_current_user
from ...integrations.base import AdapterError
from ...models import MediaFile, Post, PostAttachment, PostTarget, PostText, PublicationLog, SocialPage, User
from ...schemas import PostIn, PostOut, PostTargetOut, parse_publication_date
from ...services import publish as publish_service
from ...util import utcnow

router = APIRouter(prefix="/posts", tags=["posts"])


def post_to_out(post: Post, full: bool = False) -> dict:
    texts = [{"source_id": t.source_id, "text": t.text} for t in post.texts]
    media_ids = [a.media_file_id for a in sorted(post.attachments, key=lambda x: x.position)
                 if a.media_file.type in ("photo", "video")]
    doc_ids = [a.media_file_id for a in sorted(post.attachments, key=lambda x: x.position)
               if a.media_file.type == "document"]
    targets = []
    for t in post.targets:
        d = PostTargetOut.model_validate(t).model_dump()
        d["page_name"] = t.page.social_page_name
        targets.append(d)
    return {
        "id": post.id, "status": post.status,
        "publication_when_type": post.publication_when_type,
        "publication_date": post.publication_date.isoformat() if post.publication_date else None,
        "project_id": post.project_id, "schedule_id": post.schedule_id,
        "settings": post.settings, "repeat_type": post.repeat_type,
        "created_at": post.created_at.isoformat(),
        "texts": texts, "media_ids": media_ids, "document_ids": doc_ids,
        "targets": targets,
    }


async def load_post(s: AsyncSession, user: User, post_id: int) -> Post:
    post = (await s.execute(
        select(Post).options(selectinload(Post.texts),
                             selectinload(Post.attachments).selectinload(PostAttachment.media_file),
                             selectinload(Post.targets).selectinload(PostTarget.page))
        .where(Post.id == post_id, Post.user_id == user.id))).scalar_one_or_none()
    if not post:
        raise HTTPException(404, "Пост не найден")
    return post


@router.get("")
async def list_posts(status: str | None = None, source_id: int | None = None,
                     date_from: str | None = None, date_to: str | None = None,
                     project_id: int | None = None, schedule_id: int | None = None,
                     page: int = 1, limit: int = 50,
                     user: User = Depends(get_current_user),
                     s: AsyncSession = Depends(get_session)):
    q = select(Post).options(selectinload(Post.texts),
                             selectinload(Post.attachments).selectinload(PostAttachment.media_file),
                             selectinload(Post.targets).selectinload(PostTarget.page))
    count_q = select(Post.id).where(Post.user_id == user.id)
    conds = [Post.user_id == user.id]
    if status:
        conds.append(Post.status == status)
    if project_id:
        conds.append(Post.project_id == project_id)
    if schedule_id:
        conds.append(Post.schedule_id == schedule_id)
    if date_from:
        conds.append(Post.publication_date >= parse_publication_date(date_from))
    if date_to:
        d = parse_publication_date(date_to)
        conds.append(Post.publication_date < d + timedelta(days=1))
    if source_id:
        t_q = select(PostTarget.post_id).where(PostTarget.source_id == source_id)
        ids = (await s.execute(t_q)).scalars().all()
        conds.append(Post.id.in_(ids or [0]))
    for c in conds:
        q = q.where(c)
        count_q = count_q.where(c)
    total = len((await s.execute(count_q)).all())
    rows = (await s.execute(q.order_by(Post.publication_date.is_(None), Post.publication_date,
                                       Post.id.desc())
                            .offset((page - 1) * limit).limit(limit))).scalars().all()
    return {"list": [post_to_out(p) for p in rows], "total_rows": total, "rows_limit": limit}


@router.get("/{post_id}")
async def get_post(post_id: int, user: User = Depends(get_current_user),
                   s: AsyncSession = Depends(get_session)):
    post = await load_post(s, user, post_id)
    return post_to_out(post, full=True)


@router.post("")
async def create_post(payload: PostIn, user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    try:
        pub_date = parse_publication_date(payload.publication_date) if payload.publication_date else None
        if payload.publication_when_type == 2 and not pub_date:
            raise HTTPException(400, "Укажите дату публикации")
        post = await publish_service.create_post(
            s, user, texts=[t.model_dump() for t in payload.texts],
            media_ids=payload.media_ids, document_ids=payload.document_ids,
            settings=payload.settings, page_ids=payload.page_ids,
            when_type=payload.publication_when_type, publication_date=pub_date,
            schedule_id=payload.schedule_id, project_id=payload.project_id,
            status=payload.status if payload.status == "draft" else None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if payload.status == "draft":
        await s.commit()
        return post_to_out(await load_post(s, user, post.id))
    if payload.publication_when_type != 1:
        # отложенная публикация / по расписанию — заберёт планировщик
        await s.commit()
        return post_to_out(await load_post(s, user, post.id))
    if settings.scheduler_mode == "celery":
        from ...celery_app import publish_post_task
        await s.commit()
        publish_post_task.delay(post.id)
        return post_to_out(await load_post(s, user, post.id))
    try:
        await publish_service.publish_post_now(s, post)
        await s.commit()
    except AdapterError as e:
        await s.commit()
        raise HTTPException(400, str(e))
    return post_to_out(await load_post(s, user, post.id))


@router.put("/{post_id}")
async def update_post(post_id: int, payload: PostIn,
                      user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    post = await load_post(s, user, post_id)
    if post.status in ("publishing",):
        raise HTTPException(400, "Пост сейчас публикуется")
    # удаляем опубликованные таргеты из пересборки — их не трогаем
    published = {t.page_id for t in post.targets if t.status in ("published", "deleted")}
    keep_targets = [t for t in post.targets if t.status in ("published", "deleted")]

    try:
        pub_date = parse_publication_date(payload.publication_date) if payload.publication_date else None
        post.publication_when_type = payload.publication_when_type
        post.settings = payload.settings
        post.project_id = payload.project_id
        post.schedule_id = payload.schedule_id
        post.repeat_type = int(payload.settings.get("repeat_type") or 0)
        post.publication_date = pub_date
        if payload.publication_when_type == 1 and payload.status != "draft":
            post.publication_date = utcnow()

        await s.execute(PostText.__table__.delete().where(PostText.post_id == post.id))
        for t in payload.texts:
            if t.text:
                s.add(PostText(post_id=post.id, source_id=t.source_id, text=t.text))

        await s.execute(PostAttachment.__table__.delete().where(PostAttachment.post_id == post.id))
        pos = 0
        for mid in payload.media_ids + payload.document_ids:
            mf = await s.get(MediaFile, mid)
            if not mf or mf.user_id != user.id:
                raise HTTPException(400, f"Файл {mid} не найден")
            s.add(PostAttachment(post_id=post.id, media_file_id=mid, position=pos))
            pos += 1

        for t in list(post.targets):
            if t not in keep_targets:
                await s.delete(t)
        existing_pages = published | {t.page_id for t in keep_targets}
        for pid in payload.page_ids:
            if pid in existing_pages:
                continue
            page = (await s.execute(select(SocialPage).where(
                SocialPage.id == pid, SocialPage.user_id == user.id))).scalar_one_or_none()
            if not page:
                raise HTTPException(404, f"Страница {pid} не найдена")
            s.add(PostTarget(post_id=post.id, page_id=pid, source_id=page.source_id))

        if payload.status == "draft":
            post.status = "draft" if not keep_targets else post.status
        await s.flush()
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))

    if payload.status != "draft" and (payload.publication_when_type == 1 or
                                      (post.publication_date and post.publication_date <= utcnow())):
        if settings.scheduler_mode == "celery":
            from ...celery_app import publish_post_task
            await s.commit()
            publish_post_task.delay(post.id)
        else:
            await publish_service.publish_post_now(s, post)
    elif payload.publication_date or payload.publication_when_type == 2:
        post.status = "scheduled"
    await s.commit()
    return post_to_out(await load_post(s, user, post.id))


@router.post("/{post_id}/publish")
async def publish_now(post_id: int, user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    post = await load_post(s, user, post_id)
    if post.status == "publishing":
        raise HTTPException(400, "Пост сейчас публикуется")
    post.status = "publishing"
    await publish_service.publish_post_now(s, post)
    await s.commit()
    return post_to_out(await load_post(s, user, post.id))


@router.delete("/{post_id}")
async def delete_post(post_id: int, user: User = Depends(get_current_user),
                      s: AsyncSession = Depends(get_session)):
    post = await load_post(s, user, post_id)
    await s.delete(post)
    await s.commit()
    return {"success": True}
