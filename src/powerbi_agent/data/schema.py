"""Schema inference: logical types and suggested analytical roles.

Roles come from deterministic, explainable rules. Each column records the rule that
fired (``reason``) so the LLM and the user can challenge it.
"""

from __future__ import annotations

import re

import polars as pl

from powerbi_agent.models.analysis import (
    ColumnProfile,
    ColumnRole,
    ColumnSchema,
    DatasetProfile,
    DatasetSchema,
    LogicalType,
)

# "customer_id", "order no", "product-key", "id"
_SNAKE_ID = re.compile(r"(?i)(^|[_\s\-])(id|key|code|no|num|number)$")
# "CustomerID", "OrderId", "ProductKey", "InvoiceNo" (case-sensitive so "paid" doesn't match)
_CAMEL_ID = re.compile(r"[a-z0-9](ID|Id|Key|Code|No|Num|Number)$")
# Integer columns that label things rather than measure them.
_NUMERIC_ATTRIBUTE = re.compile(
    r"(?i)(year|month|quarter|week|day|hour|zip|postal|postcode|phone|rank|level|rating|"
    r"line|seq|sequence|index|position|sort)"
)
# Measures where a sum is meaningless: average them instead.
_NON_ADDITIVE = re.compile(r"(?i)(price|rate|pct|percent|ratio|avg|average|margin|score|per_?unit)")

LOW_CARDINALITY_LIMIT = 50
DIMENSION_DISTINCT_RATIO = 0.5
UNIQUE_LABEL_MIN_ROWS = 20


def logical_type(dtype: pl.DataType) -> LogicalType:
    if dtype.is_integer():
        return LogicalType.INTEGER
    if dtype.is_float() or isinstance(dtype, pl.Decimal):
        return LogicalType.DECIMAL
    if dtype == pl.Boolean:
        return LogicalType.BOOLEAN
    if dtype == pl.Date:
        return LogicalType.DATE
    if isinstance(dtype, pl.Datetime):
        return LogicalType.DATETIME
    if dtype in (pl.String, pl.Categorical) or isinstance(dtype, pl.Enum):
        return LogicalType.STRING
    return LogicalType.UNKNOWN


def is_id_like(name: str) -> bool:
    name = name.strip()
    return bool(_SNAKE_ID.search(name) or _CAMEL_ID.search(name))


def suggest_role(col: ColumnProfile) -> tuple[ColumnRole, str]:
    non_null = col.count - col.null_count
    if non_null == 0:
        return ColumnRole.UNKNOWN, "column is entirely empty"
    lt = col.logical_type
    if lt.is_temporal:
        return ColumnRole.DATE, "date/datetime type"
    if is_id_like(col.name):
        if col.distinct_count == non_null:
            return ColumnRole.KEY, "id-like name, unique values"
        return ColumnRole.FOREIGN_KEY, "id-like name, repeated values"
    if lt is LogicalType.BOOLEAN:
        return ColumnRole.DIMENSION_ATTRIBUTE, "boolean flag"
    if lt.is_numeric:
        if lt is LogicalType.INTEGER and _NUMERIC_ATTRIBUTE.search(col.name):
            return ColumnRole.DIMENSION_ATTRIBUTE, "integer label (name suggests a code/period)"
        return ColumnRole.MEASURE, "numeric values"
    if lt is LogicalType.STRING:
        if col.distinct_count == non_null and non_null >= UNIQUE_LABEL_MIN_ROWS:
            return ColumnRole.IDENTIFIER, "text with a unique value per row"
        if (
            col.distinct_count <= LOW_CARDINALITY_LIMIT
            or col.distinct_ratio <= DIMENSION_DISTINCT_RATIO
        ):
            return ColumnRole.DIMENSION_ATTRIBUTE, "text with repeated values"
        return ColumnRole.IDENTIFIER, "high-cardinality text"
    return ColumnRole.UNKNOWN, f"unsupported type {col.physical_type}"


def build_schema(profile: DatasetProfile) -> DatasetSchema:
    columns = []
    for col in profile.column_profiles:
        role, reason = suggest_role(col)
        additive = None
        if role is ColumnRole.MEASURE:
            additive = not _NON_ADDITIVE.search(col.name)
            if not additive:
                reason += "; non-additive (price/rate): average instead of sum"
        columns.append(
            ColumnSchema(
                name=col.name,
                physical_type=col.physical_type,
                logical_type=col.logical_type,
                nullable=col.null_count > 0,
                role=role,
                reason=reason,
                additive=additive,
            )
        )
    return DatasetSchema(dataset=profile.source.name, columns=columns)
