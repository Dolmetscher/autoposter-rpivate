from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...deps import get_current_user
from ...models import Project, User
from ...schemas import ProjectIn

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
async def list_projects(user: User = Depends(get_current_user),
                        s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(Project).where(Project.user_id == user.id)
                            .order_by(Project.id))).scalars().all()
    return {"list": [{"id": p.id, "name": p.name, "settings": p.settings} for p in rows]}


@router.post("", status_code=201)
async def create_project(payload: ProjectIn, user: User = Depends(get_current_user),
                         s: AsyncSession = Depends(get_session)):
    proj = Project(user_id=user.id, name=payload.name, settings=payload.settings)
    s.add(proj)
    await s.commit()
    await s.refresh(proj)
    return {"id": proj.id, "name": proj.name, "settings": proj.settings}


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
    await s.commit()
    return {"id": proj.id, "name": proj.name, "settings": proj.settings}


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
