"""Fact table identification: naming and grain detection."""

from __future__ import annotations

import re
from itertools import combinations

import polars as pl

from powerbi_agent.models.analysis import ColumnRole, DatasetSchema, LogicalType

MAX_GRAIN_CANDIDATES = 10
MAX_GRAIN_COLUMNS = 3

_ID_SUFFIX_SNAKE = re.compile(r"(?i)[_\s\-]*(id|key|code|no|num|number)$")
_ID_SUFFIX_CAMEL = re.compile(r"(?<=[a-z0-9])(ID|Id|Key|Code|No|Num|Number)$")
_SEQUENCE = re.compile(r"(?i)(line|seq|sequence|position|item_?no|row_?no)")


def pascal(text: str) -> str:
    """``"sales data_2025"`` -> ``"SalesData2025"``; always a valid identifier."""
    words = re.findall(r"[A-Za-z0-9]+", text)
    name = "".join(w[0].upper() + w[1:] for w in words)
    if not name:
        return "Table"
    return f"T{name}" if name[0].isdigit() else name


def entity_name(key_column: str) -> str:
    """``CustomerID`` / ``customer_id`` / ``Product Key`` -> ``Customer`` / ``Product``."""
    stem = _ID_SUFFIX_CAMEL.sub("", key_column.strip())
    if stem == key_column.strip():
        stem = _ID_SUFFIX_SNAKE.sub("", stem)
    return pascal(stem or key_column)


def fact_table_name(dataset: str) -> str:
    return f"Fact{pascal(dataset)}"


def grain_candidates(schema: DatasetSchema) -> list[str]:
    """Columns that can identify a row, most likely grain markers first.

    Order: unique keys, line/sequence numbers (explicit grain markers), foreign keys,
    other integer labels, dates. Combinations are tried in this order, so
    ``OrderID + OrderLine`` wins over an accidental ``OrderID + ProductID``.
    """
    keys = [c.name for c in schema.by_role(ColumnRole.KEY)]
    foreign = [c.name for c in schema.by_role(ColumnRole.FOREIGN_KEY)]
    labels = [
        c.name
        for c in schema.by_role(ColumnRole.DIMENSION_ATTRIBUTE)
        if c.logical_type is LogicalType.INTEGER
    ]
    sequence = [n for n in labels if _SEQUENCE.search(n)]
    other = [n for n in labels if n not in sequence]
    dates = [c.name for c in schema.by_role(ColumnRole.DATE)]
    return (keys + sequence + foreign + other + dates)[:MAX_GRAIN_CANDIDATES]


def detect_grain(frame: pl.DataFrame, schema: DatasetSchema) -> tuple[list[str], bool]:
    """Smallest column set that uniquely identifies a row.

    Exact duplicate rows can never be told apart, so uniqueness is tested on the
    de-duplicated frame; the flag says whether the grain also holds on the raw data.
    Returns ``([], False)`` when no combination of candidate columns is unique.
    """
    deduped = frame.unique()
    candidates = grain_candidates(schema)
    keys = {c.name for c in schema.by_role(ColumnRole.KEY, ColumnRole.FOREIGN_KEY)}
    for size in range(1, MAX_GRAIN_COLUMNS + 1):
        for combo in combinations(candidates, size):
            cols = list(combo)
            if not keys.intersection(cols):
                continue  # a grain needs at least one key (dates/labels alone are coincidence)
            subset = deduped.select(cols)
            if subset.null_count().sum_horizontal().item() > 0:
                continue
            if subset.n_unique() == deduped.height:
                ordered = [c for c in frame.columns if c in cols]  # source order
                return ordered, frame.select(cols).n_unique() == frame.height
    return [], False
