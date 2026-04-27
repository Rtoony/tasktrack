"""Logging configuration.

Two formats, picked by `LOG_FORMAT` (from the deployment profile):
- `text` — human-readable single-line records (personal default)
- `structured` — JSON per record (company default; greppable / shipper-friendly)

Every record carries a `request_id` field, populated by the request-ID
middleware (see app/middleware.py). Records logged outside a request
context show `request_id="-"`.
"""
import json
import logging
import time
from logging.config import dictConfig


class RequestIdFilter(logging.Filter):
    """Inject g.request_id (or '-') into every record."""

    def filter(self, record):
        try:
            from flask import g, has_request_context
            record.request_id = g.get("request_id", "-") if has_request_context() else "-"
        except Exception:
            record.request_id = "-"
        return True


class JsonFormatter(logging.Formatter):
    """Single-line JSON per record. Keep keys stable for grepping."""

    def format(self, record):
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Bring through any extra=… kwargs the caller passed.
        for key, value in record.__dict__.items():
            if key in payload or key in (
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "levelname", "levelno", "lineno",
                "module", "msecs", "msg", "name", "pathname", "process",
                "processName", "relativeCreated", "stack_info", "thread",
                "threadName", "request_id", "taskName",
            ):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging(log_format: str = "text", level: str = "INFO") -> None:
    formatter_name = "json" if log_format == "structured" else "text"
    dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {"()": "app.logging_config.RequestIdFilter"},
        },
        "formatters": {
            "text": {
                "format": "%(asctime)s %(levelname)s %(name)s req=%(request_id)s %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "json": {
                "()": "app.logging_config.JsonFormatter",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": formatter_name,
                "filters": ["request_id"],
            },
        },
        "root": {
            "level": level,
            "handlers": ["console"],
        },
        "loggers": {
            # Flask + werkzeug are noisy at INFO; tune individually if needed.
            "werkzeug": {"level": "WARNING", "propagate": True},
            "tasktrack": {"level": level, "propagate": True},
        },
    })
