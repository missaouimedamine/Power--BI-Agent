"""Column profiling -> :class:`DatasetProfile` (``analysis/profile.json``)."""

from __future__ import annotations

import math
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import polars as pl

from powerbi_agent.data.loaders import LoadedDataset
from powerbi_agent.data.schema import logical_type, suggest_role
from powerbi_agent.models.analysis import ColumnProfile, DatasetProfile, LogicalType

TOP_VALUES = 5
TOP_VALUES_MAX_DISTINCT = 1_000
MAX_VALUE_CHARS = 100


def json_value(value: Any) -> Any:
    """Convert a Polars scalar into something JSON-friendly and bounded in size."""
    if value is None:
        return None
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[: MAX_VALUE_CHARS - 1] + "…"
    if isinstance(value, bool | int | str):
        return value
    return str(value)[:MAX_VALUE_CHARS]


def _float(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    return None if math.isnan(result) or math.isinf(result) else result


def profile_column(series: pl.Series) -> ColumnProfile:
    lt = logical_type(series.dtype)
    non_null = series.drop_nulls()
    col = ColumnProfile(
        name=series.name,
        physical_type=str(series.dtype),
        logical_type=lt,
        count=series.len(),
        null_count=series.null_count(),
        distinct_count=non_null.n_unique() if non_null.len() else 0,
    )
    if non_null.len():
        if lt.is_numeric:
            col.min = json_value(non_null.min())
            col.max = json_value(non_null.max())
            col.mean = _float(non_null.mean())
            col.std = _float(non_null.std()) if non_null.len() > 1 else None
            col.median = _float(non_null.median())
        elif lt.is_temporal:
            col.min = json_value(non_null.min())
            col.max = json_value(non_null.max())
        if (
            lt is LogicalType.STRING
            or lt is LogicalType.BOOLEAN
            or (col.distinct_count <= TOP_VALUES_MAX_DISTINCT and not lt.is_temporal)
        ):
            counts = (
                non_null.value_counts()
                .sort(["count", series.name], descending=[True, False])
                .head(TOP_VALUES)
            )
            col.top_values = [(json_value(v), int(n)) for v, n in counts.iter_rows()]
    col.suggested_role = suggest_role(col)[0]
    return col


def profile_dataset(dataset: LoadedDataset) -> DatasetProfile:
    frame = dataset.frame
    columns = [profile_column(frame.get_column(name)) for name in frame.columns]
    duplicates = frame.height - frame.n_unique() if frame.height and frame.width else 0
    return DatasetProfile(
        source=dataset.source,
        rows=frame.height,
        columns=frame.width,
        missing_values=sum(c.null_count for c in columns),
        duplicate_rows=duplicates,
        column_profiles=columns,
    )
