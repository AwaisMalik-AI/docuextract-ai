from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.models.user import User
from app.services.extraction_crew import ExtractionCrew
from app.tasks.crew_tasks import run_extraction_crew_task

router = APIRouter(prefix="/crews", tags=["Crews"])


class ExtractCrewRequest(BaseModel):
    text: str = Field(..., min_length=3)
    doc_type: str = Field(default="invoice", max_length=64)
    async_run: bool = False


class ExtractCrewResponse(BaseModel):
    crew: str
    used_llm: bool
    steps: list[dict[str, Any]]
    fields: dict[str, Any]
    confidence: float
    task_id: str | None = None


@router.post("/extract", response_model=ExtractCrewResponse)
async def run_extract_crew(
    body: ExtractCrewRequest,
    _: User = Depends(get_current_user),
) -> ExtractCrewResponse:
    if body.async_run:
        task = run_extraction_crew_task.delay(body.text, body.doc_type)
        return ExtractCrewResponse(
            crew="extract",
            used_llm=False,
            steps=[],
            fields={},
            confidence=0.0,
            task_id=task.id,
        )
    result = ExtractionCrew().run(body.text, body.doc_type)
    return ExtractCrewResponse(
        crew=result.crew,
        used_llm=result.used_llm,
        steps=result.steps,
        fields=result.fields,
        confidence=result.confidence,
    )
