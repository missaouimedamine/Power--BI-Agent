"""Structured logging.

Every record carries the task context (``task_id``, ``agent``, ``tool``) from context
variables, extra fields passed via ``extra={"fields": {...}}``, and is redacted before
being emitted.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from powerbi_agent.utils.security import redact, redact_text

_task_id: ContextVar[str | None] = ContextVar("task_id", default=None)
_agent: ContextVar[str | None] = ContextVar("agent", default=None)
_tool: ContextVar[str | None] = ContextVar("tool", default=None)

LOGGER_NAME = "powerbi_agent"


@contextmanager
def log_context(
    *, task_id: str | None = None, agent: str | None = None, tool: str | None = None
) -> Iterator[None]:
    """Bind task context for all log records emitted inside the block."""
    tokens = []
    if task_id is not None:
        tokens.append((_task_id, _task_id.set(task_id)))
    if agent is not None:
        tokens.append((_agent, _agent.set(agent)))
    if tool is not None:
        tokens.append((_tool, _tool.set(tool)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def _record_payload(record: logging.LogRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "message": redact_text(record.getMessage()),
        "task_id": _task_id.get(),
        "agent": _agent.get(),
        "tool": _tool.get(),
    }
    fields = getattr(record, "fields", None)
    if isinstance(fields, dict):
        payload.update(redact(fields))
    if record.exc_info:
        payload["error"] = redact_text(logging.Formatter().formatException(record.exc_info))
    return {k: v for k, v in payload.items() if v is not None}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(_record_payload(record), default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = _record_payload(record)
        head = f"{payload.pop('timestamp')} {payload.pop('level'):<8} {payload.pop('message')}"
        payload.pop("logger", None)
        rest = " ".join(f"{k}={v}" for k, v in payload.items())
        return f"{head} {rest}".rstrip()


def configure_logging(level: str = "INFO", fmt: str = "json") -> logging.Logger:
    """Configure the package logger (idempotent). Logs go to stderr, never stdout."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)
