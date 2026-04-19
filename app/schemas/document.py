from datetime import datetime
from typing import Optional

from pydantic import BaseModel, HttpUrl


class DocumentUploadResponse(BaseModel):
    id: str
    filename: str
    status: str
    message: str


class ExtractionFieldResponse(BaseModel):
    field_name: str
    field_value: Optional[str]
    confidence: float
    was_corrected: bool
    corrected_value: Optional[str] = None

    model_config = {"from_attributes": True}


class DocumentResponse(BaseModel):
    id: str
    original_filename: str
    content_type: str
    file_size: int
    page_count: int
    status: str
    doc_type: Optional[str]
    source: str
    confidence_score: Optional[float]
    extracted_data: Optional[dict]
    validation_results: Optional[dict]
    processing_time_ms: Optional[int]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int
    page: int
    page_size: int


class ReviewRequest(BaseModel):
    action: str  # approve, reject, correct
    corrections: Optional[dict[str, str]] = None
    notes: Optional[str] = None


class WebhookConfigRequest(BaseModel):
    name: str
    url: HttpUrl
    events: list[str]
    headers: Optional[dict[str, str]] = None


class ValidationRuleRequest(BaseModel):
    name: str
    description: Optional[str] = None
    rule_type: str
    config: dict
    severity: str = "warning"
    doc_types: Optional[list[str]] = None


class DocumentStatsResponse(BaseModel):
    total_documents: int
    by_status: dict[str, int]
    by_type: dict[str, int]
    avg_confidence: Optional[float]
    avg_processing_time_ms: Optional[float]
    documents_today: int
