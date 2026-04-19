from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_admin
from app.models.document import WebhookConfig
from app.models.user import User
from app.schemas.document import WebhookConfigRequest

router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])


@router.post("/", status_code=201)
async def create_webhook(
    req: WebhookConfigRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    valid_events = {"extracted", "validated", "approved", "rejected", "failed", "posted"}
    invalid = set(req.events) - valid_events
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid events: {invalid}. Valid: {valid_events}")

    webhook = WebhookConfig(
        name=req.name,
        url=str(req.url),
        events=req.events,
        headers=req.headers,
        created_by=user.id,
    )
    db.add(webhook)
    await db.flush()
    await db.refresh(webhook)
    return {"id": webhook.id, "name": webhook.name, "events": webhook.events}


@router.get("/")
async def list_webhooks(db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    result = await db.execute(select(WebhookConfig).where(WebhookConfig.is_active == True))
    hooks = result.scalars().all()
    return [{"id": h.id, "name": h.name, "url": h.url, "events": h.events, "is_active": h.is_active} for h in hooks]


@router.delete("/{webhook_id}")
async def delete_webhook(webhook_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    result = await db.execute(select(WebhookConfig).where(WebhookConfig.id == webhook_id))
    hook = result.scalar_one_or_none()
    if not hook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    hook.is_active = False
    return {"message": "Webhook deactivated"}
