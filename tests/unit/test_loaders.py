from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import openpyxl
import polars as pl
import pytest

from powerbi_agent.data.loaders import DataLoadError, load_source
from powerbi_agent.data.loaders.sql import check_read_only
from powerbi_agent.models.datasource import DataSource, DataSourceType


# --- CSV ------------------------------------------------------------------------------------
def test_csv_basic(sales_csv: Path) -> None:
    ds = load_source(DataSource.from_path(sales_csv))
    assert ds.frame.shape == (6, 10)
    assert ds.frame.schema["OrderDate"] == pl.Date
    assert ds.frame.get_column("CustomerID").null_count() == 1


def test_csv_semicolon_is_sniffed(tmp_path: Path) -> None:
    path = tmp_path / "eu.csv"
    path.write_text("Region;Revenue\nNorth;10\nSouth;20\n", encoding="utf-8")
    ds = load_source(DataSource.from_path(path))
    assert ds.frame.columns == ["Region", "Revenue"]
    assert any("delimiter" in n for n in ds.notes)


def test_csv_utf8_bom_is_stripped(tmp_path: Path) -> None:
    path = tmp_path / "bom.csv"
    path.write_bytes("﻿Name,Value\nA,1\n".encode())
    assert load_source(DataSource.from_path(path)).frame.columns == ["Name", "Value"]


def test_csv_cp1252_fallback_is_noted(tmp_path: Path) -> None:
    path = tmp_path / "latin.csv"
    path.write_bytes("City,Value\nMünchen,1\n".encode("cp1252"))
    ds = load_source(DataSource.from_path(path))
    assert ds.frame.item(0, "City") == "München"
    assert any("cp1252" in n for n in ds.notes)


def test_csv_na_is_a_value_not_null(tmp_path: Path) -> None:
    path = tmp_path / "regions.csv"
    path.write_text("Region,Value\nNA,1\nEMEA,2\n,3\n", encoding="utf-8")
    col = load_source(DataSource.from_path(path)).frame.get_column("Region")
    assert col.to_list() == ["NA", "EMEA", None]


def test_csv_unreadable_file_raises_clean_error(tmp_path: Path) -> None:
    with pytest.raises(DataLoadError, match="cannot read"):
        load_source(DataSource.from_path(tmp_path / "missing.csv"))


# --- Excel ----------------------------------------------------------------------------------
def _workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Orders"
    ws.append(["OrderDate", "Amount", "Formula"])
    ws.append([datetime(2025, 1, 5), 10.5, "=1+1"])
    ws.append([datetime(2025, 2, 1), 20.0, "=1+1"])
    wb.create_sheet("Lookup").append(["Code", "Label"])
    wb.save(path)


def test_excel_first_sheet_and_note(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    _workbook(path)
    ds = load_source(DataSource.from_path(path))
    assert ds.source.sheet == "Orders"
    assert ds.available_sheets == ["Orders", "Lookup"]
    assert any("Lookup" in n for n in ds.notes)
    # Midnight datetimes are normalised to dates.
    assert ds.frame.schema["OrderDate"] == pl.Date
    assert ds.frame.item(0, "OrderDate") == date(2025, 1, 5)


def test_excel_formulas_are_not_evaluated(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    _workbook(path)
    # No cached value exists (never opened in Excel), so the cell is empty, not computed.
    values = load_source(DataSource.from_path(path)).frame.get_column("Formula").to_list()
    assert 2 not in values


def test_excel_explicit_sheet(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    _workbook(path)
    with pytest.raises(DataLoadError, match="not found"):
        load_source(DataSource.from_path(path, sheet="Nope"))


def test_excel_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"not a zip")
    with pytest.raises(DataLoadError):
        load_source(DataSource.from_path(path))


# --- Parquet --------------------------------------------------------------------------------
def test_parquet_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "data.parquet"
    pl.DataFrame({"a": [1, 2], "b": ["x", "y"]}).write_parquet(path)
    assert load_source(DataSource.from_path(path)).frame.shape == (2, 2)


# --- SQL ------------------------------------------------------------------------------------
@pytest.fixture
def sqlite_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    db = tmp_path / "sales.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table sales (region text, revenue real)")
        conn.executemany("insert into sales values (?, ?)", [("North", 10.0), ("South", 5.5)])
    url = f"sqlite:///{db.as_posix()}"
    monkeypatch.setenv("TEST_DB_URL", url)
    return url


def _sql(query: str, env: str = "TEST_DB_URL") -> DataSource:
    return DataSource(name="q", type=DataSourceType.SQL, query=query, connection_env=env)


def test_sql_select(sqlite_url: str) -> None:
    ds = load_source(_sql("SELECT region, revenue FROM sales ORDER BY region"))
    assert ds.frame.get_column("revenue").to_list() == [10.0, 5.5]


def test_sql_missing_env_var() -> None:
    with pytest.raises(DataLoadError, match="NOPE_URL is not set"):
        load_source(_sql("select 1", env="NOPE_URL"))


def test_sql_error_does_not_leak_url(sqlite_url: str) -> None:
    with pytest.raises(DataLoadError) as info:
        load_source(_sql("select * from missing_table"))
    assert "sales.db" not in str(info.value)


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM sales",
        "select 1; drop table sales",
        "WITH x AS (select 1) INSERT INTO sales select * from x",
        "update sales set revenue = 0",
        "",
    ],
)
def test_sql_non_read_only_rejected(query: str) -> None:
    with pytest.raises(DataLoadError):
        check_read_only(query)


@pytest.mark.parametrize(
    "query",
    [
        "select update_date, 'drop table' as label from t",  # keywords inside names/strings
        "-- comment; with semicolon\nSELECT 1",
        "select replace(name, 'a', 'b') from t;",
    ],
)
def test_sql_read_only_accepted(query: str) -> None:
    check_read_only(query)
