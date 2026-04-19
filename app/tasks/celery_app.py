from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery = Celery(
    "docuextract",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_soft_time_limit=300,
    task_time_limit=600,
    worker_max_tasks_per_child=50,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="documents",
    task_routes={
        "docuextract.process_document": {"queue": "documents"},
        "docuextract.post_approval_actions": {"queue": "webhooks"},
        "docuextract.generate_export": {"queue": "documents"},
        "docuextract.scheduled_fetch": {"queue": "documents"},
        "docuextract.check_scheduled_extractions": {"queue": "documents"},
        "docuextract.anomaly_check_task": {"queue": "documents"},
    },
    beat_schedule={
        "check_scheduled_extractions": {
            "task": "docuextract.check_scheduled_extractions",
            "schedule": 300.0,
        },
        "check_anomalies": {
            "task": "docuextract.anomaly_check_task",
            "schedule": crontab(hour=2, minute=0),
        },
    },
)

# Import tasks so Celery registers them when the worker loads this module.
from app.tasks import processing  # noqa: E402, F401
