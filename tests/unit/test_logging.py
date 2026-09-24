from __future__ import annotations

import json
import logging

import pytest

from powerbi_agent.utils.logging import JsonFormatter, get_logger, log_context


def _format(record: logging.LogRecord) -> dict[str, object]:
    return json.loads(JsonFormatter().format(record))  # type: ignore[no-any-return]


def _record(msg: str, **fields: object) -> logging.LogRecord:
    record = logging.LogRecord("powerbi_agent.test", logging.INFO, __file__, 1, msg, None, None)
    record.fields = fields
    return record


def test_context_fields_are_included() -> None:
    with log_context(task_id="abc123", agent="dax_agent", tool="mcp"):
        payload = _format(_record("create_measure", measure="Profit Margin", status="success"))
    assert payload["task_id"] == "abc123"
    assert payload["agent"] == "dax_agent"
    assert payload["tool"] == "mcp"
    assert payload["measure"] == "Profit Margin"
    assert "timestamp" in payload


def test_context_is_reset_after_block() -> None:
    with log_context(task_id="abc123"):
        pass
    assert "task_id" not in _format(_record("x"))


def test_secrets_never_logged() -> None:
    payload = _format(_record("token Bearer abc.def", client_secret="s3cr3t"))
    text = json.dumps(payload)
    assert "s3cr3t" not in text
    assert "abc.def" not in text


def test_get_logger_namespacing(caplog: pytest.LogCaptureFixture) -> None:
    assert get_logger("x").name == "powerbi_agent.x"
