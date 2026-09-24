"""Data-quality checks -> :class:`QualityReport` (``analysis/quality_report.json``).

Every check is deterministic and reports issues without changing the data. Checks that
need more than one table (orphan keys, duplicate dimension keys) run in Phase 3 once
tables are split into facts and dimensions.
"""

from __future__ import annotations

from collections.abc import Callable

import polars as pl

from powerbi_agent.data.profiler import json_value
from powerbi_agent.models.analysis import (
    ColumnRole,
    DatasetProfile,
    DatasetSchema,
    LogicalType,
    QualityIssue,
    QualityIssueKind,
    QualityReport,
    Severity,
)

MAX_EXAMPLES = 5
OUTLIER_IQR_FACTOR = 3.0
OUTLIER_MIN_ROWS = 20
TYPE_MISMATCH_RATIO = 0.95
CATEGORY_MAX_DISTINCT = 1_000

Check = Callable[[pl.DataFrame, DatasetProfile, DatasetSchema], list[QualityIssue]]


def _pct(part: int, whole: int) -> str:
    return f"{part / whole:.1%}" if whole else "0%"


def check_missing_values(
    frame: pl.DataFrame, profile: DatasetProfile, schema: DatasetSchema
) -> list[QualityIssue]:
    issues = []
    roles = {c.name: c.role for c in schema.columns}
    for col in profile.column_profiles:
        if not col.null_count:
            continue
        is_key = roles.get(col.name) in {ColumnRole.KEY, ColumnRole.FOREIGN_KEY}
        consequence = "; these rows cannot join to the related dimension" if is_key else ""
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.MISSING_VALUES,
                severity=Severity.ERROR if is_key else Severity.WARNING,
                column=col.name,
                affected_rows=col.null_count,
                description=(
                    f"{col.null_count:,} rows ({_pct(col.null_count, profile.rows)}) have no "
                    f"{col.name}{consequence}."
                ),
            )
        )
    return issues


def check_blank_strings(
    frame: pl.DataFrame, profile: DatasetProfile, schema: DatasetSchema
) -> list[QualityIssue]:
    issues = []
    for name in frame.columns:
        series = frame.get_column(name)
        if series.dtype != pl.String:
            continue
        blanks = int((series.str.strip_chars() == "").sum())
        if blanks:
            issues.append(
                QualityIssue(
                    kind=QualityIssueKind.BLANK_STRINGS,
                    severity=Severity.WARNING,
                    column=name,
                    affected_rows=blanks,
                    description=f"{blanks:,} values in {name} are blank (whitespace only).",
                )
            )
    return issues


def check_duplicate_rows(
    frame: pl.DataFrame, profile: DatasetProfile, schema: DatasetSchema
) -> list[QualityIssue]:
    if not profile.duplicate_rows:
        return []
    dupes = frame.filter(frame.is_duplicated()).unique(maintain_order=True).head(MAX_EXAMPLES)
    examples = [{k: json_value(v) for k, v in row.items()} for row in dupes.iter_rows(named=True)]
    return [
        QualityIssue(
            kind=QualityIssueKind.DUPLICATE_ROWS,
            severity=Severity.WARNING,
            affected_rows=profile.duplicate_rows,
            description=(
                f"{profile.duplicate_rows:,} rows are exact duplicates of an earlier row."
            ),
            examples=examples,
        )
    ]


def _normalise(expr: pl.Expr) -> pl.Expr:
    return expr.str.strip_chars().str.replace_all(r"\s+", " ").str.to_lowercase()


def check_inconsistent_categories(
    frame: pl.DataFrame, profile: DatasetProfile, schema: DatasetSchema
) -> list[QualityIssue]:
    """Values that differ only by case or whitespace ("Bikes", "bikes", " Bikes ")."""
    issues = []
    for col in schema.by_role(ColumnRole.DIMENSION_ATTRIBUTE):
        if col.logical_type is not LogicalType.STRING:
            continue
        prof = profile.column(col.name)
        if prof is None or prof.distinct_count > CATEGORY_MAX_DISTINCT:
            continue
        counts = (
            frame.select(pl.col(col.name).alias("raw"))
            .drop_nulls()
            .group_by("raw")
            .len()
            .with_columns(_normalise(pl.col("raw")).alias("norm"))
        )
        groups = (
            counts.sort(["len", "raw"], descending=[True, False])
            .group_by("norm", maintain_order=True)
            .agg(pl.col("raw"), pl.col("len"))
            .sort("norm")
        )
        groups = groups.filter(pl.col("raw").list.len() > 1)
        if groups.is_empty():
            continue
        affected = int(groups.select(pl.col("len").list.slice(1).list.sum()).to_series().sum())
        examples = [
            {"canonical": json_value(raw[0]), "variants": [json_value(v) for v in raw[1:]]}
            for raw in groups.get_column("raw").head(MAX_EXAMPLES).to_list()
        ]
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.INCONSISTENT_CATEGORIES,
                severity=Severity.WARNING,
                column=col.name,
                affected_rows=affected,
                description=(
                    f"{affected:,} rows in {col.name} use a spelling variant (case/whitespace) "
                    f"of another category; {groups.height} categories affected."
                ),
                examples=examples,
            )
        )
    return issues


def check_outliers(
    frame: pl.DataFrame, profile: DatasetProfile, schema: DatasetSchema
) -> list[QualityIssue]:
    issues = []
    for col in schema.by_role(ColumnRole.MEASURE):
        series = frame.get_column(col.name).drop_nulls().cast(pl.Float64)
        if series.len() < OUTLIER_MIN_ROWS:
            continue
        q1, q3 = series.quantile(0.25), series.quantile(0.75)
        if q1 is None or q3 is None or q3 == q1:
            continue
        iqr = q3 - q1
        low, high = q1 - OUTLIER_IQR_FACTOR * iqr, q3 + OUTLIER_IQR_FACTOR * iqr
        outliers = series.filter((series < low) | (series > high))
        if outliers.len():
            extremes = outliers.sort(descending=True).head(MAX_EXAMPLES).to_list()
            issues.append(
                QualityIssue(
                    kind=QualityIssueKind.OUTLIERS,
                    severity=Severity.INFO,
                    column=col.name,
                    affected_rows=outliers.len(),
                    description=(
                        f"{outliers.len():,} values in {col.name} fall outside "
                        f"[{low:,.2f}, {high:,.2f}] (Q1/Q3 +- {OUTLIER_IQR_FACTOR:g} x IQR). "
                        "Review them; they were not removed."
                    ),
                    examples=[json_value(v) for v in extremes],
                )
            )
    return issues


def check_negative_values(
    frame: pl.DataFrame, profile: DatasetProfile, schema: DatasetSchema
) -> list[QualityIssue]:
    issues = []
    for col in schema.by_role(ColumnRole.MEASURE):
        series = frame.get_column(col.name)
        negatives = int((series < 0).sum())
        if negatives:
            issues.append(
                QualityIssue(
                    kind=QualityIssueKind.NEGATIVE_VALUES,
                    severity=Severity.INFO,
                    column=col.name,
                    affected_rows=negatives,
                    description=(
                        f"{negatives:,} negative values in {col.name}: returns/credits, or "
                        "errors? Confirm the business meaning."
                    ),
                    examples=[json_value(v) for v in series.filter(series < 0).head(MAX_EXAMPLES)],
                )
            )
    return issues


def check_type_mismatch(
    frame: pl.DataFrame, profile: DatasetProfile, schema: DatasetSchema
) -> list[QualityIssue]:
    """Text columns that are mostly numbers or dates; the rest are likely invalid values."""
    issues = []
    for col in schema.columns:
        if col.logical_type is not LogicalType.STRING or col.role in {
            ColumnRole.KEY,
            ColumnRole.FOREIGN_KEY,
        }:
            continue
        values = frame.get_column(col.name).drop_nulls().str.strip_chars()
        values = values.filter(values != "")
        if values.is_empty():
            continue
        parsers: dict[str, Callable[[pl.Series], pl.Series]] = {
            "numbers": lambda s: s.str.replace_all(",", "").cast(pl.Float64, strict=False),
            # Format is inferred from the data; raises if no date format fits.
            "dates": lambda s: s.str.to_date(strict=False),
        }
        for target, parse in parsers.items():
            try:
                parsed = parse(values)
            except pl.exceptions.PolarsError:
                continue
            ok = parsed.is_not_null()
            ratio = ok.sum() / values.len()
            if ratio >= TYPE_MISMATCH_RATIO:
                bad = values.filter(~ok)
                issues.append(
                    QualityIssue(
                        kind=QualityIssueKind.TYPE_MISMATCH,
                        severity=Severity.WARNING,
                        column=col.name,
                        affected_rows=bad.len(),
                        description=(
                            f"{col.name} holds {target} stored as text ({ratio:.1%} parse); "
                            f"{bad.len():,} values do not parse and are probably invalid."
                        ),
                        examples=[
                            json_value(v)
                            for v in bad.unique(maintain_order=True).head(MAX_EXAMPLES)
                        ],
                    )
                )
                break
    return issues


def check_constant_columns(
    frame: pl.DataFrame, profile: DatasetProfile, schema: DatasetSchema
) -> list[QualityIssue]:
    return [
        QualityIssue(
            kind=QualityIssueKind.CONSTANT_COLUMN,
            severity=Severity.INFO,
            column=c.name,
            description=f"{c.name} has a single value ({c.top_values[0][0]!r}); it adds no detail.",
        )
        for c in profile.column_profiles
        if c.distinct_count == 1 and profile.rows > 1 and c.top_values
    ]


CHECKS: dict[str, Check] = {
    "missing_values": check_missing_values,
    "blank_strings": check_blank_strings,
    "duplicate_rows": check_duplicate_rows,
    "inconsistent_categories": check_inconsistent_categories,
    "outliers": check_outliers,
    "negative_values": check_negative_values,
    "type_mismatch": check_type_mismatch,
    "constant_columns": check_constant_columns,
}


def check_quality(
    frame: pl.DataFrame, profile: DatasetProfile, schema: DatasetSchema
) -> QualityReport:
    report = QualityReport(dataset=profile.source.name, rows_checked=profile.rows)
    for name, check in CHECKS.items():
        report.issues.extend(check(frame, profile, schema))
        report.checks_run.append(name)
    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    report.issues.sort(key=lambda i: (order[i.severity], -i.affected_rows))
    return report
