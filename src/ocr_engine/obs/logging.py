"""structlog JSON logging with trace correlation."""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "info", service: str = "ocr", env: str = "development") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    processor_list: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _inject_trace,
        structlog.processors.StackInfoRenderer(),
    ]
    if env == "development":
        processor_list.append(structlog.dev.ConsoleRenderer())
    processor_list.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processor_list,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level.upper()]),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.getLogger(service).handlers = []


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


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
