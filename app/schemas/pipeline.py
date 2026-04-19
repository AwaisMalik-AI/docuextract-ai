from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, HttpUrl


class ExportFilters(BaseModel):
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    doc_type: Optional[str] = None
    status: Optional[str] = None


class ExportJobCreate(BaseModel):
    export_format: str = Field(..., pattern="^(csv|excel|json)$")
    filters: Optional[dict[str, Any]] = None


class ExportJobResponse(BaseModel):
    id: str
    user_id: str
    export_format: str
    filters: Optional[dict[str, Any]] = None
    status: str
    file_path: Optional[str] = None
    record_count: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ExportJobListResponse(BaseModel):
    jobs: list[ExportJobResponse]
    total: int


class ScheduleCreate(BaseModel):
    source_name: str = Field(..., min_length=1, max_length=200)
    source_url: HttpUrl
    cron_expression: str = Field(..., min_length=1, max_length=200)


class ScheduleUpdate(BaseModel):
    cron_expression: Optional[str] = Field(None, min_length=1, max_length=200)
    is_active: Optional[bool] = None


class ScheduleResponse(BaseModel):
    id: str
    source_name: str
    source_url: str
    cron_expression: str
    next_run_at: datetime
    last_run_at: Optional[datetime] = None
    is_active: bool
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertResponse(BaseModel):
    id: str
    document_id: Optional[str] = None
    alert_type: str
    message: str
    is_read: bool
    notified_via: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertListResponse(BaseModel):
    alerts: list[AlertResponse]
    total: int


class UnreadCountResponse(BaseModel):
    count: int
