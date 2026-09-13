"""Structured logging: one JSON pipeline for app and library logs, trace-correlated.

Mirrors platform/telemetry in the Go services: every line the process emits is
JSON carrying trace_id/span_id when a span is active - structlog output and
third-party libraries (grpcio, saq, botocore) alike, because a log stream that
switches shape mid-file is unqueryable.
"""

from __future__ import annotations

import logging
import sys

import structlog

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def _inject_trace(
    logger: logging.Logger, method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
    except Exception as exc:  # logging must never fail the request
        event_dict.setdefault("trace_inject_error", str(exc))
    return event_dict


_SHARED_PROCESSORS = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    _inject_trace,
    structlog.processors.StackInfoRenderer(),
]


def configure_logging(level: str = "info", fmt: str = "json", stream=None) -> None:
    """Configure structlog and route stdlib logs (the libraries) through the
    same formatter, so both render identically."""
    numeric = _LEVELS.get(level.upper(), logging.INFO)

    structlog.configure(
        processors=[*_SHARED_PROCESSORS, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    if fmt == "text":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(numeric)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
