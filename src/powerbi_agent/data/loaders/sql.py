"""SQL loader (SQLAlchemy).

The connection URL is read from the environment variable named by
``DataSource.connection_env``. It never appears in specs, logs or error messages.

Only single, read-only statements (``SELECT`` / ``WITH``) are accepted. This check is a
guard against mistakes, not a security boundary. Also connect with a database user
that has read-only permissions.
"""

from __future__ import annotations

import os
import re

import polars as pl
import sqlalchemy as sa

from powerbi_agent.data.loaders import DataLoadError, LoadedDataset
from powerbi_agent.models.datasource import DataSource
from powerbi_agent.utils.security import redact_text

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_STRINGS = re.compile(r"'(?:[^']|'')*'")
_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|exec|execute|"
    r"call|copy|attach|pragma|vacuum)\b",
    re.IGNORECASE,
)


def check_read_only(query: str) -> None:
    """Raise ``DataLoadError`` unless ``query`` is a single SELECT/WITH statement."""
    code = _STRINGS.sub("''", _COMMENTS.sub(" ", query)).strip().rstrip(";").strip()
    if not code:
        raise DataLoadError("empty SQL query")
    if ";" in code:
        raise DataLoadError("only a single SQL statement is allowed")
    first = code.split(None, 1)[0].lower()
    if first not in {"select", "with"}:
        raise DataLoadError("only SELECT or WITH queries are allowed")
    if match := _WRITE_KEYWORDS.search(code):
        raise DataLoadError(f"query contains a non-read-only keyword: {match.group(0).upper()}")


def load_sql(source: DataSource) -> LoadedDataset:
    assert source.query is not None and source.connection_env is not None
    check_read_only(source.query)
    url = os.environ.get(source.connection_env)
    if not url:
        raise DataLoadError(f"environment variable {source.connection_env} is not set")
    try:
        engine = sa.create_engine(url)
    except (sa.exc.ArgumentError, ImportError) as exc:
        # The message may echo the URL; never let credentials through.
        raise DataLoadError(
            f"invalid connection URL in {source.connection_env}: {redact_text(str(exc))}"
        ) from None
    try:
        with engine.connect() as conn:
            frame = pl.read_database(sa.text(source.query), conn, infer_schema_length=10_000)
    except sa.exc.SQLAlchemyError as exc:
        detail = redact_text(str(getattr(exc, "orig", None) or type(exc).__name__))
        raise DataLoadError(f"query failed: {detail}") from None
    finally:
        engine.dispose()
    return LoadedDataset(source=source, frame=frame)
