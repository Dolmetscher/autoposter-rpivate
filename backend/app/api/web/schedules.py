from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...deps import get_current_user
from ...models import Schedule, User
from ...schemas import ScheduleIn

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("")
async def list_schedules(user: User = Depends(get_current_user),
                         s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(Schedule).where(Schedule.user_id == user.id)
                            .order_by(Schedule.id))).scalars().all()
    return {"list": [{"id": x.id, "name": x.name, "slots": x.slots} for x in rows]}


@router.post("", status_code=201)
async def create_schedule(payload: ScheduleIn, user: User = Depends(get_current_user),
                          s: AsyncSession = Depends(get_session)):
    sch = Schedule(user_id=user.id, name=payload.name, slots=payload.slots)
    s.add(sch)
    await s.commit()
    await s.refresh(sch)
    return {"id": sch.id, "name": sch.name, "slots": sch.slots}


@router.put("/{schedule_id}")
async def update_schedule(schedule_id: int, payload: ScheduleIn,
                          user: User = Depends(get_current_user),
                          s: AsyncSession = Depends(get_session)):
    sch = (await s.execute(select(Schedule).where(Schedule.id == schedule_id,
                                                  Schedule.user_id == user.id))).scalar_one_or_none()
    if not sch:
        raise HTTPException(404, "Расписание не найдено")
    sch.name = payload.name
    sch.slots = payload.slots
    await s.commit()
    return {"id": sch.id, "name": sch.name, "slots": sch.slots}


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: int, user: User = Depends(get_current_user),
                          s: AsyncSession = Depends(get_session)):
    sch = (await s.execute(select(Schedule).where(Schedule.id == schedule_id,
                                                  Schedule.user_id == user.id))).scalar_one_or_none()
    if not sch:
        raise HTTPException(404, "Расписание не найдено")
    await s.delete(sch)
    await s.commit()
    return {"success": True}
