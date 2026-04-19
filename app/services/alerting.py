"""Persisted alerts plus email/Slack delivery and numeric anomaly detection."""

from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.document import Alert, Document

logger = logging.getLogger(__name__)

VALID_ALERT_TYPES = frozenset(
    {"extraction_failed", "anomaly_detected", "validation_warning", "export_ready"}
)


def _numeric_values_from_document(doc: Document) -> dict[str, float]:
    out: dict[str, float] = {}
    data = doc.extracted_data
    if not isinstance(data, dict):
        return out
    fields = data.get("fields")
    if isinstance(fields, dict):
        for name, payload in fields.items():
            val: Any = None
            if isinstance(payload, dict):
                val = payload.get("value")
            else:
                val = payload
            if val is None:
                continue
            try:
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    out[name] = float(val)
                elif isinstance(val, str):
                    cleaned = val.replace(",", "").strip()
                    if cleaned:
                        out[name] = float(cleaned)
            except (TypeError, ValueError):
                continue
    return out


class AlertService:
    """Create alerts, optional outbound notifications, and z-score checks."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def send_alert(
        self,
        db: AsyncSession,
        *,
        alert_type: str,
        message: str,
        document_id: str | None = None,
        channel: str | None = None,
    ) -> Alert:
        if alert_type not in VALID_ALERT_TYPES:
            raise ValueError(f"Invalid alert_type: {alert_type}")

        notified: list[str] = []
        if channel in (None, "email", "both") and self.settings.SMTP_HOST:
            ok = await self._send_email_alert(alert_type, message, document_id)
            if ok:
                notified.append("email")
        if channel in (None, "slack", "both") and self.settings.SLACK_WEBHOOK_URL:
            ok = await self._send_slack_alert(alert_type, message, document_id)
            if ok:
                notified.append("slack")

        row = Alert(
            document_id=document_id,
            alert_type=alert_type,
            message=message,
            is_read=False,
            notified_via=",".join(notified) if notified else None,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def check_anomalies(self, db: AsyncSession, document: Document) -> list[str]:
        """Flag numeric fields whose z-score vs same doc_type history exceeds threshold."""
        settings = get_settings()
        threshold = settings.ANOMALY_Z_THRESHOLD
        min_hist = settings.ANOMALY_MIN_HISTORY

        if not document.doc_type:
            return []

        current = _numeric_values_from_document(document)
        if not current:
            return []

        result = await db.execute(
            select(Document).where(
                Document.doc_type == document.doc_type,
                Document.id != document.id,
            )
        )
        historical = list(result.scalars().all())[-500:]

        series: dict[str, list[float]] = defaultdict(list)
        for hist in historical:
            for k, v in _numeric_values_from_document(hist).items():
                series[k].append(v)

        messages: list[str] = []
        for field, value in current.items():
            vals = series.get(field, [])
            if len(vals) < min_hist:
                continue
            try:
                mean = statistics.fmean(vals)
                stdev = statistics.pstdev(vals)
            except statistics.StatisticsError:
                continue
            if stdev == 0 or stdev is None or math.isclose(stdev, 0.0):
                if not math.isclose(value, mean, rel_tol=1e-9, abs_tol=1e-6):
                    messages.append(f"Field '{field}'={value} differs from constant historical mean {mean}")
                continue
            z = abs((value - mean) / stdev)
            if z > threshold:
                messages.append(
                    f"Field '{field}' z-score {z:.2f} exceeds {threshold} (value={value}, mean={mean:.4f}, std={stdev:.4f})"
                )
        return messages

    async def _send_email_alert(self, alert_type: str, message: str, document_id: str | None) -> bool:
        import aiosmtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["Subject"] = f"[DocuExtract] {alert_type}"
        msg["From"] = self.settings.NOTIFICATION_EMAIL_FROM
        msg["To"] = self.settings.SMTP_USER or self.settings.NOTIFICATION_EMAIL_FROM
        body = f"Type: {alert_type}\nDocument: {document_id or 'N/A'}\n\n{message}"
        msg.set_content(body)
        try:
            await aiosmtplib.send(
                msg,
                hostname=self.settings.SMTP_HOST,
                port=self.settings.SMTP_PORT,
                username=self.settings.SMTP_USER,
                password=self.settings.SMTP_PASSWORD,
                start_tls=True,
            )
            return True
        except Exception as e:
            logger.error("Email alert failed: %s", e)
            return False

    async def _send_slack_alert(self, alert_type: str, message: str, document_id: str | None) -> bool:
        if not self.settings.SLACK_WEBHOOK_URL:
            return False
        payload = {
            "text": f"*DocuExtract alert* — `{alert_type}`",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*Alert:* `{alert_type}`"}},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Document:* `{document_id or 'N/A'}`\n{message}"},
                },
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(self.settings.SLACK_WEBHOOK_URL, json=payload)
                r.raise_for_status()
            return True
        except Exception as e:
            logger.error("Slack alert failed: %s", e)
            return False


def get_alert_service() -> AlertService:
    return AlertService()
