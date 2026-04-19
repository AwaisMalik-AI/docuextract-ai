import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.document import ExportJob
from app.models.user import User
from app.schemas.pipeline import ExportJobCreate, ExportJobListResponse, ExportJobResponse
from app.tasks.processing import generate_export_task

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/exports", tags=["Exports"])
settings = get_settings()


@router.post("/", response_model=ExportJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_export_job(
    body: ExportJobCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    job = ExportJob(
        user_id=user.id,
        export_format=body.export_format,
        filters=body.filters or {},
        status="pending",
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    generate_export_task.delay(job.id)
    return job


@router.get("/", response_model=ExportJobListResponse)
async def list_export_jobs(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(ExportJob).where(ExportJob.user_id == user.id).order_by(ExportJob.created_at.desc())
    total = (await db.execute(select(func.count(ExportJob.id)).where(ExportJob.user_id == user.id))).scalar() or 0
    result = await db.execute(q)
    jobs = list(result.scalars().all())
    return ExportJobListResponse(jobs=[ExportJobResponse.model_validate(j) for j in jobs], total=total)


@router.get("/{job_id}/download")
async def download_export(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(ExportJob).where(ExportJob.id == job_id, ExportJob.user_id == user.id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")
    if job.status != "completed" or not job.file_path:
        raise HTTPException(status_code=400, detail="Export is not ready for download")

    export_root = Path(settings.EXPORT_DIR).resolve()
    target = Path(job.file_path).resolve()
    try:
        target.relative_to(export_root)
    except ValueError:
        logger.warning("Path traversal blocked for export %s path %s", job_id, target)
        raise HTTPException(status_code=400, detail="Invalid export path")

    if not target.is_file():
        raise HTTPException(status_code=404, detail="Export file missing on disk")

    media_map = {"csv": "text/csv", "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "json": "application/json"}
    media = media_map.get(job.export_format, "application/octet-stream")
    return FileResponse(path=str(target), filename=target.name, media_type=media)
