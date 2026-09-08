"""Подключение аккаунтов соцсетей и синхронизация их страниц/групп."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations.base import AdapterError, PageInfo
from ..integrations.registry import get_adapter
from ..models import SocialAccount, SocialPage, User
from ..util import utcnow

log = logging.getLogger("accounts")


async def upsert_pages(s: AsyncSession, account: SocialAccount, infos: list[PageInfo]) -> int:
    existing = (await s.execute(
        select(SocialPage).where(SocialPage.account_id == account.id)
    )).scalars().all()
    by_ext = {p.social_page_id: p for p in existing}
    created = 0
    for info in infos:
        page = by_ext.get(info.social_page_id)
        if page is None:
            s.add(SocialPage(
                user_id=account.user_id, account_id=account.id,
                source_id=account.source_id, social_page_id=info.social_page_id,
                social_page_name=info.name, social_page_photo=info.photo,
                page_kind=info.kind, can_post=info.can_post,
            ))
            created += 1
        else:
            page.social_page_name = info.name or page.social_page_name
            page.social_page_photo = info.photo or page.social_page_photo
            page.can_post = info.can_post
    await s.flush()
    return created


async def connect_account(s: AsyncSession, user: User, source_id: int,
                          credentials: dict, extra: dict | None = None) -> SocialAccount:
    adapter = get_adapter(source_id)
    account = SocialAccount(user_id=user.id, source_id=source_id,
                            credentials=dict(credentials or {}),
                            extra=dict(extra or {}))
    s.add(account)
    await s.flush()

    infos = await adapter.fetch_pages(account, extra=extra)
    if not infos:
        raise AdapterError("Не найдено ни одной страницы/канала для этого аккаунта")

    first = infos[0]
    account.social_account_id = first.social_page_id
    account.social_account_name = first.name
    account.social_account_photo = first.photo
    await upsert_pages(s, account, infos)
    await s.flush()
    return account


async def refresh_pages(s: AsyncSession, account: SocialAccount) -> int:
    adapter = get_adapter(account.source_id)
    infos = await adapter.fetch_pages(account)
    return await upsert_pages(s, account, infos)
