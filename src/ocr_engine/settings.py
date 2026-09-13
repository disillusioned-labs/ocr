"""Environment configuration. Fail-fast: a wrong value must surface at boot."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, NoDecode

SCHEMA_ID_PATTERN = r"^[a-z_]+@[0-9]+$"

BAIDU_SYNC_ERROR_PERMANENT = {400, 403, 413, 422}
BAIDU_RETRYABLE_HTTP = {429, 503, 504}
BAIDU_ASYNC_ERROR_PERMANENT = {
    401,
    10001,
    10002,
    10003,
    10004,
    10005,
    10006,
    10007,
    10008,
    11001,
    11002,
    11003,
}


class Provider(StrEnum):
    BAIDU_AISTUDIO = "baidu_aistudio"
    PADDLEOCR_LOCAL = "paddleocr_local"


class BaiduMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OCR_", env_file=".env", extra="ignore")

    database_dsn: str
    redis_url: str = "redis://localhost:6379/0"
    kafka_brokers: str
    kafka_topic: str = "ocr.document.processed.v1"

    allowed_buckets: Annotated[list[str], NoDecode]
    max_file_bytes: int = 26_214_400

    @field_validator("allowed_buckets", mode="before")
    @classmethod
    def _split_buckets(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value
    max_pages: int = 10
    max_image_dim: int = 4000

    grpc_port: int = 9094
    worker_concurrency: int = 8
    semaphore_override: int | None = None
    storage_endpoint_url: str | None = None
    storage_access_key_id: str | None = None
    storage_secret_access_key: SecretStr | None = None
    storage_region: str = "us-east-1"

    schema_dir: Path = Path("./schemas")

    provider: Provider = Provider.BAIDU_AISTUDIO
    baidu_api_url: str | None = None
    baidu_async_url: str = "https://paddleocr.aistudio-app.com"
    baidu_mode: BaiduMode = BaiduMode.SYNC
    baidu_unwarp: bool = True
    baidu_poll_interval: float = 5.0
    baidu_poll_timeout: float = 300.0
    baidu_token: SecretStr | None = None
    baidu_timeout: float = 90.0

    log_level: str = "info"
    otel_endpoint: str | None = None
    service_name: str = "ocr"
    service_env: str = "development"

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        problems: list[str] = []
        if not self.database_dsn:
            problems.append("OCR_DATABASE_DSN must not be empty")
        if not self.kafka_brokers:
            problems.append("OCR_KAFKA_BROKERS must not be empty")
        if not self.allowed_buckets:
            problems.append("OCR_ALLOWED_BUCKETS must not be empty - an empty allowlist reads nothing")
        if self.max_file_bytes <= 0 or self.max_pages <= 0 or self.max_image_dim <= 0:
            problems.append("OCR_MAX_FILE_BYTES / OCR_MAX_PAGES / OCR_MAX_IMAGE_DIM must be > 0")
        if self.worker_concurrency <= 0:
            problems.append("OCR_WORKER_CONCURRENCY must be > 0")
        if not self.schema_dir.is_dir():
            problems.append(f"OCR_SCHEMA_DIR does not exist: {self.schema_dir}")

        if self.provider is Provider.BAIDU_AISTUDIO:
            if self.baidu_token is None or not self.baidu_token.get_secret_value():
                problems.append("OCR_BAIDU_TOKEN is required when OCR_PROVIDER=baidu_aistudio")
            if self.baidu_mode is BaiduMode.SYNC and not self.baidu_api_url:
                problems.append("OCR_BAIDU_API_URL is required when OCR_BAIDU_MODE=sync")
            if self.baidu_api_url and not self.baidu_api_url.startswith("https://"):
                problems.append("OCR_BAIDU_API_URL must be https")
            if self.baidu_mode is BaiduMode.ASYNC and not self.baidu_async_url.startswith("https://"):
                problems.append("OCR_BAIDU_ASYNC_URL must be https")
            if self.baidu_poll_interval <= 0 or self.baidu_poll_timeout <= 0:
                problems.append("OCR_BAIDU_POLL_INTERVAL / OCR_BAIDU_POLL_TIMEOUT must be > 0")
        if problems:
            raise ValueError("invalid settings:\n" + "\n".join(problems))
        return self

    @property
    def ocr_semaphore(self) -> int:
        if self.semaphore_override is not None and self.semaphore_override > 0:
            return self.semaphore_override
        return max(1, (os_cpu_count() or 2) // 2)


def os_cpu_count() -> int | None:
    import os

    return os.cpu_count()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
