"""Аналитика: метрики опубликованных постов (последний снимок на цель) + итоги."""
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...db import get_session
from ...deps import get_current_user
from ...models import Post, PostStat, PostTarget, User
from ...util import utcnow

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("")
async def analytics(days: int = 30, user: User = Depends(get_current_user),
                    s: AsyncSession = Depends(get_session)):
    days = max(1, min(days, 365))
    since = utcnow() - timedelta(days=days)

    targets = (await s.execute(
        select(PostTarget)
        .options(selectinload(PostTarget.post), selectinload(PostTarget.page))
        .join(Post, Post.id == PostTarget.post_id)
        .where(Post.user_id == user.id,
               PostTarget.status == "published",
               PostTarget.published_at >= since,
               PostTarget.published_at.is_not(None))
        .order_by(PostTarget.published_at.desc())
    )).scalars().unique().all()

    if not targets:
        return {"list": [], "totals": {"all": _empty(), "by_source": {}}}

    stat_rows = (await s.execute(
        select(PostStat).where(PostStat.target_id.in_([t.id for t in targets]))
        .order_by(PostStat.fetched_at)
    )).scalars().all()
    latest: dict[int, PostStat] = {}
    for st in stat_rows:  # последняя по времени перезаписывает
        latest[st.target_id] = st

    items = []
    totals_all = _empty()
    by_source: dict[int, dict] = {}
    for t in targets:
        st = latest.get(t.id)
        row = {
            "post_id": t.post_id,
            "target_id": t.id,
            "source_id": t.source_id,
            "page_name": t.page.social_page_name,
            "published_at": t.published_at.isoformat() if t.published_at else None,
            "external_url": t.external_url,
            "views": st.views if st else 0,
            "likes": st.likes if st else 0,
            "reposts": st.reposts if st else 0,
            "comments": st.comments if st else 0,
            "stats_at": st.fetched_at.isoformat() if st else None,
        }
        items.append(row)
        for k in ("views", "likes", "reposts", "comments"):
            totals_all[k] += row[k]
        agg = by_source.setdefault(t.source_id, _empty())
        agg["posts"] += 1
        for k in ("views", "likes", "reposts", "comments"):
            agg[k] += row[k]
    totals_all["posts"] = len(items)
    return {"list": items, "totals": {"all": totals_all, "by_source": by_source}}


def _empty() -> dict:
    return {"posts": 0, "views": 0, "likes": 0, "reposts": 0, "comments": 0}
