"""Locked-down DuckDB session for SQL over in-memory frames.

The connection is in-memory with ``enable_external_access = false``: queries can read
only frames registered by the caller, never files, URLs or extensions, and the setting
cannot be re-enabled from SQL. Only single read-only statements are accepted.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

import duckdb
import polars as pl

from powerbi_agent.data.loaders import DataLoadError
from powerbi_agent.data.loaders.sql import check_read_only


class QueryError(Exception):
    pass


class DuckDBSession:
    def __init__(self) -> None:
        self._con = duckdb.connect(":memory:", config={"enable_external_access": False})

    def register(self, name: str, frame: pl.DataFrame) -> None:
        if not name.isidentifier():
            raise QueryError(f"invalid table name: {name!r}")
        self._con.register(name, frame.to_arrow())

    def query(self, sql: str) -> pl.DataFrame:
        try:
            check_read_only(sql)
        except DataLoadError as exc:
            raise QueryError(str(exc)) from None
        try:
            return pl.from_arrow(self._con.execute(sql).arrow())  # type: ignore[return-value]
        except duckdb.Error as exc:
            raise QueryError(f"{type(exc).__name__}: {exc}") from None

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def quote_identifier(name: str) -> str:
    """Quote a column name for DuckDB SQL (column names come from data: never trust them)."""
    return '"' + name.replace('"', '""') + '"'
