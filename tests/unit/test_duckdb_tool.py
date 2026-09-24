from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from powerbi_agent.tools.duckdb import DuckDBSession, QueryError, quote_identifier


def test_query_registered_frame() -> None:
    with DuckDBSession() as db:
        db.register("sales", pl.DataFrame({"region": ["N", "S", "N"], "rev": [1.0, 2.0, 3.0]}))
        out = db.query("SELECT region, SUM(rev) AS total FROM sales GROUP BY 1 ORDER BY 1")
    assert out.to_dicts() == [{"region": "N", "total": 4.0}, {"region": "S", "total": 2.0}]


def test_files_cannot_be_read(sales_csv: Path) -> None:
    with DuckDBSession() as db, pytest.raises(QueryError):
        db.query(f"SELECT * FROM read_csv('{sales_csv.as_posix()}')")


@pytest.mark.parametrize(
    "sql",
    ["SET enable_external_access = true", "CREATE TABLE x (a INT)", "SELECT 1; SELECT 2"],
)
def test_non_read_only_rejected(sql: str) -> None:
    with DuckDBSession() as db, pytest.raises(QueryError):
        db.query(sql)


def test_invalid_table_name() -> None:
    with DuckDBSession() as db, pytest.raises(QueryError):
        db.register("x; drop", pl.DataFrame({"a": [1]}))


def test_quote_identifier_handles_hostile_names() -> None:
    name = 'Revenue") FROM t; --'
    with DuckDBSession() as db:
        db.register("t", pl.DataFrame({name: [1.0, 2.0]}))
        out = db.query(f"SELECT SUM({quote_identifier(name)}) AS s FROM t")
    assert out.item() == 3.0
