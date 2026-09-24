"""Dimension discovery from a flat table, via functional dependencies.

An attribute ``A`` belongs to key ``K`` when (almost) every value of ``K`` maps to a
single value of ``A``. Agreement is measured per row against the most frequent value
of ``A`` for each ``K``, so a handful of inconsistent rows (e.g. spelling variants)
don't hide a real dependency. Each conflict is reported, never silently absorbed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import polars as pl

from powerbi_agent.models.analysis import ColumnRole, DatasetSchema

# Share of rows that must agree with their key's most frequent value.
FD_MIN_AGREEMENT = 0.99
# Below FD_MIN_AGREEMENT but at least this: report as a near miss worth fixing.
NEAR_MISS_AGREEMENT = 0.9
# Keys this close to one value per row identify rows, not entities: no dimension.
ROW_IDENTIFIER_RATIO = 0.9


@dataclass
class Dependency:
    key: str
    attribute: str
    agreement: float  # share of non-null rows matching the key's modal value
    conflicting_rows: int


@dataclass
class DimensionPlan:
    key: str
    attributes: list[str] = field(default_factory=list)
    conflicts: dict[str, int] = field(default_factory=dict)  # attribute -> rows


def _modal_rows(pairs: pl.DataFrame, key: str) -> int:
    """Rows that carry the most frequent value for their key (``pairs`` has a ``len``)."""
    return int(pairs.group_by(key).agg(pl.col("len").max()).get_column("len").sum())


def dependency(frame: pl.DataFrame, key: str, attribute: str) -> Dependency:
    """How strongly ``key`` determines ``attribute``.

    Agreement ignores case/whitespace differences in text ("Bikes" vs "bikes "): those
    are one value spelled inconsistently, which the quality report already flags.
    Conflicting rows are counted on the raw values, so every row whose reported value
    would change in the dimension is reported.
    """
    data = frame.select(key, attribute).drop_nulls()
    if data.is_empty():
        return Dependency(key, attribute, 0.0, 0)
    raw = data.group_by(key, attribute).len()
    total = data.height
    compare = data
    if data.schema[attribute] == pl.String:
        normalised = pl.col(attribute).str.strip_chars().str.to_lowercase()
        compare = data.with_columns(normalised)
    agreement = _modal_rows(compare.group_by(key, attribute).len(), key) / total
    return Dependency(key, attribute, agreement, total - _modal_rows(raw, key))


def entity_keys(frame: pl.DataFrame, schema: DatasetSchema) -> list[str]:
    """Id-like columns that represent entities (not per-row identifiers)."""
    keys = []
    for col in schema.by_role(ColumnRole.KEY, ColumnRole.FOREIGN_KEY):
        distinct = frame.get_column(col.name).drop_nulls().n_unique()
        if distinct and distinct < ROW_IDENTIFIER_RATIO * frame.height:
            keys.append(col.name)
    return keys


def attribute_candidates(schema: DatasetSchema) -> list[str]:
    return [c.name for c in schema.by_role(ColumnRole.DIMENSION_ATTRIBUTE, ColumnRole.IDENTIFIER)]


def plan_dimensions(frame: pl.DataFrame, schema: DatasetSchema) -> list[DimensionPlan]:
    """Assign each attribute to the most general key that determines it."""
    keys = entity_keys(frame, schema)
    cardinality = {k: frame.get_column(k).drop_nulls().n_unique() for k in keys}
    plans = {k: DimensionPlan(key=k) for k in keys}
    for attribute in attribute_candidates(schema):
        determining = [
            dep
            for k in keys
            if (dep := dependency(frame, k, attribute)).agreement >= FD_MIN_AGREEMENT
        ]
        if not determining:
            continue  # stays in the fact as a degenerate attribute
        # Fewest distinct values = most general entity (Customer before Order).
        best = min(determining, key=lambda d: (cardinality[d.key], d.key))
        plans[best.key].attributes.append(attribute)
        if best.conflicting_rows:
            plans[best.key].conflicts[attribute] = best.conflicting_rows
    return [p for p in plans.values() if p.attributes]


def near_misses(frame: pl.DataFrame, schema: DatasetSchema, assigned: set[str]) -> list[Dependency]:
    """Unassigned attributes that *almost* depend on a key (data conflicts in the way)."""
    keys = entity_keys(frame, schema)
    misses = []
    for attribute in attribute_candidates(schema):
        if attribute in assigned:
            continue
        deps = [dependency(frame, k, attribute) for k in keys]
        best = max(deps, key=lambda d: (d.agreement, d.key), default=None)
        if best is not None and NEAR_MISS_AGREEMENT <= best.agreement < FD_MIN_AGREEMENT:
            misses.append(best)
    return misses


def build_dimension(frame: pl.DataFrame, plan: DimensionPlan) -> pl.DataFrame:
    """One row per key; each attribute takes its most frequent value (ties: smallest)."""
    keyed = frame.filter(pl.col(plan.key).is_not_null())
    dim = keyed.select(plan.key).unique().sort(plan.key)
    for attribute in plan.attributes:
        modal = (
            keyed.select(plan.key, attribute)
            .drop_nulls()
            .group_by(plan.key, attribute)
            .len()
            .sort([plan.key, "len", attribute], descending=[False, True, False])
            .group_by(plan.key, maintain_order=True)
            .first()
            .select(plan.key, attribute)
        )
        dim = dim.join(modal, on=plan.key, how="left")
    return dim


def determines(frame: pl.DataFrame, child: str, parent: str) -> bool:
    """Every ``child`` value maps to exactly one ``parent`` value (strict)."""
    pairs = frame.select(child, parent).drop_nulls().unique()
    return pairs.height == pairs.get_column(child).n_unique()


def detect_hierarchies(dim: pl.DataFrame, attributes: list[str]) -> list[list[str]]:
    """Chains of attributes where each level determines the one above it."""
    counts = {a: dim.get_column(a).drop_nulls().n_unique() for a in attributes}
    remaining = sorted((a for a in attributes if counts[a] >= 2), key=lambda a: (counts[a], a))
    chains = []
    while remaining:
        chain = [remaining.pop(0)]
        for candidate in list(remaining):
            if counts[candidate] > counts[chain[-1]] and determines(dim, candidate, chain[-1]):
                chain.append(candidate)
                remaining.remove(candidate)
        if len(chain) >= 2:
            chains.append(chain)
    return chains


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def build_date_dimension(start: date, end: date) -> pl.DataFrame:
    """Contiguous calendar covering whole years from ``start`` to ``end``."""
    dates = pl.date_range(date(start.year, 1, 1), date(end.year, 12, 31), "1d", eager=True)
    return pl.DataFrame({"Date": dates}).with_columns(
        pl.col("Date").dt.year().cast(pl.Int64).alias("Year"),
        ("Q" + pl.col("Date").dt.quarter().cast(pl.String)).alias("Quarter"),
        pl.col("Date").dt.month().cast(pl.Int64).alias("MonthNumber"),
        pl.col("Date")
        .dt.month()
        .replace_strict(list(range(1, 13)), MONTHS, return_dtype=pl.String)
        .alias("Month"),
        pl.col("Date").dt.strftime("%Y-%m").alias("YearMonth"),
        pl.col("Date").dt.day().cast(pl.Int64).alias("Day"),
        pl.col("Date").dt.weekday().cast(pl.Int64).alias("WeekdayNumber"),
        pl.col("Date")
        .dt.weekday()
        .replace_strict(list(range(1, 8)), WEEKDAYS, return_dtype=pl.String)
        .alias("Weekday"),
    )
