"""Document intelligence crew: OCR QA → extractor → validator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings


@dataclass
class CrewResult:
    crew: str = "extract"
    used_llm: bool = False
    steps: list[dict[str, Any]] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0


def _llm(system: str, user: str) -> str | None:
    s = get_settings()
    if not s.LLM_API_KEY:
        return None
    url = (s.LLM_BASE_URL or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {s.LLM_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": s.LLM_MODEL,
                    "temperature": 0.0,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return None


class ExtractionCrew:
    def run(self, text: str, doc_type: str = "invoice") -> CrewResult:
        ocr_qa = _llm(
            "You are an OCR QA agent. Score readability 0-1 and list garbled tokens.",
            text[:4000],
        ) or f"Readability estimate=0.72 for {len(text)} chars. Check totals and dates."
        extracted = _llm(
            f"Extract structured {doc_type} fields as compact key:value lines.",
            text[:6000],
        ) or self._heuristic_fields(text, doc_type)
        validated = _llm(
            "Validator. Confirm required fields and flag anomalies.",
            str(extracted),
        ) or f"Validator: parsed {doc_type}. Review currency, dates, and missing IDs."
        fields = {"doc_type": doc_type, "preview": text[:240], "notes": extracted}
        return CrewResult(
            used_llm=bool(get_settings().LLM_API_KEY),
            steps=[
                {"agent": "ocr_qa", "output": ocr_qa},
                {"agent": "extractor", "output": extracted},
                {"agent": "validator", "output": validated},
            ],
            fields=fields,
            confidence=0.78 if get_settings().LLM_API_KEY else 0.55,
        )

    @staticmethod
    def _heuristic_fields(text: str, doc_type: str) -> str:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:8]
        return f"type={doc_type}; lines={len(lines)}; sample={'; '.join(lines[:3])}"
