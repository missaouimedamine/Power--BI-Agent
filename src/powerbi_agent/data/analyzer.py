"""KPI/dimension candidates, metrics and insights.

Everything here is computed from the data. Insights are fixed templates filled with
computed numbers, and each one carries its evidence, so an LLM can phrase them but
never invent them.
"""

from __future__ import annotations

import re
from typing import Any

import polars as pl

from powerbi_agent.data.loaders import LoadedDataset
from powerbi_agent.data.profiler import json_value, profile_dataset
from powerbi_agent.data.quality import check_quality
from powerbi_agent.data.schema import build_schema
from powerbi_agent.models.analysis import (
    AnalysisResult,
    ColumnRole,
    DatasetMetrics,
    DatasetProfile,
    DatasetSchema,
    Insight,
    LogicalType,
    QualityReport,
    Severity,
    SuggestedKpi,
)
from powerbi_agent.utils.security import quote_data_value

BREAKDOWN_MAX_DISTINCT = 50
MAX_BREAKDOWNS = 4
CORRELATION_MIN_ROWS = 30
CORRELATION_THRESHOLD = 0.7
NEAR_PERFECT_CORRELATION = 0.98
MIN_MONTHS_FOR_TREND = 3

# Business meaning from column names. Order matters: earlier patterns are preferred.
_REVENUE = [r"^revenue$|net_?revenue", r"revenue", r"^sales$|sales_?amount|net_?sales", r"amount"]
_COST = [r"cost|cogs|expense"]
_PROFIT = [r"profit"]
_QUANTITY = [r"qty|quantity|units"]
_ORDER = [r"order|invoice|transaction|receipt"]


def _key(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip().lower())


def find_column(candidates: list[str], patterns: list[str], exclude: set[str]) -> str | None:
    for pattern in patterns:
        for name in candidates:
            if name not in exclude and re.search(pattern, _key(name)):
                return name
    return None


class BusinessColumns:
    """Columns recognised by name, used for KPI suggestions and insights."""

    def __init__(self, schema: DatasetSchema) -> None:
        measure_cols = schema.by_role(ColumnRole.MEASURE)
        measures = [c.name for c in measure_cols]
        self.additive = [c.name for c in measure_cols if c.additive is not False]
        self.non_additive = [c.name for c in measure_cols if c.additive is False]
        keys = [c.name for c in schema.by_role(ColumnRole.KEY, ColumnRole.FOREIGN_KEY)]
        self.measures = measures
        self.cost = find_column(measures, _COST, set())
        self.profit = find_column(measures, _PROFIT, set())
        exclude = {c for c in (self.cost, self.profit) if c}
        self.quantity = find_column(self.additive, _QUANTITY, exclude)
        exclude |= {self.quantity} if self.quantity else set()
        self.revenue = find_column(self.additive, _REVENUE, exclude)
        self.order = find_column(keys, _ORDER, set())
        dates = schema.by_role(ColumnRole.DATE)
        date_names = [c.name for c in dates]
        self.date = find_column(date_names, [r"order", r"date"], set()) or (
            date_names[0] if date_names else None
        )

    @property
    def primary_measure(self) -> str | None:
        return self.revenue or (self.additive[0] if self.additive else None)


def _sum(frame: pl.DataFrame, column: str) -> float:
    return float(frame.get_column(column).cast(pl.Float64).sum())


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def suggest_kpis(frame: pl.DataFrame, cols: BusinessColumns) -> list[SuggestedKpi]:
    kpis = [
        SuggestedKpi(
            name=f"Total {name}",
            definition=f"Sum of {name}",
            based_on=[name],
            aggregation="sum",
            value=_sum(frame, name),
        )
        for name in cols.additive
    ]
    kpis += [
        SuggestedKpi(
            name=f"Average {name}",
            definition=f"Average of {name} (non-additive, so not summed)",
            based_on=[name],
            aggregation="average",
            value=_float_or_none(frame.get_column(name).cast(pl.Float64).mean()),
        )
        for name in cols.non_additive
    ]
    revenue = _sum(frame, cols.revenue) if cols.revenue else None
    profit: float | None = None
    if cols.profit:
        profit = _sum(frame, cols.profit)
    elif cols.revenue and cols.cost:
        profit = revenue - _sum(frame, cols.cost)  # type: ignore[operator]
        kpis.append(
            SuggestedKpi(
                name="Total Profit",
                definition=f"{cols.revenue} minus {cols.cost}",
                based_on=[cols.revenue, cols.cost],
                aggregation="difference",
                value=profit,
            )
        )
    if cols.revenue and profit is not None:
        based_on = [cols.revenue, cols.profit or cols.cost or ""]
        kpis.append(
            SuggestedKpi(
                name="Profit Margin",
                definition="Profit divided by revenue",
                based_on=[b for b in based_on if b],
                aggregation="ratio",
                format_hint="percent",
                value=_ratio(profit, revenue or 0.0),
            )
        )
    if cols.order:
        orders = frame.get_column(cols.order).drop_nulls().n_unique()
        kpis.append(
            SuggestedKpi(
                name="Orders",
                definition=f"Distinct count of {cols.order}",
                based_on=[cols.order],
                aggregation="distinct_count",
                format_hint="integer",
                value=float(orders),
            )
        )
        if cols.revenue:
            kpis.append(
                SuggestedKpi(
                    name="Average Order Value",
                    definition=f"{cols.revenue} divided by distinct {cols.order}",
                    based_on=[cols.revenue, cols.order],
                    aggregation="ratio",
                    value=_ratio(revenue or 0.0, float(orders)),
                )
            )
    return kpis


def compute_metrics(
    frame: pl.DataFrame, profile: DatasetProfile, schema: DatasetSchema, cols: BusinessColumns
) -> DatasetMetrics:
    metrics = DatasetMetrics(dataset=profile.source.name, rows=profile.rows)
    for name in cols.measures:
        prof = profile.column(name)
        assert prof is not None
        metrics.measures[name] = {
            "sum": _sum(frame, name),
            "mean": prof.mean,
            "median": prof.median,
            "min": float(prof.min) if prof.min is not None else None,
            "max": float(prof.max) if prof.max is not None else None,
        }
    for col in schema.by_role(ColumnRole.DATE):
        prof = profile.column(col.name)
        if prof is not None and prof.min is not None and prof.max is not None:
            metrics.date_ranges[col.name] = (str(prof.min), str(prof.max))
    metrics.suggested_kpis = suggest_kpis(frame, cols)
    return metrics


def monthly_totals(frame: pl.DataFrame, date_col: str, measure: str | None) -> pl.DataFrame:
    """Columns ``month`` (date) and ``value``: sum of measure, or row count."""
    value = pl.col(measure).cast(pl.Float64).sum() if measure else pl.len().cast(pl.Float64)
    return (
        frame.filter(pl.col(date_col).is_not_null())
        .group_by(pl.col(date_col).dt.truncate("1mo").cast(pl.Date).alias("month"))
        .agg(value.alias("value"))
        .sort("month")
    )


def breakdown(frame: pl.DataFrame, dimension: str, measure: str | None) -> pl.DataFrame:
    """Columns ``category``, ``value``, ``share`` sorted by value descending."""
    value = pl.col(measure).cast(pl.Float64).sum() if measure else pl.len().cast(pl.Float64)
    out = (
        frame.group_by(pl.col(dimension).alias("category"))
        .agg(value.alias("value"))
        # Tie-break on the category so results never depend on hash ordering.
        .sort(["value", "category"], descending=[True, False], nulls_last=True)
    )
    total = out.get_column("value").sum()
    return out.with_columns((pl.col("value") / total if total else pl.lit(None)).alias("share"))


def breakdown_dimensions(profile: DatasetProfile, schema: DatasetSchema) -> list[str]:
    """Low-cardinality text dimensions, fewest categories first."""
    dims: list[tuple[int, str]] = []
    for col in schema.by_role(ColumnRole.DIMENSION_ATTRIBUTE):
        # Numeric labels (line numbers, years) make poor category breakdowns.
        if col.logical_type not in {LogicalType.STRING, LogicalType.BOOLEAN}:
            continue
        prof = profile.column(col.name)
        if prof is not None and 2 <= prof.distinct_count <= BREAKDOWN_MAX_DISTINCT:
            dims.append((prof.distinct_count, col.name))
    return [name for _, name in sorted(dims)[:MAX_BREAKDOWNS]]


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2f}"


def generate_insights(
    frame: pl.DataFrame,
    profile: DatasetProfile,
    schema: DatasetSchema,
    quality: QualityReport,
    cols: BusinessColumns,
) -> list[Insight]:
    insights: list[Insight] = []
    measure = cols.primary_measure
    what = measure or "row count"  # used in titles (escaped when rendered)
    # Column names and values come from the data: quote them in every detail text.
    q = quote_data_value
    what_q = q(measure) if measure else "row count"

    overview = f"{profile.rows:,} rows and {profile.columns} columns."
    if cols.date:
        prof = profile.column(cols.date)
        if prof is not None and prof.min is not None:
            overview += f" {q(cols.date)} spans {prof.min} to {prof.max}."
    insights.append(
        Insight(
            title="Dataset overview",
            detail=overview,
            evidence={"rows": profile.rows, "columns": profile.columns},
        )
    )

    if cols.date:
        monthly = monthly_totals(frame, cols.date, measure)
        if monthly.height >= MIN_MONTHS_FOR_TREND:
            peak = monthly.sort(["value", "month"], descending=[True, False]).row(0, named=True)
            low = monthly.sort(["value", "month"]).row(0, named=True)
            detail = (
                f"Monthly {what_q} peaked in {peak['month']:%Y-%m} ({_fmt(peak['value'])}) and was "
                f"lowest in {low['month']:%Y-%m} ({_fmt(low['value'])})."
            )
            evidence: dict[str, Any] = {
                "monthly": {f"{m:%Y-%m}": v for m, v in monthly.iter_rows()},
                "note": "First and last months may be partial.",
            }
            if monthly.height >= 6:
                values = monthly.get_column("value")
                recent = float(values.tail(3).sum())
                prior = float(values.tail(6).head(3).sum())
                if prior:
                    change = recent / prior - 1
                    detail += f" The last 3 months are {change:+.1%} vs the 3 months before."
                    evidence["last3_vs_prior3"] = change
            insights.append(Insight(title=f"{what} over time", detail=detail, evidence=evidence))

    for dim in breakdown_dimensions(profile, schema):
        table = breakdown(frame, dim, measure)
        top = table.row(0, named=True)
        if top["share"] is None:
            continue
        detail = (
            f"Top {q(dim)} is {q(json_value(top['category']))} with {top['share']:.1%} of {what_q}"
        )
        if table.height > 3:
            top3 = float(table.head(3).get_column("share").sum())
            detail += f"; the top 3 of {table.height} account for {top3:.1%}"
        insights.append(
            Insight(
                title=f"{what} by {dim}",
                detail=detail + ".",
                evidence={
                    "breakdown": [
                        {"category": json_value(c), "value": v, "share": s}
                        for c, v, s in table.head(10).iter_rows()
                    ]
                },
            )
        )

    if profile.rows >= CORRELATION_MIN_ROWS and len(cols.additive) > 1:
        numeric = frame.select([pl.col(m).cast(pl.Float64) for m in cols.additive]).drop_nulls()
        found = 0
        for i, a in enumerate(cols.additive):
            for b in cols.additive[i + 1 :]:
                r = numeric.select(pl.corr(a, b)).item()
                if r is not None and abs(r) >= CORRELATION_THRESHOLD and found < 3:
                    found += 1
                    insights.append(
                        Insight(
                            title=f"{a} and {b} move together",
                            detail=(
                                f"Pearson correlation between {q(a)} and {q(b)} is {r:.2f} "
                                f"(n={numeric.height:,}). "
                                + (
                                    "Near-perfect: one is probably derived from the other."
                                    if abs(r) >= NEAR_PERFECT_CORRELATION
                                    else "Correlation is not causation."
                                )
                            ),
                            evidence={"r": r, "n": numeric.height},
                        )
                    )

    errors, warnings = quality.count(Severity.ERROR), quality.count(Severity.WARNING)
    insights.append(
        Insight(
            title="Data quality",
            detail=(
                f"{errors} error(s), {warnings} warning(s), {quality.count(Severity.INFO)} "
                "informational finding(s). See quality_report.json."
            ),
            evidence={"errors": errors, "warnings": warnings},
        )
    )
    return insights


def analyze_dataset(dataset: LoadedDataset) -> AnalysisResult:
    """Run the full deterministic analysis on one dataset (no files written)."""
    frame = dataset.frame
    profile = profile_dataset(dataset)
    schema = build_schema(profile)
    quality = check_quality(frame, profile, schema)
    cols = BusinessColumns(schema)
    return AnalysisResult(
        profile=profile,
        dataset_schema=schema,
        quality=quality,
        metrics=compute_metrics(frame, profile, schema, cols),
        candidate_dimensions=[
            c.name
            for c in schema.by_role(
                ColumnRole.DIMENSION_ATTRIBUTE, ColumnRole.DATE, ColumnRole.FOREIGN_KEY
            )
        ],
        candidate_measures=cols.measures,
        insights=generate_insights(frame, profile, schema, quality, cols),
        notes=list(dataset.notes),
    )
