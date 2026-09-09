"""Тарифы и лимиты (MVP SaaS-режима: планы в конфиге, смена плана вручную;
в реальном сервисе сюда подключается вебхук биллинга)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import CrosspostSource, Post, SocialAccount, User
from ..util import utcnow


def plans() -> dict:
    return settings.plans


async def usage(s: AsyncSession, user: User) -> dict:
    month_ago = utcnow() - timedelta(days=30)
    accounts = (await s.execute(select(func.count(SocialAccount.id))
                                .where(SocialAccount.user_id == user.id))).scalar_one()
    posts = (await s.execute(select(func.count(Post.id))
                             .where(Post.user_id == user.id,
                                    Post.created_at >= month_ago))).scalar_one()
    sources = (await s.execute(select(func.count(CrosspostSource.id))
                               .where(CrosspostSource.user_id == user.id))).scalar_one()
    return {"accounts": accounts, "posts_month": posts, "sources": sources}


async def ensure_within(s: AsyncSession, user: User, kind: str) -> None:
    """Бросает ValueError с человекочитаемым сообщением при превышении лимита."""
    plan = plans().get(user.plan) or plans()["free"]
    limit = plan.get(kind)
    if not limit:  # 0/None = без лимита
        return
    used = (await usage(s, user)).get(kind, 0)
    if used >= int(limit):
        raise ValueError(
            f"Лимит тарифа «{plan['name']}»: { {'accounts': 'аккаунтов', 'posts_month': 'постов в месяц', 'sources': 'источников кросспостинга'}[kind] } "
            f"— {limit}. Обновите тариф в Настройках.")
