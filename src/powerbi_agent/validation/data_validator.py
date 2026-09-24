"""Data validation.

- :func:`validate_analysis` checks the ``analysis/`` outputs of a workspace: files are
  present, parse against their models, agree with each other, and still match the
  source data (row count), so stale or hand-edited analyses are caught.
- :func:`check_referential_integrity` checks fact-to-dimension keys (used from Phase 3).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import polars as pl
from pydantic import BaseModel, ValidationError

from powerbi_agent.data.loaders import DataLoadError, load_source
from powerbi_agent.models.analysis import (
    DatasetMetrics,
    DatasetProfile,
    DatasetSchema,
    QualityReport,
    Severity,
)
from powerbi_agent.models.validation import (
    CheckStatus,
    ValidationFinding,
    ValidationReport,
)
from powerbi_agent.tools.duckdb import DuckDBSession, QueryError, quote_identifier


def _load_model[M: BaseModel](
    report: ValidationReport, path: Path, model: type[M], required: bool
) -> M | None:
    name = f"analysis/{path.name}"
    if not path.exists():
        if required:
            report.add("file_present", False, "required file is missing", object=name)
        else:
            report.findings.append(
                ValidationFinding(
                    check="file_present",
                    status=CheckStatus.NOT_RUN,
                    object=name,
                    message="not generated in this run (e.g. profile-only)",
                )
            )
        return None
    try:
        parsed = model.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        report.add("file_parses", False, f"invalid content: {exc.__class__.__name__}", object=name)
        return None
    report.add("file_parses", True, object=name)
    return parsed


def validate_analysis(workspace_dir: Path, *, recheck_source: bool = True) -> ValidationReport:
    analysis = workspace_dir / "analysis"
    report = ValidationReport(area="data", target=str(workspace_dir))
    profile = _load_model(report, analysis / "profile.json", DatasetProfile, True)
    schema = _load_model(report, analysis / "schema.json", DatasetSchema, True)
    quality = _load_model(report, analysis / "quality_report.json", QualityReport, False)
    metrics = _load_model(report, analysis / "metrics.json", DatasetMetrics, False)

    if (analysis / "insights.md").exists():
        report.add("file_present", True, object="analysis/insights.md")

    if profile is not None:
        report.add(
            "profile_column_count",
            profile.columns == len(profile.column_profiles),
            f"header says {profile.columns}, found {len(profile.column_profiles)} profiles",
            object="analysis/profile.json",
        )
        report.add(
            "profile_missing_total",
            profile.missing_values == sum(c.null_count for c in profile.column_profiles),
            "total missing cells equals the sum of per-column nulls",
            object="analysis/profile.json",
        )
        bad = [c.name for c in profile.column_profiles if c.null_count > c.count]
        report.add(
            "null_counts_within_rows",
            not bad,
            f"columns with more nulls than rows: {bad}" if bad else "",
            object="analysis/profile.json",
        )
        if schema is not None:
            report.add(
                "schema_matches_profile",
                [c.name for c in schema.columns] == [c.name for c in profile.column_profiles],
                "schema.json and profile.json describe the same columns in the same order",
                object="analysis/schema.json",
            )
        if quality is not None:
            report.add(
                "quality_matches_profile",
                quality.dataset == profile.source.name and quality.rows_checked == profile.rows,
                f"quality checked {quality.rows_checked} rows of '{quality.dataset}'",
                object="analysis/quality_report.json",
            )
        if metrics is not None:
            report.add(
                "metrics_match_profile",
                metrics.rows == profile.rows,
                f"metrics.json covers {metrics.rows} rows, profile {profile.rows}",
                object="analysis/metrics.json",
            )
        if recheck_source:
            frame = _recheck_source(report, profile)
            if frame is not None and metrics is not None:
                cross_check_metrics(report, frame, metrics)
    return report


def _recheck_source(report: ValidationReport, profile: DatasetProfile) -> pl.DataFrame | None:
    source = profile.source
    if source.path is not None and not source.path.exists():
        report.findings.append(
            ValidationFinding(
                check="row_count_matches_source",
                status=CheckStatus.NOT_RUN,
                object=str(source.path),
                message="source file not reachable from here",
            )
        )
        return None
    try:
        frame = load_source(source).frame
    except DataLoadError as exc:
        report.findings.append(
            ValidationFinding(
                check="row_count_matches_source",
                status=CheckStatus.NOT_RUN,
                object=source.name,
                message=str(exc),
            )
        )
        return None
    matches = frame.height == profile.rows and frame.width == profile.columns
    report.add(
        "row_count_matches_source",
        matches,
        f"source has {frame.height}x{frame.width}, profile says {profile.rows}x{profile.columns}"
        " (re-run analyze if the source changed)",
        object=source.name,
        severity=Severity.ERROR,
    )
    return frame if matches else None


def cross_check_metrics(
    report: ValidationReport, frame: pl.DataFrame, metrics: DatasetMetrics
) -> None:
    """Recompute every measure total with DuckDB SQL, independently of the Polars pipeline."""
    names = [n for n, stats in metrics.measures.items() if stats.get("sum") is not None]
    if not names:
        return
    missing = [n for n in names if n not in frame.columns]
    if missing:
        report.add(
            "metrics_columns_exist",
            False,
            f"metrics.json has unknown columns {missing}",
            object="analysis/metrics.json",
        )
        return
    select = ", ".join(f"SUM({quote_identifier(n)})::DOUBLE" for n in names)
    try:
        with DuckDBSession() as db:
            db.register("source", frame.select(names))
            row = db.query(f"SELECT {select} FROM source").row(0)  # noqa: S608 - quoted identifiers
    except QueryError as exc:
        report.findings.append(
            ValidationFinding(
                check="metrics_match_sql", status=CheckStatus.NOT_RUN, message=str(exc)
            )
        )
        return
    for name, sql_total in zip(names, row, strict=True):
        recorded = metrics.measures[name]["sum"]
        ok = (
            sql_total is not None
            and recorded is not None
            and math.isclose(recorded, sql_total, rel_tol=1e-9, abs_tol=1e-6)
        )
        report.add(
            "metrics_match_sql",
            ok,
            f"metrics.json sum={recorded}, DuckDB SUM={sql_total}",
            object=f"analysis/metrics.json:{name}",
        )


def check_referential_integrity(
    fact: pl.DataFrame, fact_key: str, dimension: pl.DataFrame, dimension_key: str
) -> ValidationFinding:
    """Every non-null fact key must exist in the dimension key column."""
    keys = dimension.get_column(dimension_key).drop_nulls().unique()
    fact_keys = fact.get_column(fact_key).drop_nulls()
    orphans = fact_keys.filter(~fact_keys.is_in(keys.implode()))
    ok = orphans.is_empty()
    examples = orphans.unique().head(5).to_list()
    return ValidationFinding(
        check="referential_integrity",
        status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
        severity=Severity.INFO if ok else Severity.ERROR,
        object=f"{fact_key} -> {dimension_key}",
        message="" if ok else f"{orphans.len():,} orphan rows; e.g. {examples}",
    )
