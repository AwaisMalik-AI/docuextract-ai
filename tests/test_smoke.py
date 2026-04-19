"""Lightweight import and routing smoke checks (no DB required)."""


def test_app_import_and_routes():
    from fastapi.routing import APIRoute

    from app.main import app

    assert app.title == "DocuExtract AI"
    paths = [r.path for r in app.routes if isinstance(r, APIRoute)]
    assert any(p.startswith("/api/exports") for p in paths)
    assert any(p.startswith("/api/schedules") for p in paths)
    assert any(p.startswith("/api/alerts") for p in paths)


def test_celery_beat_schedule_configured():
    from app.tasks.celery_app import celery

    assert "check_scheduled_extractions" in (celery.conf.beat_schedule or {})
    assert "check_anomalies" in (celery.conf.beat_schedule or {})
