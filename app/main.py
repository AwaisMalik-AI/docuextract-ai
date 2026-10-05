import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import init_db

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    await init_db()
    logger.info("Database initialized")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    description="Production AI document intelligence, OCR, and data pipeline platform. "
    "Ingest from upload, email, URL, or cloud — run OCR (Tesseract + OpenCV) and LLM extraction, "
    "validate with rules, approval workflows, and webhooks. "
    "Export to CSV, Excel, and JSON; schedule URL extractions with cron; "
    "alert via email and Slack with anomaly detection on extracted numerics.",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

origins = settings.CORS_ORIGINS.split(",") if settings.CORS_ORIGINS != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.api.routes import alerts, auth, compare, crews, documents, exports, rules, schedules, webhooks

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(webhooks.router)
app.include_router(rules.router)
app.include_router(exports.router)
app.include_router(schedules.router)
app.include_router(alerts.router)
app.include_router(crews.router)
app.include_router(compare.router)


@app.get("/health", tags=["System"])
async def health():
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }


@app.get("/", tags=["System"])
async def root():
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/health",
    }
