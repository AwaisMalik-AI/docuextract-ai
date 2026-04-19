import logging
from typing import Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class NotificationService:
    """Multi-channel notification dispatcher for document workflow events."""

    def __init__(self):
        self.settings = get_settings()

    async def notify_document_event(self, event: str, document_data: dict):
        tasks = []
        if self.settings.SLACK_WEBHOOK_URL:
            await self._send_slack(event, document_data)
        if self.settings.SMTP_HOST:
            await self._send_email(event, document_data)

    async def deliver_webhook(self, url: str, payload: dict, headers: Optional[dict] = None) -> bool:
        merged_headers = {"Content-Type": "application/json"}
        if headers:
            merged_headers.update(headers)

        try:
            async with httpx.AsyncClient(timeout=self.settings.WEBHOOK_TIMEOUT) as client:
                response = await client.post(url, json=payload, headers=merged_headers)
                response.raise_for_status()
                logger.info(f"Webhook delivered to {url}: {response.status_code}")
                return True
        except Exception as e:
            logger.error(f"Webhook delivery failed for {url}: {e}")
            return False

    async def _send_slack(self, event: str, data: dict):
        emoji_map = {"approved": "✅", "rejected": "❌", "extracted": "📄", "failed": "🚨", "pending_review": "👀"}
        emoji = emoji_map.get(event, "📋")
        message = {
            "text": f"{emoji} *DocuExtract* — Document `{data.get('filename', 'unknown')}` is now *{event}*",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": f"{emoji} *Document {event.upper()}*"}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*File:* {data.get('filename', 'N/A')}"},
                    {"type": "mrkdwn", "text": f"*Type:* {data.get('doc_type', 'N/A')}"},
                    {"type": "mrkdwn", "text": f"*Confidence:* {data.get('confidence', 'N/A')}"},
                    {"type": "mrkdwn", "text": f"*ID:* `{data.get('id', 'N/A')}`"},
                ]},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(self.settings.SLACK_WEBHOOK_URL, json=message)
        except Exception as e:
            logger.error(f"Slack notification failed: {e}")

    async def _send_email(self, event: str, data: dict):
        import aiosmtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["Subject"] = f"[DocuExtract] Document {event}: {data.get('filename', 'unknown')}"
        msg["From"] = self.settings.NOTIFICATION_EMAIL_FROM
        msg["To"] = data.get("notify_email", self.settings.SMTP_USER)
        msg.set_content(
            f"Document: {data.get('filename')}\n"
            f"Status: {event}\n"
            f"Type: {data.get('doc_type', 'N/A')}\n"
            f"Confidence: {data.get('confidence', 'N/A')}\n"
        )
        try:
            await aiosmtplib.send(
                msg,
                hostname=self.settings.SMTP_HOST,
                port=self.settings.SMTP_PORT,
                username=self.settings.SMTP_USER,
                password=self.settings.SMTP_PASSWORD,
                start_tls=True,
            )
        except Exception as e:
            logger.error(f"Email notification failed: {e}")


def get_notification_service() -> NotificationService:
    return NotificationService()
