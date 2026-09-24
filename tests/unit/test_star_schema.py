from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from powerbi_agent.data.loaders import LoadedDataset
from powerbi_agent.data.profiler import profile_dataset
from powerbi_agent.data.schema import build_schema
from powerbi_agent.modeling.star_schema import design_star_schema
from powerbi_agent.models.datasource import DataSource
from powerbi_agent.models.model_design import ModelDesign
from powerbi_agent.models.semantic_model import SummarizeBy, TableKind


def _design(frame: pl.DataFrame, **kw: object) -> tuple[ModelDesign, dict[str, pl.DataFrame]]:
    ds = LoadedDataset(source=DataSource.from_path(Path("sales.csv")), frame=frame)
    profile = profile_dataset(ds)
    return design_star_schema(frame, profile, build_schema(profile), **kw)  # type: ignore[arg-type]


@pytest.fixture
def sales() -> pl.DataFrame:
    rows = []
    for i in range(90):
        cust, prod = (i // 3) % 6, i % 4
        rows.append(
            {
                "OrderID": f"SO{i // 3:03d}",
                "OrderLine": i % 3 + 1,
                "OrderDate": date(2025, 1 + (i // 3) % 12, 1 + i % 28),
                "ShipDate": date(2025, 1 + (i // 3) % 12, 2 + i % 27),
                "CustomerID": None if i == 0 else f"C{cust}",
                "CustomerName": f"Customer {cust}",
                "Region": ["North", "South", "East"][cust % 3],
                "ProductID": f"P{prod}",
                "Category": ["Bikes", "Clothing"][prod % 2],
                "Revenue": float(i),
                "UnitPrice": 10.0 + prod,
            }
        )
    return pl.DataFrame(rows)


def test_star_schema_shape(sales: pl.DataFrame) -> None:
    design, tables = _design(sales)
    spec = design.spec
    assert [(t.name, t.kind) for t in spec.tables] == [
        ("FactSales", TableKind.FACT),
        ("DimCustomer", TableKind.DIMENSION),
        ("DimProduct", TableKind.DIMENSION),
        ("DimDate", TableKind.DATE),
    ]
    assert design.grain == ["OrderID", "OrderLine"] and design.grain_unique
    assert tables["FactSales"].height == 90
    assert tables["FactSales"].columns == [
        "OrderID",
        "OrderLine",
        "OrderDate",
        "ShipDate",
        "CustomerID",
        "ProductID",
        "Revenue",
        "UnitPrice",
    ]
    assert tables["DimCustomer"].columns == ["CustomerID", "CustomerName", "Region"]
    assert design.table_rows == {name: t.height for name, t in tables.items()}
    for table in spec.tables:
        assert table.source == f"model_data/{table.name}.parquet"


def test_relationships_and_role_playing_dates(sales: pl.DataFrame) -> None:
    spec = _design(sales)[0].spec
    rels = {(r.from_column, r.to_table, r.is_active) for r in spec.relationships}
    assert rels == {
        ("CustomerID", "DimCustomer", True),
        ("ProductID", "DimProduct", True),
        ("OrderDate", "DimDate", True),
        ("ShipDate", "DimDate", False),
    }


def test_fact_columns_metadata(sales: pl.DataFrame) -> None:
    fact = _design(sales)[0].spec.table("FactSales")
    assert fact is not None
    cols = {c.name: c for c in fact.columns}
    assert cols["CustomerID"].is_hidden and cols["OrderDate"].is_hidden
    assert not cols["OrderID"].is_hidden
    assert cols["Revenue"].summarize_by is SummarizeBy.SUM
    assert cols["UnitPrice"].summarize_by is SummarizeBy.NONE  # non-additive


def test_date_table_metadata(sales: pl.DataFrame) -> None:
    dim = _design(sales)[0].spec.table("DimDate")
    assert dim is not None
    cols = {c.name: c for c in dim.columns}
    assert cols["Date"].is_key
    assert cols["Month"].sort_by_column == "MonthNumber"
    assert cols["MonthNumber"].is_hidden
    assert dim.hierarchies[0].levels == ["Year", "Quarter", "Month", "Date"]


def test_null_foreign_key_is_a_finding(sales: pl.DataFrame) -> None:
    findings = {f.topic: f for f in _design(sales)[0].findings}
    null_fk = findings["null_foreign_keys"]
    assert (null_fk.column, null_fk.affected_rows) == ("CustomerID", 1)
    assert "cannot be linked" in null_fk.message


def test_datetime_with_time_gets_date_column() -> None:
    frame = pl.DataFrame(
        {
            "Ts": [datetime(2025, 1, 1, 10, 30), datetime(2025, 3, 2, 8, 0)] * 30,
            "Region": ["N", "S"] * 30,
            "Amount": [1.0] * 60,
        }
    )
    design, tables = _design(frame)
    assert "TsDate" in tables["FactSales"].columns
    rel = next(r for r in design.spec.relationships if r.to_table == "DimDate")
    assert rel.from_column == "TsDate"


def test_no_dates_no_date_table() -> None:
    frame = pl.DataFrame({"Region": ["N", "S"] * 10, "Amount": [1.0] * 20})
    design, tables = _design(frame)
    assert "DimDate" not in tables
    assert any("No date column" in d for d in design.decisions)
    assert any(f.topic == "grain" for f in design.findings)


def test_diff_against_previous(sales: pl.DataFrame) -> None:
    first = _design(sales)[0]
    assert first.diff is not None and "DimCustomer" in first.diff.tables_added
    again = _design(sales, previous=first.spec)[0]
    assert again.diff is not None and again.diff.is_empty


def test_design_is_deterministic(sales: pl.DataFrame) -> None:
    a, ta = _design(sales)
    b, tb = _design(sales)
    assert a.model_dump_json() == b.model_dump_json()
    assert all(ta[n].equals(tb[n]) for n in ta)


def test_a_unique_date_alone_is_not_a_grain() -> None:
    frame = pl.DataFrame(
        {
            "Day": [date(2025, 1, d) for d in range(1, 29)],
            "StoreID": ["S1", "S2"] * 14,
            "Amount": [1.0] * 28,
        }
    )
    design = _design(frame)[0]
    assert design.grain == ["Day", "StoreID"]


def test_near_dependency_is_reported(sales: pl.DataFrame) -> None:
    frame = sales.with_columns(
        pl.when(pl.int_range(pl.len()) < 3)
        .then(pl.lit("Helmets"))
        .otherwise(pl.col("Category"))
        .alias("Category")
    )
    design, tables = _design(frame)
    near = next(f for f in design.findings if f.topic == "near_dependency")
    assert (near.column, near.affected_rows) == ("Category", 3)
    assert "Category" in tables["FactSales"].columns
