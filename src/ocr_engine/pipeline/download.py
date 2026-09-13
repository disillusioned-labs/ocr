"""Object storage download with an allowlist and no URL fetching (no SSRF surface)."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from pathlib import Path

import aioboto3
from opentelemetry.trace import SpanKind

from ..obs.tracing import tracer
from ..settings import Settings


class StorageObjectMissing(Exception):
    pass


def _validate_ref(settings: Settings, bucket: str, path: str) -> None:
    if bucket not in settings.allowed_buckets:
        raise ValueError(f"bucket {bucket!r} is not in the allowlist")
    if not path or ".." in Path(path).parts or path.startswith("/"):
        raise ValueError(f"invalid storage path {path!r}")


def _client_kwargs(settings: Settings) -> dict:
    kwargs: dict = {"region_name": settings.storage_region}
    if settings.storage_endpoint_url:
        kwargs["endpoint_url"] = settings.storage_endpoint_url
    if settings.storage_access_key_id and settings.storage_secret_access_key:
        kwargs["aws_access_key_id"] = settings.storage_access_key_id
        kwargs["aws_secret_access_key"] = settings.storage_secret_access_key.get_secret_value()
    return kwargs


async def download_to_temp(settings: Settings, bucket: str, path: str, *, suffix: str = "") -> Path:
    _validate_ref(settings, bucket, path)
    session = aioboto3.Session()
    async with session.client("s3", **_client_kwargs(settings)) as s3:
        with tracer("pipeline.download").start_as_current_span(
            "download", kind=SpanKind.CLIENT, attributes={"ocr.bucket": bucket, "ocr.path": path}
        ):
            try:
                response = await s3.get_object(Bucket=bucket, Key=path)
                stream = response["Body"]
            except s3.exceptions.NoSuchKey:
                raise StorageObjectMissing(f"{bucket}/{path}") from None

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix or _suffix_of(path))
            try:
                downloaded = 0
                while chunk := await stream.read(1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > settings.max_file_bytes:
                        raise ValueError(f"object exceeds OCR_MAX_FILE_BYTES ({settings.max_file_bytes})")
                    tmp.write(chunk)
                tmp.close()
                return Path(tmp.name)
            except Exception:
                tmp.close()
                await asyncio.to_thread(_unlink_quiet, Path(tmp.name))
                raise


def _suffix_of(path: str) -> str:
    suffix = Path(path).suffix
    return suffix if suffix else ""


def _unlink_quiet(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def cleanup_temp(path: Path | None) -> None:
    if path is not None:
        _unlink_quiet(path)
