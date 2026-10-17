from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...db import get_session
from ...deps import get_current_user
from ...models import Project, User, SocialAccount
from ...schemas import ProjectIn

router = APIRouter(prefix="/projects", tags=["projects"])


def _project_out(p: Project) -> dict:
    return {"id": p.id, "name": p.name, "settings": p.settings,
            "account_ids": [a.id for a in p.accounts]}


@router.get("")
async def list_projects(user: User = Depends(get_current_user),
                        s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(
        select(Project).options(selectinload(Project.accounts))
        .where(Project.user_id == user.id).order_by(Project.id)
    )).scalars().all()
    return {"list": [_project_out(p) for p in rows]}


async def _load_accounts(s: AsyncSession, user: User, account_ids: list[int]) -> list[SocialAccount]:
    """Аккаунты пользователя по id; ошибка, если хотя бы один не найден/не его."""
    unique_ids = list(dict.fromkeys(account_ids))
    result = await s.execute(
        select(SocialAccount).where(
            SocialAccount.id.in_(unique_ids),
            SocialAccount.user_id == user.id,
        )
    )
    accounts = result.scalars().all()
    if len(accounts) != len(unique_ids):
        raise HTTPException(400, "Some accounts not found or do not belong to user")
    return accounts


@router.post("", status_code=201)
async def create_project(payload: ProjectIn, user: User = Depends(get_current_user),
                         s: AsyncSession = Depends(get_session)):
    proj = Project(user_id=user.id, name=payload.name, settings=payload.settings)
    if payload.account_ids:
        proj.accounts = await _load_accounts(s, user, payload.account_ids)
    s.add(proj)
    await s.commit()
    await s.refresh(proj, ["accounts"])
    return _project_out(proj)


@router.put("/{project_id}")
async def update_project(project_id: int, payload: ProjectIn,
                         user: User = Depends(get_current_user),
                         s: AsyncSession = Depends(get_session)):
    proj = (await s.execute(select(Project).where(Project.id == project_id,
                                                  Project.user_id == user.id))).scalar_one_or_none()
    if not proj:
        raise HTTPException(404, "Проект не найден")
    proj.name = payload.name
    proj.settings = payload.settings
    if payload.account_ids is not None:
        # Заменяем привязку только когда поле передано: PUT без account_ids
        # не должен молча отвязывать все аккаунты проекта.
        # Загружаем текущую коллекцию явно: присваивание коллекции иначе
        # требует ленивой загрузки, недоступной в async-сессии (MissingGreenlet).
        await s.refresh(proj, ["accounts"])
        proj.accounts = await _load_accounts(s, user, payload.account_ids)
    await s.commit()
    await s.refresh(proj, ["accounts"])
    return _project_out(proj)


@router.delete("/{project_id}")
async def delete_project(project_id: int, user: User = Depends(get_current_user),
                         s: AsyncSession = Depends(get_session)):
    proj = (await s.execute(select(Project).where(Project.id == project_id,
                                                  Project.user_id == user.id))).scalar_one_or_none()
    if not proj:
        raise HTTPException(404, "Проект не найден")
    await s.delete(proj)
    await s.commit()
    return {"success": True}
