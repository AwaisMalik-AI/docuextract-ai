"""Export documents to CSV, Excel, and JSON with optional query filters."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Select, select

from app.core.config import get_settings
from app.models.document import Document

logger = logging.getLogger(__name__)


def _ensure_export_dir() -> Path:
    settings = get_settings()
    path = Path(settings.EXPORT_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def apply_filters(query: Select, filters: dict[str, Any] | None) -> Select:
    """Apply date range, doc_type, and status filters to a Document select."""
    if not filters:
        return query

    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    doc_type = filters.get("doc_type")
    status_val = filters.get("status")

    if date_from:
        try:
            dt = datetime.fromisoformat(str(date_from).replace("Z", "+00:00"))
            query = query.where(Document.created_at >= dt)
        except (TypeError, ValueError):
            logger.warning("Invalid date_from filter: %s", date_from)

    if date_to:
        try:
            dt = datetime.fromisoformat(str(date_to).replace("Z", "+00:00"))
            query = query.where(Document.created_at <= dt)
        except (TypeError, ValueError):
            logger.warning("Invalid date_to filter: %s", date_to)

    if doc_type:
        query = query.where(Document.doc_type == doc_type)
    if status_val:
        query = query.where(Document.status == status_val)

    return query


def _documents_to_rows(documents: list[Document]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in documents:
        row: dict[str, Any] = {
            "id": doc.id,
            "original_filename": doc.original_filename,
            "content_type": doc.content_type,
            "status": doc.status,
            "doc_type": doc.doc_type,
            "source": doc.source,
            "confidence_score": doc.confidence_score,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        }
        extracted = doc.extracted_data or {}
        fields = extracted.get("fields") if isinstance(extracted, dict) else None
        if isinstance(fields, dict):
            for name, data in fields.items():
                if isinstance(data, dict):
                    row[f"field_{name}"] = data.get("value")
                else:
                    row[f"field_{name}"] = data
        elif isinstance(extracted, dict):
            for k, v in extracted.items():
                if k != "fields" and not isinstance(v, (dict, list)):
                    row[f"ext_{k}"] = v
        rows.append(row)
    return rows


class ExportEngine:
    """Write document snapshots to flat files (CSV, Excel, JSON)."""

    def export_csv(self, documents: list[Document], filters: dict | None = None) -> str:
        _ = filters  # filters applied at query level before calling this
        base = _ensure_export_dir()
        out = base / f"export_{uuid.uuid4().hex}.csv"
        rows = _documents_to_rows(documents)
        pd.DataFrame(rows).to_csv(out, index=False)
        logger.info("CSV export written: %s (%s rows)", out, len(rows))
        return str(out.resolve())

    def export_excel(self, documents: list[Document], filters: dict | None = None) -> str:
        _ = filters
        base = _ensure_export_dir()
        out = base / f"export_{uuid.uuid4().hex}.xlsx"
        rows = _documents_to_rows(documents)
        df = pd.DataFrame(rows)
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Documents", index=False)
            summary = pd.DataFrame(
                [
                    {"metric": "record_count", "value": len(documents)},
                    {"metric": "generated_at", "value": datetime.now(timezone.utc).isoformat()},
                ]
            )
            summary.to_excel(writer, sheet_name="Summary", index=False)
        logger.info("Excel export written: %s (%s rows)", out, len(rows))
        return str(out.resolve())

    def export_json(self, documents: list[Document], filters: dict | None = None) -> str:
        _ = filters
        base = _ensure_export_dir()
        out = base / f"export_{uuid.uuid4().hex}.json"
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "record_count": len(documents),
            "documents": _documents_to_rows(documents),
        }
        out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        logger.info("JSON export written: %s (%s rows)", out, len(documents))
        return str(out.resolve())


def build_filtered_documents_query(filters: dict | None) -> Select:
    q = select(Document).order_by(Document.created_at.desc())
    return apply_filters(q, filters)


def get_export_engine() -> ExportEngine:
    return ExportEngine()
