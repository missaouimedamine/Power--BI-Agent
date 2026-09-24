"""Facts, dimensions, relationships and diff building blocks."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from powerbi_agent.data.loaders import LoadedDataset
from powerbi_agent.data.profiler import profile_dataset
from powerbi_agent.data.schema import build_schema
from powerbi_agent.modeling.diff import diff_specs, summarize_diff
from powerbi_agent.modeling.dimensions import (
    build_date_dimension,
    build_dimension,
    dependency,
    detect_hierarchies,
    near_misses,
    plan_dimensions,
)
from powerbi_agent.modeling.facts import detect_grain, entity_name, fact_table_name, pascal
from powerbi_agent.modeling.relationships import check_key_pair, tmdl_type
from powerbi_agent.models.analysis import DatasetSchema
from powerbi_agent.models.datasource import DataSource
from powerbi_agent.models.semantic_model import DataType, SemanticModelSpec


def _schema(frame: pl.DataFrame) -> DatasetSchema:
    ds = LoadedDataset(source=DataSource.from_path(Path("t.csv")), frame=frame)
    return build_schema(profile_dataset(ds))


@pytest.fixture
def orders() -> pl.DataFrame:
    """60 order lines: 20 orders, 6 customers (3 regions), 4 products (2 categories)."""
    rows = []
    for i in range(60):
        order, cust, prod = i // 3, (i // 3) % 6, i % 4
        rows.append(
            {
                "OrderID": f"SO{order:03d}",
                "OrderLine": i % 3 + 1,
                "CustomerID": f"C{cust}",
                "CustomerName": f"Customer {cust}",
                "Region": ["North", "South", "East"][cust % 3],
                "ProductID": f"P{prod}",
                "Category": ["Bikes", "Clothing"][prod % 2],
                "Channel": ["Web", "Store"][(i // 5) % 2],  # depends on no key
                "Revenue": float(i),
            }
        )
    return pl.DataFrame(rows)


# --- naming ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sales", "Sales"),
        ("sales data_2025", "SalesData2025"),
        ("2025 sales", "T2025Sales"),
        ("", "Table"),
    ],
)
def test_pascal(text: str, expected: str) -> None:
    assert pascal(text) == expected


@pytest.mark.parametrize(
    ("key", "entity"),
    [
        ("CustomerID", "Customer"),
        ("customer_id", "Customer"),
        ("Product Key", "Product"),
        ("InvoiceNo", "Invoice"),
        ("id", "Id"),
    ],
)
def test_entity_name(key: str, entity: str) -> None:
    assert entity_name(key) == entity


def test_fact_table_name() -> None:
    assert fact_table_name("sales") == "FactSales"


# --- grain ----------------------------------------------------------------------------------
def test_grain_is_smallest_unique_combination(orders: pl.DataFrame) -> None:
    grain, unique = detect_grain(orders, _schema(orders))
    assert (grain, unique) == (["OrderID", "OrderLine"], True)


def test_grain_with_exact_duplicates(orders: pl.DataFrame) -> None:
    frame = pl.concat([orders, orders.head(2)])
    grain, unique = detect_grain(frame, _schema(frame))
    assert (grain, unique) == (["OrderID", "OrderLine"], False)


def test_no_grain() -> None:
    frame = pl.DataFrame({"Region": ["N", "N"], "Revenue": [1.0, 2.0]})
    assert detect_grain(frame, _schema(frame)) == ([], False)


# --- dependencies & dimensions --------------------------------------------------------------
def test_dependency_agreement_and_conflicts() -> None:
    frame = pl.DataFrame({"K": ["a"] * 99 + ["a"], "A": ["x"] * 99 + ["y"]})
    dep = dependency(frame, "K", "A")
    assert dep.agreement == pytest.approx(0.99)
    assert dep.conflicting_rows == 1


def test_attributes_go_to_the_most_general_key(orders: pl.DataFrame) -> None:
    plans = {p.key: p for p in plan_dimensions(orders, _schema(orders))}
    # Customer attributes also depend on OrderID, but CustomerID is more general.
    assert set(plans) == {"CustomerID", "ProductID"}
    assert plans["CustomerID"].attributes == ["CustomerName", "Region"]
    assert plans["ProductID"].attributes == ["Category"]


def test_row_identifiers_never_become_dimensions() -> None:
    frame = pl.DataFrame(
        {"TxnID": [f"T{i}" for i in range(50)], "Region": ["N", "S"] * 25, "Amt": [1.0] * 50}
    )
    assert plan_dimensions(frame, _schema(frame)) == []


def _with_first_category(frame: pl.DataFrame, value: str) -> pl.DataFrame:
    first = pl.int_range(pl.len()) == 0
    return frame.with_columns(
        pl.when(first).then(pl.lit(value)).otherwise(pl.col("Category")).alias("Category")
    )


def test_spelling_variants_keep_the_dependency_and_are_counted(orders: pl.DataFrame) -> None:
    frame = _with_first_category(orders, " bikes")  # P0 is "Bikes" everywhere else
    plans = {p.key: p for p in plan_dimensions(frame, _schema(frame))}
    assert plans["ProductID"].conflicts == {"Category": 1}
    dim = build_dimension(frame, plans["ProductID"])
    assert dim.filter(pl.col("ProductID") == "P0").item(0, "Category") == "Bikes"


def test_real_conflicts_above_threshold_break_the_dependency(orders: pl.DataFrame) -> None:
    frame = _with_first_category(orders, "Helmets")  # 1 of 60 rows: 98.3% < 99%
    plans = {p.key: p for p in plan_dimensions(frame, _schema(frame))}
    assert "ProductID" not in plans  # Category was its only attribute
    (miss,) = near_misses(frame, _schema(frame), set())
    assert (miss.key, miss.attribute, miss.conflicting_rows) == ("ProductID", "Category", 1)
    big = pl.concat([frame, orders, orders])  # 1 of 180 rows: 99.4%
    plans = {p.key: p for p in plan_dimensions(big, _schema(big))}
    assert plans["ProductID"].conflicts == {"Category": 1}


def test_build_dimension_one_row_per_key_sorted(orders: pl.DataFrame) -> None:
    plan = next(p for p in plan_dimensions(orders, _schema(orders)) if p.key == "CustomerID")
    dim = build_dimension(orders, plan)
    assert dim.columns == ["CustomerID", "CustomerName", "Region"]
    assert dim.get_column("CustomerID").to_list() == [f"C{i}" for i in range(6)]


def test_hierarchies_follow_dependency_chains() -> None:
    dim = pl.DataFrame(
        {
            "Category": ["Bikes", "Bikes", "Clothing", "Clothing"],
            "Subcategory": ["Road", "Mountain", "Jerseys", "Jerseys"],
            "Product": ["R1", "M1", "J1", "J2"],
            "Colour": ["Red", "Red", "Blue", "Red"],
        }
    )
    chains = detect_hierarchies(dim, ["Product", "Colour", "Subcategory", "Category"])
    assert ["Category", "Subcategory", "Product"] in chains
    assert all("Colour" not in c or c[0] == "Colour" for c in chains)


def test_date_dimension_whole_years() -> None:
    dim = build_date_dimension(date(2024, 3, 5), date(2025, 1, 2))
    assert dim.height == 366 + 365
    assert dim.item(0, "Date") == date(2024, 1, 1)
    first = dim.row(0, named=True)
    assert (first["Quarter"], first["Month"], first["Weekday"], first["WeekdayNumber"]) == (
        "Q1",
        "Jan",
        "Mon",
        1,
    )
    assert dim.item(-1, "YearMonth") == "2025-12"


# --- relationships --------------------------------------------------------------------------
def test_tmdl_type_mapping() -> None:
    assert tmdl_type(pl.Int32()) is DataType.INT64
    assert tmdl_type(pl.Float64()) is DataType.DOUBLE
    assert tmdl_type(pl.Date()) is DataType.DATETIME
    assert tmdl_type(pl.String()) is DataType.STRING


def test_check_key_pair() -> None:
    fact = pl.DataFrame({"k": ["a", "b", "z", None]})
    dim = pl.DataFrame({"k": ["a", "b", "b"]})
    check = check_key_pair(fact, "k", dim, "k")
    assert check.type_compatible
    assert not check.to_side_unique
    assert (check.orphan_rows, check.orphan_examples, check.null_from_rows) == (1, ["z"], 1)
    assert not check.ok
    incompatible = check_key_pair(pl.DataFrame({"k": [1]}), "k", dim, "k")
    assert not incompatible.type_compatible


# --- diff -----------------------------------------------------------------------------------
def _spec(tables: list[tuple[str, str, list[str]]]) -> SemanticModelSpec:
    return SemanticModelSpec.model_validate(
        {
            "model": {"name": "M"},
            "tables": [
                {
                    "name": n,
                    "kind": k,
                    "columns": [{"name": c, "data_type": "string"} for c in cols],
                }
                for n, k, cols in tables
            ],
        }
    )


def test_diff_and_summary() -> None:
    old = _spec([("FactSales", "fact", ["a", "b"]), ("DimOld", "dimension", ["x"])])
    new = _spec([("FactSales", "fact", ["a", "c"]), ("DimNew", "dimension", ["y"])])
    diff = diff_specs(old, new)
    assert diff.tables_added == ["DimNew"] and diff.tables_removed == ["DimOld"]
    assert diff.columns_added == ["FactSales[c]"]  # DimNew[y] is implied by the new table
    assert diff.columns_removed == ["FactSales[b]"]
    lines = summarize_diff(diff)
    assert "+ 1 table: DimNew" in lines
    assert "No objects removed." not in lines
    assert summarize_diff(diff_specs(new, new)) == ["No changes."]
