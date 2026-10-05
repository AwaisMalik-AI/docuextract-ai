# DocuExtract AI — Production AI Document Intelligence, OCR & Data Pipeline Platform

DocuExtract AI is a full-stack document intelligence platform: ingest from **upload, email, URL, or cloud**, run **OCR (Tesseract + OpenCV)** and **LLM extraction**, validate with a **rules engine**, route through **human approval**, store structured records, and push events to downstream systems via **webhooks**. It also provides **scheduled URL extraction** (cron), **bulk export** (CSV, Excel, JSON), **multi-channel alerting** (email, Slack), and **numeric anomaly detection** (z-score vs historical documents of the same type).

**Latest:** Extraction crew plus **field-level document compare** (`POST /compare/fields`) for extraction drift.

---

## Architecture (ASCII)

```
  Sources:  [ Upload ] [ Email ] [ URL / Scheduled ] [ Cloud watch ]
                              |
                              v
         +-------------------------------------------------------------+
         |  OCR Pipeline (Tesseract + OpenCV preprocessing, PyMuPDF)    |
         +---------------------------+---------------------------------+
                                     v
         +-------------------------------------------------------------+
         |  LLM Extraction (structured fields + per-field confidence)   |
         +---------------------------+---------------------------------+
                                     v
         +-------------------------------------------------------------+
         |  Validation Engine (rules: required, format, range, dupes)    |
         +---------------------------+---------------------------------+
                                     v
         +-------------------------------------------------------------+
         |  Approval Workflow (auto vs pending_review / corrections)    |
         +---------------------------+---------------------------------+
                                     v
                          [ Record Store — PostgreSQL ]
                                     |
         +---------------------------+---------------------------------+
         |  Export Engine — CSV / Excel / JSON (async via Celery)       |
         |  Scheduled Extractions — croniter + Celery Beat (5 min)     |
         |  Alerting — Email / Slack + in-app Alert model               |
         |  Webhook Delivery — per config + per-document URLs         |
         +-------------------------------------------------------------+
```

---

## Document Processing Pipeline

1. **Ingest** — File bytes land in local or S3-backed storage; metadata and state are persisted.
2. **OCR preprocessing** — OpenCV (deskew, denoise, threshold) where applicable; PDF text via PyMuPDF with raster fallback.
3. **LLM extraction** — Provider-agnostic structured JSON with `doc_type` detection and per-field confidence.
4. **Validation** — Configurable rules (API-managed) with severity and doc-type scoping.
5. **Approval** — High confidence + valid → automated path; otherwise **pending_review** with audit trail and corrections.
6. **Post-approval** — Webhooks and optional per-document callback URL; status transitions to **posted**.

---

## Scheduled Extraction from URLs

- **`ScheduledExtraction`** rows store `source_url`, `cron_expression`, `next_run_at`, `last_run_at`, and `is_active`.
- **Celery Beat** runs **`docuextract.check_scheduled_extractions`** every **5 minutes**, enqueueing **`scheduled_fetch`** per due schedule.
- Fetched bytes are stored like uploads, a **`Document`** is created with `source="url"`, and the standard **`process_document`** pipeline runs.
- Failures raise **`extraction_failed`** alerts (persisted + optional email/Slack).

---

## Export System (CSV, Excel, JSON)

- **`POST /api/exports`** creates an **`ExportJob`** (`pending` → `generating` → `completed` / `failed`).
- **`generate_export`** (Celery) applies **filters** (`date_from`, `date_to`, `doc_type`, `status`) via `ExportEngine.apply_filters`, then writes:
  - **CSV** — `pandas` + flat field columns
  - **Excel** — `openpyxl`, **Documents** sheet + **Summary** sheet
  - **JSON** — records + metadata
- **`GET /api/exports/{id}/download`** serves the file (path constrained under `EXPORT_DIR`).
- On success, an **`export_ready`** alert is recorded and notifications fire if SMTP/Slack are configured.

---

## Alerting and Anomaly Detection

- **`Alert`** model: `extraction_failed`, `anomaly_detected`, `validation_warning`, `export_ready`; optional `document_id`; `notified_via` records channels used.
- **`AlertService.check_anomalies`** compares numeric fields in `extracted_data` to historical documents with the same **`doc_type`**; flags **|z| > ANOMALY_Z_THRESHOLD** (default **3.0**) when history size ≥ **ANOMALY_MIN_HISTORY** (default **5**).
- **Celery Beat** runs **`docuextract.anomaly_check_task` daily** (02:00 UTC) over recently updated documents with extraction payloads.
- **`_send_email_alert`** / **`_send_slack_alert`** use existing SMTP and `SLACK_WEBHOOK_URL` settings.

---

## Webhook Delivery for Downstream Systems

- Global **`WebhookConfig`** entries (admin API) fire on configured events (e.g. **approved**).
- Per-upload **`webhook_url`** on **`Document`** is honored in **`post_approval_actions`**.
- Retries and timeouts are controlled via **`WEBHOOK_TIMEOUT`** and Celery retry policy.

---

## Cloud Deployment (reference)

| Concern | AWS-oriented pattern |
|--------|----------------------|
| Object storage | **S3** for uploads and export artifacts (`STORAGE_BACKEND=s3`) |
| Async work | **SQS** as Celery broker (with **Redis**-compatible bridge or Celery SQS transport) / or **Redis** on ElastiCache |
| Schedules | **EventBridge** → Lambda → enqueue **`check_scheduled_extractions`** / beat on a small always-on worker |
| API | **ECS/Fargate** or **EKS** behind **ALB**; **RDS PostgreSQL** for metadata |
| Observability | **CloudWatch** + **Prometheus** exporters for app/worker metrics |

Tune **`EXPORT_DIR`**, **`HTTP_FETCH_TIMEOUT_SEC`**, and worker concurrency for your environment.

---

## CI/CD

- GitHub Actions workflow **`.github/workflows/ci.yml`** runs on **push/PR** to `main`/`master`:
  - Installs **`requirements.txt`** + **pytest**
  - **`python -m compileall app`**
  - **`pytest tests/`**
- Extend with Docker image build/push, migration job (**Alembic**), and deploy hooks as needed.

---

## Observability

- **Structured logging** — `LOG_LEVEL`, timestamped lines from API and workers.
- **Prometheus** — expose metrics from FastAPI (e.g. `prometheus-fastapi-instrumentator`) and Celery worker exporters in production.
- **Traces** — optional OpenTelemetry for cross-service correlation (API → worker → external LLM).

---

## API Endpoints (expanded)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register` | Register user |
| POST | `/api/auth/login` | JWT access token |
| GET | `/api/auth/me` | Current profile |
| POST | `/api/documents/upload` | Upload + queue processing |
| GET | `/api/documents/` | List / filter / paginate |
| GET | `/api/documents/stats` | Aggregates |
| GET | `/api/documents/{id}` | Detail |
| POST | `/api/documents/{id}/review` | Approve / reject / correct |
| POST | `/api/documents/{id}/reprocess` | Re-queue |
| POST | `/api/exports` | Create export job (Celery) |
| GET | `/api/exports` | List current user’s jobs |
| GET | `/api/exports/{id}/download` | Download completed export |
| POST | `/api/schedules` | Create scheduled URL extraction |
| GET | `/api/schedules` | List schedules (own; admins see all) |
| PATCH | `/api/schedules/{id}` | Update cron / active flag |
| DELETE | `/api/schedules/{id}` | Remove schedule |
| GET | `/api/alerts` | List alerts (`unread_only` optional) |
| PATCH | `/api/alerts/{id}/read` | Mark read |
| GET | `/api/alerts/unread-count` | Unread total |
| POST | `/api/webhooks/` | Configure webhook |
| GET | `/api/webhooks/` | List webhooks |
| DELETE | `/api/webhooks/{id}` | Deactivate |
| POST | `/api/rules/` | Create validation rule |
| GET | `/api/rules/` | List rules |
| PATCH | `/api/rules/{id}` | Toggle active |
| DELETE | `/api/rules/{id}` | Delete rule |
| GET | `/health` | Health |
| GET | `/` | Service metadata |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| API | **FastAPI**, **Pydantic v2**, **Uvicorn** |
| Database | **PostgreSQL** + **SQLAlchemy 2** (async) |
| Workers | **Celery 5** + **Redis**; **Celery Beat** |
| OCR / PDF | **Tesseract**, **OpenCV**, **PyMuPDF**, **Pillow** |
| Exports | **pandas**, **openpyxl** |
| Scheduling | **croniter**, **httpx** (sync fetch in workers) |
| Storage | Local path or **S3** (`boto3`) |
| Auth | **JWT**, **passlib** / **bcrypt** |

---

## Quick Start

```bash
git clone https://github.com/yourusername/docuextract-ai.git
cd docuextract-ai
cp .env.example .env   # configure LLM, DB, Redis, optional SMTP/Slack

docker compose up --build
# API: http://localhost:8000  |  Docs: /docs
```

**Workers (local)**

```bash
celery -A app.tasks.celery_app:celery worker --loglevel=info -Q documents,webhooks
celery -A app.tasks.celery_app:celery beat --loglevel=info
```

---

## Project Structure (high level)

```
app/
  main.py                 # FastAPI app + routers
  core/                   # config, database, security, deps
  api/routes/             # auth, documents, exports, schedules, alerts, webhooks, rules
  models/                 # User, Document, schedules, exports, alerts, rules, webhooks
  schemas/                # Pydantic models
  services/               # ocr, extractor, validator, storage, notifications, export_engine, scheduler, alerting
  tasks/                  # celery_app (beat schedule), processing tasks
tests/
.github/workflows/ci.yml
```

---

## Scaling

- **Stateless API** — horizontal scale behind a load balancer.
- **Workers** — scale Celery consumers on queue depth; separate queues for **documents** vs **webhooks** as already routed.
- **Exports** — CPU/IO heavy; dedicate workers and shared **`EXPORT_DIR`** or upload artifacts to S3 post-write.
- **DB** — connection pooling (async), read replicas for reporting, indexes on **`next_run_at`**, **`status`**, **`is_read`**.

---

## License

MIT
