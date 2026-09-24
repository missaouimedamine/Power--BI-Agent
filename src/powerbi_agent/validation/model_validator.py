"""Semantic model validation (``specs/semantic_model.json`` + ``model_data/``).

Structural checks run on the spec alone; data checks load the materialised tables and
verify what the spec claims: key uniqueness, key type compatibility, orphan keys,
cardinality, date-table continuity and row counts. Errors block later phases;
warnings are design smells to show the user.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import polars as pl
from pydantic import ValidationError

from powerbi_agent.modeling.relationships import check_key_pair, tmdl_type
from powerbi_agent.models.analysis import DatasetProfile, Severity
from powerbi_agent.models.semantic_model import (
    Cardinality,
    CrossFilter,
    DataType,
    SemanticModelSpec,
    TableKind,
    TableSpec,
)
from powerbi_agent.models.validation import CheckStatus, ValidationFinding, ValidationReport
from powerbi_agent.utils.security import PathOutsideWorkspaceError, confine_path

SPEC_PATH = "specs/semantic_model.json"
_BAD_NAME_CHARS = re.compile(r"[\[\]'\"\x00-\x1f]")
_PREFIX = {TableKind.FACT: "Fact", TableKind.DIMENSION: "Dim", TableKind.DATE: "Dim"}


def load_spec(workspace_dir: Path) -> SemanticModelSpec | None:
    path = workspace_dir / SPEC_PATH
    if not path.exists():
        return None
    return SemanticModelSpec.model_validate_json(path.read_text(encoding="utf-8"))


def validate_model(workspace_dir: Path, *, check_data: bool = True) -> ValidationReport:
    report = ValidationReport(area="model", target=str(workspace_dir))
    try:
        spec = load_spec(workspace_dir)
    except (ValidationError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        detail = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else type(exc).__name__
        report.add("spec_parses", False, f"invalid model spec: {detail}", object=SPEC_PATH)
        return report
    if spec is None:
        report.add("spec_present", False, "no model spec; run design-model first", object=SPEC_PATH)
        return report
    report.add(
        "spec_parses", True, "structure, references and names are consistent", object=SPEC_PATH
    )

    _check_naming(report, spec)
    _check_structure(report, spec)
    if check_data:
        _check_data(report, spec, workspace_dir)
    return report


# --- naming ---------------------------------------------------------------------------------
def _check_naming(report: ValidationReport, spec: SemanticModelSpec) -> None:
    for table in spec.tables:
        prefix = _PREFIX.get(table.kind)
        if prefix:
            report.add(
                "table_naming",
                table.name.startswith(prefix),
                f"{table.kind.value} tables are named {prefix}<Name>",
                object=table.name,
                severity=Severity.WARNING,
            )
        names = [table.name] + [c.name for c in table.columns]
        bad = [n for n in names if n != n.strip() or _BAD_NAME_CHARS.search(n)]
        report.add(
            "object_names_clean",
            not bad,
            f"names with surrounding spaces, quotes, brackets or control characters: {bad}"
            if bad
            else "",
            object=table.name,
            severity=Severity.WARNING,
        )


# --- structure ------------------------------------------------------------------------------
def _check_structure(report: ValidationReport, spec: SemanticModelSpec) -> None:
    kinds = {t.name: t.kind for t in spec.tables}
    facts = [t for t in spec.tables if t.kind is TableKind.FACT]
    report.add("has_fact_table", bool(facts), "a star schema needs at least one fact table")

    related: set[str] = set()
    for rel in spec.relationships:
        related |= {rel.from_table, rel.to_table}
        report.add(
            "relationship_direction",
            kinds[rel.from_table] in {TableKind.FACT, TableKind.BRIDGE}
            and kinds[rel.to_table] in {TableKind.DIMENSION, TableKind.DATE},
            "relationships should go from a fact to a dimension (anything else snowflakes)",
            object=rel.key,
            severity=Severity.WARNING,
        )
        to_col = spec.table(rel.to_table).column(rel.to_column)  # type: ignore[union-attr]
        report.add(
            "relationship_to_key",
            bool(to_col and to_col.is_key),
            "the one side of a relationship should be the table's key column",
            object=rel.key,
            severity=Severity.WARNING,
        )
        from_col = spec.table(rel.from_table).column(rel.from_column)  # type: ignore[union-attr]
        if kinds[rel.from_table] is TableKind.FACT and from_col is not None:
            report.add(
                "foreign_key_hidden",
                from_col.is_hidden,
                "fact foreign keys should be hidden (users slice by the dimension)",
                object=f"{rel.from_table}[{rel.from_column}]",
                severity=Severity.INFO,
            )
        report.add(
            "single_direction_filter",
            rel.cross_filter is CrossFilter.SINGLE,
            "bidirectional filtering needs a written justification",
            object=rel.key,
            severity=Severity.WARNING,
        )
        report.add(
            "no_many_to_many",
            rel.cardinality is not Cardinality.MANY_TO_MANY,
            "many-to-many relationships need a written justification",
            object=rel.key,
            severity=Severity.WARNING,
        )

    for table in spec.tables:
        report.add(
            "table_connected",
            table.name in related or len(spec.tables) == 1,
            "table has no relationships",
            object=table.name,
            severity=Severity.WARNING,
        )

    # Ambiguity: a cycle among active relationships means two filter paths.
    parent = {t.name: t.name for t in spec.tables}

    def root(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    cycles = []
    for rel in spec.relationships:
        if not rel.is_active:
            continue
        a, b = root(rel.from_table), root(rel.to_table)
        if a == b:
            cycles.append(rel.key)
        else:
            parent[a] = b
    report.add(
        "no_ambiguous_paths",
        not cycles,
        f"active relationships form a cycle (ambiguous filter paths): {cycles}" if cycles else "",
        object=SPEC_PATH,
    )

    for fact in facts:
        has_dates = any(c.data_type is DataType.DATETIME for c in fact.columns)
        dated = any(
            r.from_table == fact.name and kinds[r.to_table] is TableKind.DATE
            for r in spec.relationships
        )
        if has_dates:
            report.add(
                "date_table_related",
                dated,
                "fact has date columns but no relationship to a date table",
                object=fact.name,
                severity=Severity.WARNING,
            )


# --- data -----------------------------------------------------------------------------------
def _load_tables(
    report: ValidationReport, spec: SemanticModelSpec, workspace_dir: Path
) -> dict[str, pl.DataFrame]:
    frames: dict[str, pl.DataFrame] = {}
    for table in spec.tables:
        if table.source is None:
            report.findings.append(
                ValidationFinding(
                    check="table_data_present",
                    status=CheckStatus.NOT_RUN,
                    object=table.name,
                    message="table has no materialised data to check",
                )
            )
            continue
        try:
            path = confine_path(workspace_dir, table.source)
        except PathOutsideWorkspaceError:
            report.add(
                "table_data_present", False, "source path escapes the workspace", object=table.name
            )
            continue
        if not path.is_file():
            report.add("table_data_present", False, f"missing {table.source}", object=table.name)
            continue
        try:
            frames[table.name] = pl.read_parquet(path)
        except (pl.exceptions.PolarsError, OSError) as exc:
            report.add("table_data_present", False, f"unreadable: {exc}", object=table.name)
            continue
        report.add("table_data_present", True, object=table.name)
        _check_columns(report, table, frames[table.name])
    return frames


def _check_columns(report: ValidationReport, table: TableSpec, frame: pl.DataFrame) -> None:
    missing, mismatched = [], []
    for col in table.columns:
        source = col.source_column or col.name
        if source not in frame.columns:
            missing.append(source)
        elif tmdl_type(frame.schema[source]) is not col.data_type:
            mismatched.append(
                f"{col.name}: spec {col.data_type.value}, data {frame.schema[source]}"
            )
    report.add(
        "columns_match_data",
        not missing and not mismatched,
        f"missing {missing}; type mismatches {mismatched}" if missing or mismatched else "",
        object=table.name,
    )
    report.add(
        "table_not_empty",
        frame.height > 0,
        f"{frame.height:,} rows",
        object=table.name,
        severity=Severity.WARNING,
    )
    for col in table.columns:
        if col.is_key and (col.source_column or col.name) in frame.columns:
            values = frame.get_column(col.source_column or col.name)
            nulls, dupes = values.null_count(), values.len() - values.n_unique()
            report.add(
                "key_unique_not_null",
                nulls == 0 and dupes == 0,
                f"{nulls:,} nulls, {dupes:,} duplicates" if nulls or dupes else "",
                object=f"{table.name}[{col.name}]",
            )


def _check_data(report: ValidationReport, spec: SemanticModelSpec, workspace_dir: Path) -> None:
    frames = _load_tables(report, spec, workspace_dir)

    for rel in spec.relationships:
        many, one = frames.get(rel.from_table), frames.get(rel.to_table)
        if (
            many is None
            or one is None
            or rel.from_column not in many.columns
            or rel.to_column not in one.columns
        ):
            report.findings.append(
                ValidationFinding(
                    check="relationship_keys",
                    status=CheckStatus.NOT_RUN,
                    object=rel.key,
                    message="table data not available",
                )
            )
            continue
        check = check_key_pair(many, rel.from_column, one, rel.to_column)
        report.add(
            "key_types_compatible",
            check.type_compatible,
            object=rel.key,
            message=f"{many.schema[rel.from_column]} vs {one.schema[rel.to_column]}",
        )
        report.add(
            "cardinality_matches_data",
            check.to_side_unique and check.to_side_nulls == 0,
            f"{rel.to_table}[{rel.to_column}] must be unique and non-null for many-to-one",
            object=rel.key,
        )
        report.add(
            "no_orphan_keys",
            check.orphan_rows == 0,
            f"{check.orphan_rows:,} fact rows reference keys missing from {rel.to_table}, "
            f"e.g. {check.orphan_examples}"
            if check.orphan_rows
            else "",
            object=rel.key,
        )
        report.add(
            "no_blank_foreign_keys",
            check.null_from_rows == 0,
            f"{check.null_from_rows:,} rows have no {rel.from_column} and show as (Blank)",
            object=rel.key,
            severity=Severity.WARNING,
        )

    for table in spec.tables:
        if table.kind is TableKind.DATE and table.name in frames:
            _check_date_table(report, spec, table, frames)

    fact = next((t for t in spec.tables if t.kind is TableKind.FACT), None)
    profile_path = workspace_dir / "analysis" / "profile.json"
    if fact is not None and fact.name in frames and profile_path.exists():
        profile = DatasetProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))
        rows = frames[fact.name].height
        report.add(
            "fact_rows_match_source",
            rows == profile.rows,
            f"{fact.name} has {rows:,} rows, source profile {profile.rows:,}",
            object=fact.name,
        )


def _check_date_table(
    report: ValidationReport,
    spec: SemanticModelSpec,
    table: TableSpec,
    frames: dict[str, pl.DataFrame],
) -> None:
    key = next((c.name for c in table.columns if c.is_key), None)
    frame = frames[table.name]
    if key is None or key not in frame.columns or frame.is_empty():
        report.add("date_table_key", False, "date table needs a key date column", object=table.name)
        return
    dates = frame.get_column(key).cast(pl.Date)
    lo, hi = _date_bounds(dates)
    if lo is None or hi is None:
        report.add("date_table_key", False, "date table key is empty", object=table.name)
        return
    span = (hi - lo).days + 1
    report.add(
        "date_table_contiguous",
        dates.n_unique() == dates.len() == span,
        f"{dates.len():,} rows for a {span:,}-day range",
        object=table.name,
    )
    for rel in spec.relationships:
        fact_frame = frames.get(rel.from_table)
        if rel.to_table != table.name or fact_frame is None:
            continue
        first, last = _date_bounds(fact_frame.get_column(rel.from_column).cast(pl.Date))
        if first is None or last is None:
            continue
        report.add(
            "date_table_covers_facts",
            lo <= first and last <= hi,
            f"facts span {first}..{last}, date table {lo}..{hi}",
            object=rel.key,
        )


def _date_bounds(values: pl.Series) -> tuple[date | None, date | None]:
    lo, hi = values.min(), values.max()
    return (
        lo if isinstance(lo, date) else None,
        hi if isinstance(hi, date) else None,
    )
