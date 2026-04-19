from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.document import Alert
from app.models.user import User
from app.schemas.pipeline import AlertListResponse, AlertResponse, UnreadCountResponse

router = APIRouter(prefix="/api/alerts", tags=["Alerts"])


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ = user
    q = select(func.count(Alert.id)).where(Alert.is_read.is_(False))
    count = (await db.execute(q)).scalar() or 0
    return UnreadCountResponse(count=count)


@router.get("/", response_model=AlertListResponse)
async def list_alerts(
    unread_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ = user
    count_q = select(func.count(Alert.id))
    if unread_only:
        count_q = count_q.where(Alert.is_read.is_(False))
    total = (await db.execute(count_q)).scalar() or 0

    q = select(Alert)
    if unread_only:
        q = q.where(Alert.is_read.is_(False))
    q = q.order_by(Alert.created_at.desc()).limit(500)
    result = await db.execute(q)
    rows = list(result.scalars().all())
    return AlertListResponse(alerts=[AlertResponse.model_validate(r) for r in rows], total=total)


@router.patch("/{alert_id}/read", response_model=AlertResponse)
async def mark_alert_read(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ = user
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    row.is_read = True
    await db.flush()
    await db.refresh(row)
    return row
