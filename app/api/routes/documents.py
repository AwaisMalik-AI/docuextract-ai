import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user, require_reviewer
from app.models.document import AuditLog, Document, ExtractionField
from app.models.user import User
from app.schemas.document import (
    DocumentListResponse,
    DocumentResponse,
    DocumentStatsResponse,
    DocumentUploadResponse,
    ReviewRequest,
)
from app.services.storage import get_storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["Documents"])
settings = get_settings()


@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile = File(...),
    doc_type: Optional[str] = Form(None),
    webhook_url: Optional[str] = Form(None),
    source: str = Form("upload"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    allowed = settings.ALLOWED_EXTENSIONS.split(",")
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"File type .{ext} not allowed. Allowed: {allowed}")

    content = await file.read()
    if len(content) > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"File exceeds {settings.MAX_FILE_SIZE_MB}MB limit")

    storage = get_storage()
    storage_path = await storage.save(content, file.filename, file.content_type)

    document = Document(
        filename=storage_path.split("/")[-1] if "/" in storage_path else storage_path.split("\\")[-1],
        original_filename=file.filename,
        content_type=file.content_type,
        file_size=len(content),
        storage_path=storage_path,
        doc_type=doc_type,
        source=source,
        uploaded_by=user.id,
        webhook_url=webhook_url,
    )
    db.add(document)
    await db.flush()

    audit = AuditLog(document_id=document.id, user_id=user.id, action="uploaded", details={"filename": file.filename, "size": len(content)})
    db.add(audit)
    await db.flush()

    from app.tasks.processing import process_document
    process_document.delay(document.id)

    return DocumentUploadResponse(id=document.id, filename=file.filename, status="received", message="Document queued for processing")


@router.get("/", response_model=DocumentListResponse)
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    doc_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(Document).order_by(Document.created_at.desc())
    count_query = select(func.count(Document.id))

    if status_filter:
        query = query.where(Document.status == status_filter)
        count_query = count_query.where(Document.status == status_filter)
    if doc_type:
        query = query.where(Document.doc_type == doc_type)
        count_query = count_query.where(Document.doc_type == doc_type)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    docs = result.scalars().all()

    return DocumentListResponse(
        documents=[DocumentResponse.model_validate(d) for d in docs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/stats", response_model=DocumentStatsResponse)
async def get_stats(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    total = (await db.execute(select(func.count(Document.id)))).scalar() or 0

    status_result = await db.execute(select(Document.status, func.count(Document.id)).group_by(Document.status))
    by_status = {row[0]: row[1] for row in status_result.all()}

    type_result = await db.execute(
        select(Document.doc_type, func.count(Document.id)).where(Document.doc_type.isnot(None)).group_by(Document.doc_type)
    )
    by_type = {row[0]: row[1] for row in type_result.all()}

    avg_conf = (await db.execute(select(func.avg(Document.confidence_score)))).scalar()
    avg_time = (await db.execute(select(func.avg(Document.processing_time_ms)))).scalar()

    from datetime import datetime, timezone
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = (await db.execute(select(func.count(Document.id)).where(Document.created_at >= today_start))).scalar() or 0

    return DocumentStatsResponse(
        total_documents=total,
        by_status=by_status,
        by_type=by_type,
        avg_confidence=round(avg_conf, 3) if avg_conf else None,
        avg_processing_time_ms=round(avg_time, 1) if avg_time else None,
        documents_today=today_count,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.post("/{document_id}/review")
async def review_document(
    document_id: str,
    req: ReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_reviewer),
):
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status not in ("pending_review", "validated", "extracted"):
        raise HTTPException(status_code=400, detail=f"Document in '{doc.status}' state cannot be reviewed")

    if req.action == "approve":
        doc.status = "approved"
    elif req.action == "reject":
        doc.status = "rejected"
    elif req.action == "correct":
        if req.corrections:
            for field_name, new_value in req.corrections.items():
                field_result = await db.execute(
                    select(ExtractionField).where(
                        ExtractionField.document_id == document_id,
                        ExtractionField.field_name == field_name,
                    )
                )
                field = field_result.scalar_one_or_none()
                if field:
                    field.was_corrected = True
                    field.corrected_value = new_value
                    field.corrected_by = user.id

            if doc.extracted_data and "fields" in doc.extracted_data:
                updated = doc.extracted_data.copy()
                for field_name, new_value in req.corrections.items():
                    if field_name in updated["fields"]:
                        updated["fields"][field_name]["corrected"] = new_value
                doc.extracted_data = updated

        doc.status = "approved"
    else:
        raise HTTPException(status_code=400, detail="Action must be approve, reject, or correct")

    audit = AuditLog(
        document_id=document_id,
        user_id=user.id,
        action=f"review_{req.action}",
        details={"corrections": req.corrections, "notes": req.notes},
    )
    db.add(audit)

    from app.tasks.processing import post_approval_actions
    if doc.status == "approved":
        post_approval_actions.delay(document_id)

    return {"status": doc.status, "message": f"Document {req.action}d successfully"}


@router.post("/{document_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
async def reprocess_document(document_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.status = "received"
    doc.retry_count += 1
    doc.error_message = None

    audit = AuditLog(document_id=document_id, user_id=user.id, action="reprocess_requested", details={"retry_count": doc.retry_count})
    db.add(audit)

    from app.tasks.processing import process_document
    process_document.delay(document_id)

    return {"message": "Document queued for reprocessing"}
