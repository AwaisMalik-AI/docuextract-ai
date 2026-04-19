from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "DocuExtract AI"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/docuextract"
    DATABASE_SYNC_URL: str = "postgresql://postgres:postgres@localhost:5432/docuextract"

    REDIS_URL: str = "redis://localhost:6379/0"

    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    ALGORITHM: str = "HS256"

    STORAGE_BACKEND: str = "local"
    STORAGE_PATH: str = "./storage"
    S3_BUCKET: Optional[str] = None
    S3_REGION: Optional[str] = None

    LLM_PROVIDER: str = "openai"
    LLM_MODEL: str = "gpt-4o"
    LLM_API_KEY: Optional[str] = None
    LLM_BASE_URL: Optional[str] = None

    OCR_ENGINE: str = "tesseract"
    OCR_LANGUAGES: str = "eng"

    WEBHOOK_TIMEOUT: int = 30
    MAX_FILE_SIZE_MB: int = 50
    ALLOWED_EXTENSIONS: str = "pdf,png,jpg,jpeg,tiff,bmp"

    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    NOTIFICATION_EMAIL_FROM: str = "noreply@docuextract.ai"

    SLACK_WEBHOOK_URL: Optional[str] = None

    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "*"

    EXPORT_DIR: str = "./storage/exports"
    HTTP_FETCH_TIMEOUT_SEC: float = 120.0
    ANOMALY_Z_THRESHOLD: float = 3.0
    ANOMALY_MIN_HISTORY: int = 5

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
