"""Cron-based scheduled URL ingestion for the document pipeline."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.document import ScheduledExtraction

logger = logging.getLogger(__name__)


class SchedulerService:
    """Register schedules, compute next runs, and fetch remote document bytes."""

    @staticmethod
    def fetch_from_url(url: str) -> bytes:
        settings = get_settings()
        timeout = settings.HTTP_FETCH_TIMEOUT_SEC
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.content

    @staticmethod
    def fetch_from_url_with_meta(url: str) -> tuple[bytes, str, str | None]:
        """Return (body, content_type, suggested_filename_from_url)."""
        settings = get_settings()
        timeout = settings.HTTP_FETCH_TIMEOUT_SEC
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            ctype = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
            path = httpx.URL(url).path
            name = path.rsplit("/", 1)[-1] if path else None
            if not name or name == "/":
                name = None
            return response.content, ctype, name

    @staticmethod
    def compute_next_run(cron_expression: str, base: datetime | None = None) -> datetime:
        base = base or datetime.now(timezone.utc)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        itr = croniter(cron_expression, base)
        nxt = itr.get_next(datetime)
        if isinstance(nxt, datetime):
            if nxt.tzinfo is None:
                nxt = nxt.replace(tzinfo=timezone.utc)
            return nxt
        return datetime.fromtimestamp(float(nxt), tz=timezone.utc)

    async def register_schedule(
        self,
        db: AsyncSession,
        *,
        source_name: str,
        source_url: str,
        cron_expression: str,
        created_by: str,
    ) -> ScheduledExtraction:
        next_run = self.compute_next_run(cron_expression)
        row = ScheduledExtraction(
            source_name=source_name,
            source_url=source_url,
            cron_expression=cron_expression,
            next_run_at=next_run,
            is_active=True,
            created_by=created_by,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def get_due_jobs(self, db: AsyncSession) -> list[ScheduledExtraction]:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(ScheduledExtraction).where(
                ScheduledExtraction.is_active.is_(True),
                ScheduledExtraction.next_run_at <= now,
            )
        )
        return list(result.scalars().all())

    async def advance_next_run(self, db: AsyncSession, schedule_id: str) -> None:
        result = await db.execute(select(ScheduledExtraction).where(ScheduledExtraction.id == schedule_id))
        row = result.scalar_one_or_none()
        if not row:
            return
        base = datetime.now(timezone.utc)
        row.next_run_at = self.compute_next_run(row.cron_expression, base=base)
        await db.flush()


def get_scheduler_service() -> SchedulerService:
    return SchedulerService()
