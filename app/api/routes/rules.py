from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_admin
from app.models.document import ValidationRule
from app.models.user import User
from app.schemas.document import ValidationRuleRequest

router = APIRouter(prefix="/api/rules", tags=["Validation Rules"])


@router.post("/", status_code=201)
async def create_rule(req: ValidationRuleRequest, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    existing = await db.execute(select(ValidationRule).where(ValidationRule.name == req.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Rule with this name already exists")

    rule = ValidationRule(
        name=req.name,
        description=req.description,
        rule_type=req.rule_type,
        config=req.config,
        severity=req.severity,
        doc_types=req.doc_types,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return {"id": rule.id, "name": rule.name, "rule_type": rule.rule_type}


@router.get("/")
async def list_rules(db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    result = await db.execute(select(ValidationRule))
    rules = result.scalars().all()
    return [
        {"id": r.id, "name": r.name, "rule_type": r.rule_type, "severity": r.severity, "is_active": r.is_active, "doc_types": r.doc_types}
        for r in rules
    ]


@router.patch("/{rule_id}")
async def toggle_rule(rule_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    result = await db.execute(select(ValidationRule).where(ValidationRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule.is_active = not rule.is_active
    return {"id": rule.id, "name": rule.name, "is_active": rule.is_active}


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    result = await db.execute(select(ValidationRule).where(ValidationRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.delete(rule)
    return {"message": "Rule deleted"}
