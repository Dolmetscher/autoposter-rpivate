from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...db import get_session
from ...deps import get_current_user
from ...models import Post, PostTarget, PublicationLog, SocialAccount, SocialPage, User

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard(user: User = Depends(get_current_user),
                    s: AsyncSession = Depends(get_session)):
    counts = {
        "accounts": (await s.execute(select(func.count(SocialAccount.id))
                                     .where(SocialAccount.user_id == user.id))).scalar_one(),
        "pages": (await s.execute(select(func.count(SocialPage.id))
                                  .where(SocialPage.user_id == user.id))).scalar_one(),
        "scheduled": (await s.execute(select(func.count(Post.id))
                                      .where(Post.user_id == user.id, Post.status == "scheduled"))).scalar_one(),
        "published": (await s.execute(select(func.count(Post.id))
                                      .where(Post.user_id == user.id,
                                             Post.status.in_(["published", "partial"])))).scalar_one(),
        # счётчик ошибок — только по постам пользователя (иначе видны чужие сбои)
        "failed": (await s.execute(select(func.count(PostTarget.id))
                                   .join(Post, Post.id == PostTarget.post_id)
                                   .where(Post.user_id == user.id,
                                          PostTarget.status == "failed"))).scalar_one(),
    }
    upcoming = (await s.execute(
        select(Post).options(selectinload(Post.texts))
        .where(Post.user_id == user.id, Post.status == "scheduled",
               Post.publication_date.is_not(None))
        .order_by(Post.publication_date).limit(8))).scalars().all()
    logs = (await s.execute(
        select(PublicationLog).where(PublicationLog.user_id == user.id)
        .order_by(PublicationLog.id.desc()).limit(12))).scalars().all()
    return {
        "counts": counts,
        "upcoming": [{"id": p.id, "publication_date": p.publication_date.isoformat(),
                      "title": next((t.text[:80] for t in
                                     sorted(p.texts, key=lambda x: x.source_id) if t.text), "")}
                     for p in upcoming],
        "logs": [{"id": l.id, "level": l.level, "message": l.message,
                  "created_at": l.created_at.isoformat()} for l in logs],
    }
