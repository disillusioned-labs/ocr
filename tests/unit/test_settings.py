"""Settings must fail fast on every combination that cannot work."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ocr_engine.settings import Settings


def base_kwargs() -> dict:
    return {
        "database_dsn": "postgres://ocr_app:pw@localhost:5432/ocr",
        "kafka_brokers": "localhost:9092",
        "allowed_buckets": ["expense-files"],
        "schema_dir": _schemas_dir(),
        "baidu_api_url": "https://x.aistudio-app.com/layout-parsing",
        "baidu_token": "secret-token",
    }


def _schemas_dir():
    import pathlib

    return pathlib.Path(__file__).resolve().parents[2] / "schemas"


def test_valid_settings_pass() -> None:
    settings = Settings(**base_kwargs())
    assert settings.ocr_semaphore >= 1
    assert settings.provider.value == "baidu_aistudio"
    assert settings.baidu_mode.value == "sync"


def test_empty_allowlist_rejected() -> None:
    kwargs = base_kwargs()
    kwargs["allowed_buckets"] = []
    with pytest.raises(ValidationError, match="OCR_ALLOWED_BUCKETS"):
        Settings(**kwargs)


def test_baidu_without_token_rejected() -> None:
    kwargs = base_kwargs()
    del kwargs["baidu_token"]
    with pytest.raises(ValidationError, match="OCR_BAIDU_TOKEN"):
        Settings(**kwargs)


def test_sync_mode_without_api_url_rejected() -> None:
    kwargs = base_kwargs()
    del kwargs["baidu_api_url"]
    with pytest.raises(ValidationError, match="OCR_BAIDU_API_URL"):
        Settings(**kwargs)


def test_http_api_url_rejected() -> None:
    kwargs = base_kwargs()
    kwargs["baidu_api_url"] = "http://x.aistudio-app.com/layout-parsing"
    with pytest.raises(ValidationError, match="https"):
        Settings(**kwargs)


def test_missing_schema_dir_rejected() -> None:
    kwargs = base_kwargs()
    kwargs["schema_dir"] = "/nonexistent-path-xyz"
    with pytest.raises(ValidationError, match="OCR_SCHEMA_DIR"):
        Settings(**kwargs)


def test_async_mode_without_api_url_is_fine() -> None:
    kwargs = base_kwargs()
    del kwargs["baidu_api_url"]
    kwargs["baidu_mode"] = "async"
    settings = Settings(**kwargs)
    assert settings.baidu_mode.value == "async"


def test_shared_vocabulary_env_names() -> None:
    kwargs = base_kwargs()
    kwargs["SERVICE_NAME"] = "ocr-eu"
    kwargs["SERVICE_ENV"] = "staging"
    kwargs["LOG_LEVEL"] = "warning"
    kwargs["LOG_FORMAT"] = "text"
    settings = Settings(**kwargs)
    assert settings.service_name == "ocr-eu"
    assert settings.service_env == "staging"
    assert settings.log_level == "warning"
    assert settings.log_format == "text"


def test_invalid_log_format_rejected() -> None:
    kwargs = base_kwargs()
    kwargs["LOG_FORMAT"] = "yaml"
    with pytest.raises(ValidationError, match="LOG_FORMAT"):
        Settings(**kwargs)


def test_invalid_log_level_rejected() -> None:
    kwargs = base_kwargs()
    kwargs["LOG_LEVEL"] = "verbose"
    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        Settings(**kwargs)


def test_otel_endpoint_requires_scheme() -> None:
    kwargs = base_kwargs()
    kwargs["OTEL_EXPORTER_OTLP_ENDPOINT"] = "localhost:4317"
    with pytest.raises(ValidationError, match="scheme"):
        Settings(**kwargs)


def test_otel_spec_names_accepted() -> None:
    kwargs = base_kwargs()
    kwargs["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
    kwargs["OTEL_SDK_DISABLED"] = "true"
    settings = Settings(**kwargs)
    assert settings.otel_endpoint == "http://localhost:4317"
    assert settings.otel_sdk_disabled is True


def test_sync_max_bytes_above_file_cap_rejected() -> None:
    kwargs = base_kwargs()
    kwargs["sync_max_bytes"] = 99_999_999
    with pytest.raises(ValidationError, match="OCR_SYNC_MAX_BYTES"):
        Settings(**kwargs)
