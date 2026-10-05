from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.models.user import User
from app.services.doc_compare import compare_fields

router = APIRouter(prefix="/compare", tags=["Compare"])


class CompareRequest(BaseModel):
    left: dict[str, Any] = Field(default_factory=dict)
    right: dict[str, Any] = Field(default_factory=dict)


@router.post("/fields")
async def compare_docs(body: CompareRequest, _: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"kind": "field_diff", **compare_fields(body.left, body.right)}
