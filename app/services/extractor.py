import json
import logging
from typing import Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are a document data extraction specialist. Analyze the following document text 
and extract structured information.

Document type: {doc_type}

Extract ALL of the following fields where present. Return a JSON object with these keys:

For invoices:
- vendor_name, vendor_address, vendor_tax_id
- invoice_number, invoice_date, due_date
- bill_to_name, bill_to_address
- subtotal, tax_amount, tax_rate, discount, total_amount
- currency, payment_terms
- line_items (array of: description, quantity, unit_price, amount)

For receipts:
- store_name, store_address
- receipt_date, receipt_number
- items (array of: name, quantity, price)
- subtotal, tax, total
- payment_method

For purchase orders:
- po_number, po_date, delivery_date
- buyer_name, supplier_name
- line_items (array of: item_code, description, quantity, unit_price, amount)
- total_amount, currency, shipping_terms

For any document, also include:
- document_language
- confidence_notes (any fields you are uncertain about)

IMPORTANT: For each field, include a confidence score from 0.0 to 1.0.
Return format: {{"fields": {{"field_name": {{"value": "...", "confidence": 0.95}}, ...}}, "doc_type_detected": "..."}}

Document text:
---
{text}
---

Return ONLY valid JSON, no markdown formatting."""


class LLMExtractor:
    """LLM-based document field extraction with provider abstraction."""

    def __init__(self):
        self.settings = get_settings()

    async def extract(self, text: str, doc_type: Optional[str] = None) -> dict:
        prompt = EXTRACTION_PROMPT.format(
            doc_type=doc_type or "auto-detect",
            text=text[:12000],  # token budget guard
        )

        if self.settings.LLM_PROVIDER == "openai":
            return await self._call_openai(prompt)
        elif self.settings.LLM_PROVIDER == "anthropic":
            return await self._call_anthropic(prompt)
        elif self.settings.LLM_PROVIDER == "ollama":
            return await self._call_ollama(prompt)
        else:
            raise ValueError(f"Unsupported LLM provider: {self.settings.LLM_PROVIDER}")

    async def _call_openai(self, prompt: str) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.settings.LLM_BASE_URL or 'https://api.openai.com/v1'}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.LLM_API_KEY}"},
                json={
                    "model": self.settings.LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)

    async def _call_anthropic(self, prompt: str) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.settings.LLM_API_KEY,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": self.settings.LLM_MODEL,
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            content = response.json()["content"][0]["text"]
            return self._parse_json_response(content)

    async def _call_ollama(self, prompt: str) -> dict:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.settings.LLM_BASE_URL or 'http://localhost:11434'}/api/generate",
                json={
                    "model": self.settings.LLM_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
            )
            response.raise_for_status()
            content = response.json()["response"]
            return json.loads(content)

    @staticmethod
    def _parse_json_response(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        return json.loads(text)


def get_extractor() -> LLMExtractor:
    return LLMExtractor()
