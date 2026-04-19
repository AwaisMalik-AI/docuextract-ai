import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.document import ScheduledExtraction
from app.models.user import User
from app.schemas.pipeline import ScheduleCreate, ScheduleResponse, ScheduleUpdate
from app.services.scheduler import get_scheduler_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/schedules", tags=["Schedules"])


def _can_manage_schedule(user: User, row: ScheduledExtraction) -> bool:
    return user.role == "admin" or row.created_by == user.id


@router.post("/", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    body: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = get_scheduler_service()
    row = await svc.register_schedule(
        db,
        source_name=body.source_name,
        source_url=str(body.source_url),
        cron_expression=body.cron_expression,
        created_by=user.id,
    )
    return row


@router.get("/", response_model=list[ScheduleResponse])
async def list_schedules(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(ScheduledExtraction).order_by(ScheduledExtraction.created_at.desc())
    if user.role != "admin":
        q = q.where(ScheduledExtraction.created_by == user.id)
    result = await db.execute(q)
    rows = list(result.scalars().all())
    return [ScheduleResponse.model_validate(r) for r in rows]


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str,
    body: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(ScheduledExtraction).where(ScheduledExtraction.id == schedule_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if not _can_manage_schedule(user, row):
        raise HTTPException(status_code=403, detail="Not allowed to update this schedule")

    svc = get_scheduler_service()
    if body.cron_expression is not None:
        row.cron_expression = body.cron_expression
        row.next_run_at = svc.compute_next_run(body.cron_expression)
    if body.is_active is not None:
        row.is_active = body.is_active

    await db.flush()
    await db.refresh(row)
    return row


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(ScheduledExtraction).where(ScheduledExtraction.id == schedule_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if not _can_manage_schedule(user, row):
        raise HTTPException(status_code=403, detail="Not allowed to delete this schedule")
    await db.execute(delete(ScheduledExtraction).where(ScheduledExtraction.id == schedule_id))
