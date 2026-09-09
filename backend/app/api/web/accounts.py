from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...db import get_session
from ...deps import get_current_user
from ...integrations.base import AdapterError
from ...models import SocialAccount, SocialPage, User
from ...schemas import AccountOut, ConnectIn, PageOut, source_out
from ...services import accounts as accounts_service

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("")
async def list_accounts(user: User = Depends(get_current_user),
                        s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(
        select(SocialAccount).options(selectinload(SocialAccount.pages))
        .where(SocialAccount.user_id == user.id).order_by(SocialAccount.id)
    )).scalars().all()
    out = []
    for a in rows:
        d = AccountOut.model_validate(a).model_dump()
        d["pages"] = [PageOut.model_validate(p).model_dump() for p in a.pages]
        out.append(d)
    return {"list": out}


@router.post("/connect")
async def connect(payload: ConnectIn, user: User = Depends(get_current_user),
                  s: AsyncSession = Depends(get_session)):
    from ...integrations.registry import get_adapter
    from ...constants import SOURCES
    from ...services import limits
    if payload.source_id not in SOURCES or SOURCES[payload.source_id].adapter is None:
        raise HTTPException(400, "Эта соцсеть пока не поддерживается")
    try:
        await limits.ensure_within(s, user, "accounts")
        account = await accounts_service.connect_account(
            s, user, payload.source_id, payload.credentials, payload.extra)
    except AdapterError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    await s.commit()
    d = AccountOut.model_validate(account).model_dump()
    pages = (await s.execute(select(SocialPage).where(SocialPage.account_id == account.id))).scalars().all()
    d["pages"] = [PageOut.model_validate(p).model_dump() for p in pages]
    return d


@router.post("/{account_id}/refresh")
async def refresh_pages(account_id: int, user: User = Depends(get_current_user),
                        s: AsyncSession = Depends(get_session)):
    account = (await s.execute(
        select(SocialAccount).where(SocialAccount.id == account_id,
                                    SocialAccount.user_id == user.id))).scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Аккаунт не найден")
    try:
        created = await accounts_service.refresh_pages(s, account)
    except AdapterError as e:
        raise HTTPException(400, str(e))
    await s.commit()
    return {"created": created}


@router.delete("/{account_id}")
async def delete_account(account_id: int, user: User = Depends(get_current_user),
                         s: AsyncSession = Depends(get_session)):
    account = (await s.execute(
        select(SocialAccount).where(SocialAccount.id == account_id,
                                    SocialAccount.user_id == user.id))).scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Аккаунт не найден")
    await s.delete(account)
    await s.commit()
    return {"success": True}
