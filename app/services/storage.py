import os
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

import aiofiles

from app.core.config import get_settings


class StorageBackend(ABC):
    @abstractmethod
    async def save(self, file_bytes: bytes, filename: str, content_type: str) -> str:
        ...

    @abstractmethod
    async def retrieve(self, path: str) -> bytes:
        ...

    @abstractmethod
    async def delete(self, path: str) -> None:
        ...


class LocalStorage(StorageBackend):
    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    async def save(self, file_bytes: bytes, filename: str, content_type: str) -> str:
        ext = Path(filename).suffix
        unique_name = f"{uuid.uuid4().hex}{ext}"
        file_path = self.base_path / unique_name
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(file_bytes)
        return str(file_path)

    async def retrieve(self, path: str) -> bytes:
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    async def delete(self, path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


class S3Storage(StorageBackend):
    """Production S3 storage — requires boto3 and AWS credentials."""

    def __init__(self, bucket: str, region: str):
        import boto3
        self.bucket = bucket
        self.s3 = boto3.client("s3", region_name=region)

    async def save(self, file_bytes: bytes, filename: str, content_type: str) -> str:
        ext = Path(filename).suffix
        key = f"documents/{uuid.uuid4().hex}{ext}"
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=file_bytes, ContentType=content_type)
        return f"s3://{self.bucket}/{key}"

    async def retrieve(self, path: str) -> bytes:
        key = path.replace(f"s3://{self.bucket}/", "")
        response = self.s3.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    async def delete(self, path: str) -> None:
        key = path.replace(f"s3://{self.bucket}/", "")
        self.s3.delete_object(Bucket=self.bucket, Key=key)


def get_storage() -> StorageBackend:
    settings = get_settings()
    if settings.STORAGE_BACKEND == "s3" and settings.S3_BUCKET:
        return S3Storage(settings.S3_BUCKET, settings.S3_REGION or "us-east-1")
    return LocalStorage(settings.STORAGE_PATH)
