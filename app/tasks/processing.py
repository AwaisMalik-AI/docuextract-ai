import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.config import get_settings
from app.tasks.celery_app import celery

logger = logging.getLogger(__name__)
settings = get_settings()

task_engine = create_async_engine(settings.DATABASE_URL, pool_size=5)
task_session_factory = async_sessionmaker(task_engine, class_=AsyncSession, expire_on_commit=False)


def run_async(coro):
    """Run async function inside sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery.task(name="docuextract.process_document", bind=True, max_retries=3, default_retry_delay=60)
def process_document(self, document_id: str):
    try:
        run_async(_process_document(document_id))
    except Exception as exc:
        logger.error(f"Processing failed for {document_id}: {exc}", exc_info=True)
        run_async(_mark_failed(document_id, str(exc)))
        raise self.retry(exc=exc)


async def _process_document(document_id: str):
    async with task_session_factory() as db:
        from app.models.document import Document, ExtractionField, AuditLog
        result = await db.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()
        if not doc:
            logger.error(f"Document {document_id} not found")
            return

        start_time = time.time()

        # --- Phase 1: OCR ---
        doc.status = "preprocessing"
        await db.commit()

        from app.services.storage import get_storage
        from app.services.ocr import get_ocr_engine

        storage = get_storage()
        file_bytes = await storage.retrieve(doc.storage_path)

        ocr = get_ocr_engine()
        ocr_result = ocr.extract_text(file_bytes, doc.content_type)
        doc.raw_ocr_text = ocr_result["text"]
        doc.page_count = ocr_result["page_count"]
        doc.status = "extracting"
        await db.commit()

        # --- Phase 2: LLM extraction ---
        from app.services.extractor import get_extractor
        extractor = get_extractor()
        extracted = await extractor.extract(ocr_result["text"], doc.doc_type)

        doc.extracted_data = extracted
        detected_type = extracted.get("doc_type_detected")
        if detected_type and not doc.doc_type:
            doc.doc_type = detected_type

        fields = extracted.get("fields", {})
        overall_confidence = 0.0
        field_count = 0
        for field_name, field_data in fields.items():
            if isinstance(field_data, dict):
                conf = field_data.get("confidence", 0.5)
                ef = ExtractionField(
                    document_id=document_id,
                    field_name=field_name,
                    field_value=str(field_data.get("value", "")),
                    confidence=conf,
                )
                db.add(ef)
                overall_confidence += conf
                field_count += 1

        doc.confidence_score = round(overall_confidence / max(field_count, 1), 3)
        doc.status = "extracted"
        await db.commit()

        # --- Phase 3: Validation ---
        doc.status = "validating"
        await db.commit()

        from app.services.validator import get_validator
        validator = get_validator()
        validation = await validator.validate(extracted, doc.doc_type, db)
        doc.validation_results = validation

        if validation["is_valid"] and doc.confidence_score >= 0.85:
            doc.status = "validated"
        else:
            doc.status = "pending_review"

        elapsed_ms = int((time.time() - start_time) * 1000)
        doc.processing_time_ms = elapsed_ms

        audit = AuditLog(
            document_id=document_id,
            action="processing_complete",
            details={
                "confidence": doc.confidence_score,
                "status": doc.status,
                "fields_extracted": field_count,
                "processing_time_ms": elapsed_ms,
                "validation_passed": validation["is_valid"],
            },
        )
        db.add(audit)

        if not validation["is_valid"]:
            try:
                from app.services.alerting import get_alert_service

                await get_alert_service().send_alert(
                    db,
                    alert_type="validation_warning",
                    message=f"Validation did not pass for document {document_id}: {str(validation)[:3500]}",
                    document_id=document_id,
                    channel=None,
                )
            except Exception:
                logger.exception("validation_warning alert failed for %s", document_id)

        await db.commit()

        # --- Notifications ---
        from app.services.notifications import get_notification_service
        notifier = get_notification_service()
        await notifier.notify_document_event(doc.status, {
            "id": doc.id,
            "filename": doc.original_filename,
            "doc_type": doc.doc_type,
            "confidence": doc.confidence_score,
        })

        logger.info(f"Document {document_id} processed in {elapsed_ms}ms — status={doc.status}, confidence={doc.confidence_score}")


async def _mark_failed(document_id: str, error: str):
    async with task_session_factory() as db:
        from app.models.document import Document
        result = await db.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()
        if doc:
            doc.status = "failed"
            doc.error_message = error[:2000]
            await db.commit()

    try:
        async with task_session_factory() as db_alert:
            from app.services.alerting import get_alert_service

            await get_alert_service().send_alert(
                db_alert,
                alert_type="extraction_failed",
                message=error[:4000],
                document_id=document_id,
                channel=None,
            )
            await db_alert.commit()
    except Exception:
        logger.exception("Failed to persist extraction_failed alert for %s", document_id)


@celery.task(name="docuextract.post_approval_actions", bind=True, max_retries=2)
def post_approval_actions(self, document_id: str):
    try:
        run_async(_post_approval(document_id))
    except Exception as exc:
        logger.error(f"Post-approval actions failed for {document_id}: {exc}")
        raise self.retry(exc=exc)


async def _post_approval(document_id: str):
    async with task_session_factory() as db:
        from app.models.document import Document, WebhookConfig
        result = await db.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()
        if not doc:
            return

        hooks_result = await db.execute(
            select(WebhookConfig).where(WebhookConfig.is_active == True)
        )
        hooks = hooks_result.scalars().all()

        from app.services.notifications import get_notification_service
        notifier = get_notification_service()

        payload = {
            "event": "approved",
            "document_id": doc.id,
            "filename": doc.original_filename,
            "doc_type": doc.doc_type,
            "extracted_data": doc.extracted_data,
            "confidence_score": doc.confidence_score,
        }

        for hook in hooks:
            if "approved" in hook.events:
                delivered = await notifier.deliver_webhook(hook.url, payload, hook.headers)
                if delivered:
                    logger.info(f"Webhook {hook.name} delivered for document {document_id}")

        if doc.webhook_url:
            await notifier.deliver_webhook(doc.webhook_url, payload)
            doc.webhook_delivered = True
            await db.commit()

        doc.status = "posted"
        await db.commit()


def _filename_for_scheduled_download(source_name: str, content_type: str, url_name: str | None) -> str:
    ext = ".bin"
    low = (content_type or "").lower()
    if "pdf" in low:
        ext = ".pdf"
    elif "png" in low:
        ext = ".png"
    elif "jpeg" in low or "jpg" in low:
        ext = ".jpg"
    elif "tiff" in low:
        ext = ".tiff"
    elif "csv" in low:
        ext = ".csv"
    if url_name and "." in url_name[-12:]:
        return url_name[:255]
    safe = "".join(c for c in source_name if c.isalnum() or c in "._- ")[:80] or "scheduled"
    return f"{safe}{ext}"


@celery.task(name="docuextract.generate_export", bind=True, max_retries=2, default_retry_delay=30)
def generate_export_task(self, job_id: str):
    try:
        run_async(_generate_export(job_id))
    except Exception as exc:
        logger.exception("generate_export failed for job %s", job_id)
        run_async(_mark_export_failed(job_id, str(exc)))
        raise self.retry(exc=exc)


async def _mark_export_failed(job_id: str, error: str):
    async with task_session_factory() as db:
        from app.models.document import ExportJob

        result = await db.execute(select(ExportJob).where(ExportJob.id == job_id))
        job = result.scalar_one_or_none()
        if job:
            job.status = "failed"
            job.error_message = error[:4000]
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()


async def _generate_export(job_id: str):
    from app.models.document import ExportJob
    from app.services.alerting import get_alert_service
    from app.services.export_engine import build_filtered_documents_query, get_export_engine

    async with task_session_factory() as db:
        result = await db.execute(select(ExportJob).where(ExportJob.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            logger.error("ExportJob %s not found", job_id)
            return

        job.status = "generating"
        job.error_message = None
        await db.commit()

    try:
        async with task_session_factory() as db:
            result = await db.execute(select(ExportJob).where(ExportJob.id == job_id))
            job = result.scalar_one_or_none()
            if not job:
                return

            q = build_filtered_documents_query(job.filters)
            res = await db.execute(q)
            docs = list(res.scalars().all())
            engine = get_export_engine()
            fmt = job.export_format
            if fmt == "csv":
                path = engine.export_csv(docs, job.filters)
            elif fmt == "excel":
                path = engine.export_excel(docs, job.filters)
            elif fmt == "json":
                path = engine.export_json(docs, job.filters)
            else:
                raise ValueError(f"Unsupported export format: {fmt}")

            job.file_path = path
            job.record_count = len(docs)
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()

        async with task_session_factory() as db_alert:
            alert_svc = get_alert_service()
            await alert_svc.send_alert(
                db_alert,
                alert_type="export_ready",
                message=f"Export job {job_id} completed with {len(docs)} records.",
                document_id=None,
                channel=None,
            )
            await db_alert.commit()
    except Exception:
        logger.exception("Export generation failed for %s", job_id)
        raise


@celery.task(name="docuextract.scheduled_fetch", bind=True, max_retries=2, default_retry_delay=120)
def scheduled_fetch_task(self, schedule_id: str):
    try:
        run_async(_scheduled_fetch(schedule_id))
    except Exception as exc:
        logger.exception("scheduled_fetch failed for %s", schedule_id)
        raise self.retry(exc=exc)


async def _scheduled_fetch(schedule_id: str):
    from app.models.document import AuditLog, Document, ScheduledExtraction
    from app.services.alerting import get_alert_service
    from app.services.scheduler import get_scheduler_service
    from app.services.storage import get_storage

    doc_id: str | None = None
    async with task_session_factory() as db:
        result = await db.execute(select(ScheduledExtraction).where(ScheduledExtraction.id == schedule_id))
        sch = result.scalar_one_or_none()
        if not sch or not sch.is_active:
            return

        svc = get_scheduler_service()
        try:
            content, ctype, url_name = svc.fetch_from_url_with_meta(sch.source_url)
        except Exception as exc:
            logger.exception("Scheduled URL fetch failed schedule_id=%s", schedule_id)
            sch.last_run_at = datetime.now(timezone.utc)
            sch.next_run_at = svc.compute_next_run(sch.cron_expression)
            await get_alert_service().send_alert(
                db,
                alert_type="extraction_failed",
                message=f"Scheduled fetch failed for '{sch.source_name}': {exc}",
                document_id=None,
                channel=None,
            )
            await db.commit()
            return

        storage = get_storage()
        filename = _filename_for_scheduled_download(sch.source_name, ctype, url_name)
        storage_path = await storage.save(content, filename, ctype)
        short_name = storage_path.split("/")[-1] if "/" in storage_path else storage_path.split("\\")[-1]

        doc = Document(
            filename=short_name,
            original_filename=filename,
            content_type=ctype,
            file_size=len(content),
            storage_path=storage_path,
            doc_type=None,
            source="url",
            uploaded_by=sch.created_by,
        )
        db.add(doc)
        await db.flush()
        doc_id = doc.id

        sch.last_run_at = datetime.now(timezone.utc)
        sch.next_run_at = svc.compute_next_run(sch.cron_expression)

        db.add(
            AuditLog(
                document_id=doc_id,
                user_id=sch.created_by,
                action="scheduled_fetch",
                details={"schedule_id": schedule_id, "url": sch.source_url, "source_name": sch.source_name},
            )
        )
        await db.commit()

    if doc_id:
        process_document.delay(doc_id)


@celery.task(name="docuextract.check_scheduled_extractions")
def check_scheduled_extractions():
    run_async(_check_scheduled_extractions())


async def _check_scheduled_extractions():
    from app.services.scheduler import get_scheduler_service

    async with task_session_factory() as db:
        due = await get_scheduler_service().get_due_jobs(db)
        ids = [row.id for row in due]
    for sid in ids:
        scheduled_fetch_task.delay(sid)


@celery.task(name="docuextract.anomaly_check_task")
def anomaly_check_task():
    run_async(_anomaly_check_task())


async def _anomaly_check_task():
    from app.models.document import Document
    from app.services.alerting import get_alert_service

    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    async with task_session_factory() as db:
        result = await db.execute(
            select(Document).where(
                Document.updated_at >= cutoff,
                Document.status.in_(("approved", "validated", "posted", "extracted")),
                Document.extracted_data.is_not(None),
            )
        )
        docs = list(result.scalars().all())
        alert_svc = get_alert_service()
        for doc in docs:
            messages = await alert_svc.check_anomalies(db, doc)
            if messages:
                await alert_svc.send_alert(
                    db,
                    alert_type="anomaly_detected",
                    message="; ".join(messages)[:8000],
                    document_id=doc.id,
                    channel=None,
                )
        await db.commit()
