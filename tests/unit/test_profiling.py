from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from powerbi_agent.data.loaders import LoadedDataset
from powerbi_agent.data.profiler import json_value, profile_dataset
from powerbi_agent.data.schema import build_schema, is_id_like
from powerbi_agent.models.analysis import ColumnRole, LogicalType
from powerbi_agent.models.datasource import DataSource


def _dataset(frame: pl.DataFrame) -> LoadedDataset:
    return LoadedDataset(source=DataSource.from_path(Path("t.csv")), frame=frame)


@pytest.fixture
def frame() -> pl.DataFrame:
    n = 60
    return pl.DataFrame(
        {
            "OrderID": [f"SO{i // 2}" for i in range(n)],  # repeats: foreign key
            "InvoiceNo": list(range(n)),  # unique: key
            "OrderDate": [date(2025, 1 + i % 12, 1) for i in range(n)],
            "Region": ["North", "South", "East"] * (n // 3),
            "Comment": [f"free text {i}" for i in range(n)],  # unique text: identifier
            "Year": [2024, 2025] * (n // 2),  # integer label
            "Revenue": [float(i) for i in range(n)],
            "UnitPrice": [9.99] * n,
            "IsOnline": [True, False] * (n // 2),
        }
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("CustomerID", True),
        ("customer_id", True),
        ("Order No", True),
        ("ProductKey", True),
        ("id", True),
        ("paid", False),
        ("Casino", False),
        ("Revenue", False),
    ],
)
def test_is_id_like(name: str, expected: bool) -> None:
    assert is_id_like(name) is expected


def test_roles(frame: pl.DataFrame) -> None:
    schema = build_schema(profile_dataset(_dataset(frame)))
    roles = {c.name: c.role for c in schema.columns}
    assert roles == {
        "OrderID": ColumnRole.FOREIGN_KEY,
        "InvoiceNo": ColumnRole.KEY,
        "OrderDate": ColumnRole.DATE,
        "Region": ColumnRole.DIMENSION_ATTRIBUTE,
        "Comment": ColumnRole.IDENTIFIER,
        "Year": ColumnRole.DIMENSION_ATTRIBUTE,
        "Revenue": ColumnRole.MEASURE,
        "UnitPrice": ColumnRole.MEASURE,
        "IsOnline": ColumnRole.DIMENSION_ATTRIBUTE,
    }
    additive = {c.name: c.additive for c in schema.columns if c.role is ColumnRole.MEASURE}
    assert additive == {"Revenue": True, "UnitPrice": False}
    assert all(c.reason for c in schema.columns)


def test_profile_counts(sales_csv: Path) -> None:
    from powerbi_agent.data.loaders import load_source

    profile = profile_dataset(load_source(DataSource.from_path(sales_csv)))
    assert (profile.rows, profile.columns) == (6, 10)
    assert profile.duplicate_rows == 1
    assert profile.missing_values == 1
    customer = profile.column("CustomerID")
    assert customer is not None
    assert customer.null_count == 1
    assert customer.null_ratio == pytest.approx(1 / 6)


def test_numeric_and_temporal_stats(frame: pl.DataFrame) -> None:
    profile = profile_dataset(_dataset(frame))
    revenue = profile.column("Revenue")
    assert revenue is not None and revenue.logical_type is LogicalType.DECIMAL
    assert (revenue.min, revenue.max, revenue.mean) == (0.0, 59.0, 29.5)
    dates = profile.column("OrderDate")
    assert dates is not None
    assert (dates.min, dates.max) == ("2025-01-01", "2025-12-01")
    region = profile.column("Region")
    assert region is not None
    assert region.top_values[0][1] == 20


def test_profile_is_json_serialisable(frame: pl.DataFrame) -> None:
    profile = profile_dataset(_dataset(frame))
    assert profile.model_validate_json(profile.model_dump_json()) == profile


def test_empty_column_is_unknown() -> None:
    frame = pl.DataFrame({"x": [None, None]}, schema={"x": pl.String})
    schema = build_schema(profile_dataset(_dataset(frame)))
    assert schema.columns[0].role is ColumnRole.UNKNOWN


def test_json_value_bounds_long_strings_and_nan() -> None:
    assert len(json_value("x" * 500)) == 100
    assert json_value(float("nan")) is None
    assert json_value(date(2025, 1, 2)) == "2025-01-02"
