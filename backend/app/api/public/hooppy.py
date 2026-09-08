"""Публичный API hooppy-формы. Авторизация: Authorization: Bearer <api_token>.
Пути: /accounts, /accounts/pages, /posts, /posts/projects, /posts/schedules,
/files/media/upload, /files/documents/upload, /posts/{id}, /posts/batch/delete."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...deps import get_session, get_token_user
from ...models import Post, Project, Schedule, SocialAccount, SocialPage, User
from ...schemas import parse_publication_date
from ...services import files as files_service
from ...services import publish as publish_service
from ...util import utcnow

router = APIRouter(tags=["public-api"])


def envelope(items: list, total: int, page: int, rows_limit: int) -> dict:
    return {
        "list": items,
        "total_rows": total,
        "is_has_more": page * rows_limit < total,
        "rows_limit": rows_limit,
    }


# ---------- accounts ----------

@router.get("/accounts")
async def accounts_list(source_id: int | None = None, page: int = 1,
                        user: User = Depends(get_token_user),
                        s: AsyncSession = Depends(get_session)):
    q = select(SocialAccount).where(SocialAccount.user_id == user.id)
    count_q = select(func.count(SocialAccount.id)).where(SocialAccount.user_id == user.id)
    if source_id is not None:
        q = q.where(SocialAccount.source_id == source_id)
        count_q = count_q.where(SocialAccount.source_id == source_id)
    total = (await s.execute(count_q)).scalar_one()
    rows = (await s.execute(q.order_by(SocialAccount.id).offset((page - 1) * 20).limit(20))).scalars().all()
    return envelope([{
        "id": a.id,
        "source_id": a.source_id,
        "social_account_id": a.social_account_id,
        "social_account_name": a.social_account_name,
        "social_account_photo": a.social_account_photo,
    } for a in rows], total, page, 20)


@router.get("/accounts/pages")
async def pages_list(source_id: int | None = None, account_id: int | None = None, page: int = 1,
                     user: User = Depends(get_token_user),
                     s: AsyncSession = Depends(get_session)):
    q = select(SocialPage).where(SocialPage.user_id == user.id)
    count_q = select(func.count(SocialPage.id)).where(SocialPage.user_id == user.id)
    if source_id is not None:
        q = q.where(SocialPage.source_id == source_id)
        count_q = count_q.where(SocialPage.source_id == source_id)
    if account_id is not None:
        q = q.where(SocialPage.account_id == account_id)
        count_q = count_q.where(SocialPage.account_id == account_id)
    total = (await s.execute(count_q)).scalar_one()
    rows = (await s.execute(q.order_by(SocialPage.id).offset((page - 1) * 20).limit(20))).scalars().all()
    return envelope([{
        "id": p.id,
        "source_id": p.source_id,
        "account_id": p.account_id,
        "social_page_id": p.social_page_id,
        "social_page_name": p.social_page_name,
        "social_page_photo": p.social_page_photo,
    } for p in rows], total, page, 20)


# ---------- projects / schedules ----------

@router.get("/posts/projects")
async def projects_list(page: int = 1, user: User = Depends(get_token_user),
                        s: AsyncSession = Depends(get_session)):
    total = (await s.execute(select(func.count(Project.id)).where(Project.user_id == user.id))).scalar_one()
    rows = (await s.execute(select(Project).where(Project.user_id == user.id)
                            .order_by(Project.id).offset((page - 1) * 20).limit(20))).scalars().all()
    return envelope([{"id": p.id, "name": p.name} for p in rows], total, page, 20)


@router.get("/posts/schedules")
async def schedules_list(page: int = 1, user: User = Depends(get_token_user),
                         s: AsyncSession = Depends(get_session)):
    total = (await s.execute(select(func.count(Schedule.id)).where(Schedule.user_id == user.id))).scalar_one()
    rows = (await s.execute(select(Schedule).where(Schedule.user_id == user.id)
                            .order_by(Schedule.id).offset((page - 1) * 20).limit(20))).scalars().all()
    return envelope([{"id": p.id, "name": p.name, "slots": p.slots} for p in rows], total, page, 20)


# ---------- files ----------

@router.post("/files/media/upload")
async def upload_media(file: UploadFile = File(...), file_id: str | None = Form(None),
                       user: User = Depends(get_token_user),
                       s: AsyncSession = Depends(get_session)):
    mf = await files_service.save_upload(user.id, file, s, file_id_hint=file_id)
    await s.commit()
    kind = "photo" if mf.type == "photo" else "video"
    data = files_service.media_out(mf)
    return {kind: data}


@router.post("/files/documents/upload")
async def upload_document(file: UploadFile = File(...), file_id: str | None = Form(None),
                          user: User = Depends(get_token_user),
                          s: AsyncSession = Depends(get_session)):
    mf = await files_service.save_upload(user.id, file, s, file_id_hint=file_id,
                                         force_type="document")
    await s.commit()
    return {"document": files_service.media_out(mf)}


# ---------- posts ----------

class PostTextIn(BaseModel):
    text: str = ""
    source_id: int = 0


class PublicPostIn(BaseModel):
    publication_when_type: int = 1
    publication_how_type: int = 1
    publication_date: str | None = None
    selected_pages_ids: list[int] = []
    schedules_ids: list[int] = []
    project_id: int | None = None
    texts: list[PostTextIn] = []
    attachments: list[dict] = []


def _parse_attachments(attachments: list[dict]):
    media_ids: list[str] = []
    doc_ids: list[str] = []
    settings: dict = {}
    for att in attachments:
        t = att.get("type")
        data = att.get("data", [])
        if t in ("photos", "videos", "media"):
            for item in data if isinstance(data, list) else []:
                if item.get("type") == "document":
                    doc_ids.append(item["id"])
                else:
                    media_ids.append(item["id"])
        elif t == "documents":
            for item in data if isinstance(data, list) else []:
                doc_ids.append(item["id"])
        elif t == "settings" and isinstance(data, dict):
            settings = data
    return media_ids, doc_ids, settings


@router.post("/posts")
async def create_post(payload: PublicPostIn, user: User = Depends(get_token_user),
                      s: AsyncSession = Depends(get_session)):
    media_ids, doc_ids, settings = _parse_attachments(payload.attachments)
    page_ids = payload.selected_pages_ids
    when_type = payload.publication_when_type
    pub_date = parse_publication_date(payload.publication_date) if payload.publication_date else None

    if when_type == 3 and payload.schedules_ids:
        # ближайший слот расписания
        scheds = (await s.execute(select(Schedule).where(
            Schedule.user_id == user.id, Schedule.id.in_(payload.schedules_ids)))).scalars().all()
        if not scheds:
            raise HTTPException(400, "Расписания не найдены")
        page_ids = page_ids or [p.id for p in (await s.execute(
            select(SocialPage).where(SocialPage.user_id == user.id))).scalars().all()]
        pub_date = publish_service.next_schedule_slot(scheds, user.timezone)
    elif when_type == 4 and payload.project_id:
        proj = (await s.execute(select(Project).where(
            Project.user_id == user.id, Project.id == payload.project_id))).scalar_one_or_none()
        if not proj:
            raise HTTPException(400, "Проект не найден")
        proj_pages = proj.settings.get("page_ids", [])
        page_ids = page_ids or proj_pages
        if proj.settings.get("interval_minutes"):
            pub_date = utcnow() + __import__("datetime").timedelta(minutes=int(proj.settings["interval_minutes"]))
        when_type = 2 if pub_date else 1

    post = await publish_service.create_post(
        s, user,
        texts=[{"text": t.text, "source_id": t.source_id} for t in payload.texts],
        media_ids=media_ids, document_ids=doc_ids,
        settings=settings, page_ids=page_ids,
        when_type=when_type, publication_date=pub_date,
        schedule_id=(payload.schedules_ids[0] if when_type == 3 and payload.schedules_ids else None),
        project_id=payload.project_id,
    )
    if when_type == 1:
        await publish_service.publish_post_now(s, post)
    await s.commit()
    return {"id": post.id}


@router.get("/posts")
async def posts_list(is_published: int | None = None, publication_date: str | None = None,
                     source_id: int | None = None, account_id: int | None = None,
                     page_id: int | None = None, schedule_id: int | None = None,
                     project_id: int | None = None, page: int = 1,
                     user: User = Depends(get_token_user),
                     s: AsyncSession = Depends(get_session)):
    q = select(Post).where(Post.user_id == user.id)
    count_q = select(func.count(Post.id)).where(Post.user_id == user.id)
    filters = []
    if is_published is not None:
        if is_published == 1:
            filters.append(Post.status.in_(["published", "partial"]))
        else:
            filters.append(Post.status.in_(["draft", "scheduled", "publishing", "failed"]))
    if publication_date:
        try:
            d = datetime.strptime(publication_date, "%d.%m.%Y")
        except ValueError:
            raise HTTPException(400, "publication_date в формате дд.мм.гггг")
        day_start = d.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + __import__("datetime").timedelta(days=1)
        filters.append(Post.publication_date >= day_start)
        filters.append(Post.publication_date < day_end)
    if schedule_id:
        filters.append(Post.schedule_id == schedule_id)
    if project_id:
        filters.append(Post.project_id == project_id)
    if page_id or source_id or account_id:
        t_q = select(PostTarget.post_id)
        if page_id:
            t_q = t_q.where(PostTarget.page_id == page_id)
        if source_id:
            t_q = t_q.where(PostTarget.source_id == source_id)
        if account_id:
            t_q = t_q.join(SocialPage, SocialPage.id == PostTarget.page_id).where(SocialPage.account_id == account_id)
        ids = (await s.execute(t_q)).scalars().all()
        filters.append(Post.id.in_(ids or [0]))
    for f in filters:
        q = q.where(f)
        count_q = count_q.where(f)
    total = (await s.execute(count_q)).scalar_one()
    rows = (await s.execute(
        q.options(selectinload(Post.targets))
        .order_by(Post.id.desc()).offset((page - 1) * 20).limit(20))).scalars().all()
    return envelope([{"id": p.id, "status": p.status,
                      "publication_date": p.publication_date.isoformat() if p.publication_date else None,
                      "targets": [{"source_id": t.source_id, "page_id": t.page_id, "status": t.status,
                                   "external_url": t.external_url} for t in p.targets]}
                     for p in rows], total, page, 20)


@router.delete("/posts/{post_id}")
async def delete_post(post_id: int, user: User = Depends(get_token_user),
                      s: AsyncSession = Depends(get_session)):
    post = (await s.execute(select(Post).where(Post.id == post_id, Post.user_id == user.id))).scalar_one_or_none()
    if not post:
        raise HTTPException(404, "Пост не найден")
    await s.execute(sa_delete(Post).where(Post.id == post.id))
    await s.commit()
    return {"success": True}


class BatchDeleteIn(BaseModel):
    ids: str


@router.post("/posts/batch/delete")
async def batch_delete(payload: BatchDeleteIn, user: User = Depends(get_token_user),
                       s: AsyncSession = Depends(get_session)):
    ids = [int(x) for x in payload.ids.split(",") if x.strip().isdigit()]
    if not ids:
        raise HTTPException(400, "Некорректные ids")
    res = await s.execute(sa_delete(Post).where(Post.user_id == user.id, Post.id.in_(ids)))
    await s.commit()
    return {"success": True, "deleted": res.rowcount}
