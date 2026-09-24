"""Star-schema design: flat table -> fact + dimensions + date table.

Returns the :class:`ModelDesign` (spec, decisions, findings) and the materialised
tables. Nothing is written here; the modeler agent persists the result.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from powerbi_agent.data.analyzer import BusinessColumns
from powerbi_agent.modeling.diff import diff_specs
from powerbi_agent.modeling.dimensions import (
    DimensionPlan,
    build_date_dimension,
    build_dimension,
    detect_hierarchies,
    entity_keys,
    near_misses,
    plan_dimensions,
)
from powerbi_agent.modeling.facts import detect_grain, entity_name, fact_table_name, pascal
from powerbi_agent.modeling.relationships import tmdl_type
from powerbi_agent.models.analysis import ColumnRole, DatasetProfile, DatasetSchema, Severity
from powerbi_agent.models.model_design import ModelDesign, ModelFinding
from powerbi_agent.models.semantic_model import (
    ColumnSpec,
    HierarchySpec,
    ModelInfo,
    RelationshipSpec,
    SemanticModelSpec,
    SummarizeBy,
    TableKind,
    TableSpec,
)

DATE_TABLE = "DimDate"
MODEL_DATA_DIR = "model_data"


def _rows(n: int, noun: str = "row") -> str:
    return f"{n:,} {noun}" + ("" if n == 1 else "s")


def _have(n: int) -> str:
    return "has" if n == 1 else "have"


def _source(table: str) -> str:
    return f"{MODEL_DATA_DIR}/{table}.parquet"


def _columns(
    frame: pl.DataFrame, overrides: dict[str, dict[str, object]] | None = None
) -> list[ColumnSpec]:
    overrides = overrides or {}
    specs = []
    for name, dtype in frame.schema.items():
        extra = overrides.get(name, {})
        specs.append(ColumnSpec(name=name, data_type=tmdl_type(dtype), source_column=name, **extra))
    return specs


def _unique_name(name: str, taken: set[str]) -> str:
    candidate, n = name, 2
    while candidate.lower() in taken:
        candidate, n = f"{name}{n}", n + 1
    taken.add(candidate.lower())
    return candidate


class _Builder:
    def __init__(self, frame: pl.DataFrame, profile: DatasetProfile, schema: DatasetSchema) -> None:
        self.frame = frame
        self.profile = profile
        self.schema = schema
        self.fact = fact_table_name(profile.source.name)
        self.taken = {self.fact.lower()}
        self.tables: dict[str, pl.DataFrame] = {}
        self.specs: list[TableSpec] = []
        self.relationships: list[RelationshipSpec] = []
        self.decisions: list[str] = []
        self.findings: list[ModelFinding] = []

    # -- dimensions from keys ---------------------------------------------------------------
    def add_entity_dimension(self, plan: DimensionPlan) -> str:
        name = _unique_name(f"Dim{entity_name(plan.key)}", self.taken)
        dim = build_dimension(self.frame, plan)
        chains = detect_hierarchies(dim, plan.attributes)
        overrides: dict[str, dict[str, object]] = {plan.key: {"is_key": True}}
        self.specs.append(
            TableSpec(
                name=name,
                kind=TableKind.DIMENSION,
                columns=_columns(dim, overrides),
                hierarchies=[HierarchySpec(name=f"{c[0]} Hierarchy", levels=c) for c in chains],
                source=_source(name),
                description=f"One row per {plan.key}.",
            )
        )
        self.tables[name] = dim
        self.relationships.append(
            RelationshipSpec(
                from_table=self.fact, from_column=plan.key, to_table=name, to_column=plan.key
            )
        )
        self.decisions.append(
            f"{name}: one row per {plan.key} ({dim.height:,} rows) with "
            f"{', '.join(plan.attributes)}; these columns are determined by {plan.key}."
        )
        for levels in chains:
            self.decisions.append(f"{name}: hierarchy {' > '.join(levels)}.")
        for attribute, rows in plan.conflicts.items():
            self.findings.append(
                ModelFinding(
                    severity=Severity.WARNING,
                    topic="attribute_conflicts",
                    table=name,
                    column=attribute,
                    affected_rows=rows,
                    message=(
                        f"{_rows(rows)} {_have(rows)} a {attribute} that differs from the "
                        f"most frequent value for their {plan.key}. {name} keeps the most "
                        "frequent value, so these rows will report under it. Fix the source "
                        "if that is wrong."
                    ),
                )
            )
        self._report_null_keys(plan, name)
        return name

    def _report_null_keys(self, plan: DimensionPlan, dim: str) -> None:
        missing = self.frame.filter(pl.col(plan.key).is_null())
        if missing.is_empty():
            return
        with_values = missing.filter(
            pl.any_horizontal(pl.col(a).is_not_null() for a in plan.attributes)
        ).height
        lost = (
            f" {with_values:,} of them carry {', '.join(plan.attributes)} values that cannot be "
            f"linked to {dim}."
            if with_values
            else ""
        )
        self.findings.append(
            ModelFinding(
                severity=Severity.WARNING,
                topic="null_foreign_keys",
                table=self.fact,
                column=plan.key,
                affected_rows=missing.height,
                message=(
                    f"{_rows(missing.height, 'fact row')} {_have(missing.height)} no "
                    f"{plan.key}; they relate to no {dim} row and show as (Blank) when "
                    f"sliced by {dim}.{lost}"
                ),
            )
        )

    # -- date dimension ---------------------------------------------------------------------
    def add_date_dimension(self, fact: pl.DataFrame, primary: str | None) -> pl.DataFrame:
        date_cols = [c.name for c in self.schema.by_role(ColumnRole.DATE)]
        if not date_cols:
            self.decisions.append("No date column found: no date table was created.")
            return fact
        primary = primary if primary in date_cols else date_cols[0]
        link_cols: dict[str, str] = {}
        for col in date_cols:
            if isinstance(fact.schema[col], pl.Datetime):
                derived = _unique_name(f"{col}Date", {c.lower() for c in fact.columns})
                fact = fact.with_columns(pl.col(col).dt.date().alias(derived))
                link_cols[col] = derived
                self.decisions.append(
                    f"{col} has a time part: added {self.fact}[{derived}] (date only) to relate "
                    f"to {DATE_TABLE}."
                )
            else:
                link_cols[col] = col
        bounds = fact.select(
            pl.min_horizontal(pl.col(link_cols[c]).min() for c in date_cols).alias("lo"),
            pl.max_horizontal(pl.col(link_cols[c]).max() for c in date_cols).alias("hi"),
        ).row(0)
        lo, hi = bounds
        if not isinstance(lo, date) or not isinstance(hi, date):
            self.decisions.append("Date columns are empty: no date table was created.")
            return fact
        name = _unique_name(DATE_TABLE, self.taken)
        dim = build_date_dimension(lo, hi)
        self.tables[name] = dim
        overrides: dict[str, dict[str, object]] = {
            "Date": {"is_key": True, "format_string": "yyyy-mm-dd"},
            "MonthNumber": {"is_hidden": True},
            "WeekdayNumber": {"is_hidden": True},
            "Month": {"sort_by_column": "MonthNumber"},
            "Weekday": {"sort_by_column": "WeekdayNumber"},
        }
        self.specs.append(
            TableSpec(
                name=name,
                kind=TableKind.DATE,
                columns=_columns(dim, overrides),
                hierarchies=[
                    HierarchySpec(name="Calendar", levels=["Year", "Quarter", "Month", "Date"])
                ],
                source=_source(name),
                description="Contiguous calendar (whole years). Mark as date table.",
            )
        )
        for col in date_cols:
            self.relationships.append(
                RelationshipSpec(
                    from_table=self.fact,
                    from_column=link_cols[col],
                    to_table=name,
                    to_column="Date",
                    is_active=col == primary,
                )
            )
        inactive = [c for c in date_cols if c != primary]
        self.decisions.append(
            f"{name}: {dim.height:,} days from {dim.item(0, 'Date')} to {dim.item(-1, 'Date')} "
            f"(whole years, no gaps) so time intelligence works; active relationship on "
            f"{link_cols[primary]}"
            + (
                f", inactive (role-playing) on {', '.join(link_cols[c] for c in inactive)}"
                if inactive
                else ""
            )
            + "."
        )
        return fact

    # -- fact -------------------------------------------------------------------------------
    def add_fact(self, fact: pl.DataFrame, grain: list[str], grain_unique: bool) -> None:
        roles = {c.name: c for c in self.schema.columns}
        fk_cols = {r.from_column for r in self.relationships}
        overrides: dict[str, dict[str, object]] = {}
        for name in fact.columns:
            col = roles.get(name)
            if name in fk_cols:
                overrides[name] = {"is_hidden": True}
            elif col is not None and col.role is ColumnRole.MEASURE:
                overrides[name] = {
                    "summarize_by": SummarizeBy.SUM if col.additive else SummarizeBy.NONE
                }
        grain_text = " + ".join(grain) if grain else "no unique column combination found"
        self.specs.insert(
            0,
            TableSpec(
                name=self.fact,
                kind=TableKind.FACT,
                columns=_columns(fact, overrides),
                source=_source(self.fact),
                description=f"Grain: one row per {grain_text}.",
            ),
        )
        self.tables[self.fact] = fact


def design_star_schema(
    frame: pl.DataFrame,
    profile: DatasetProfile,
    schema: DatasetSchema,
    *,
    previous: SemanticModelSpec | None = None,
) -> tuple[ModelDesign, dict[str, pl.DataFrame]]:
    b = _Builder(frame, profile, schema)

    grain, grain_unique = detect_grain(frame, schema)
    if not grain:
        b.findings.append(
            ModelFinding(
                severity=Severity.WARNING,
                topic="grain",
                table=b.fact,
                message="No combination of up to 3 key/label/date columns identifies a row. "
                "Confirm the fact grain with the user.",
            )
        )
    elif not grain_unique:
        b.findings.append(
            ModelFinding(
                severity=Severity.WARNING,
                topic="grain",
                table=b.fact,
                affected_rows=profile.duplicate_rows,
                message=f"{' + '.join(grain)} identifies a row except for "
                f"{profile.duplicate_rows:,} exact duplicate rows, which are kept in "
                f"{b.fact} and will be double counted unless removed upstream.",
            )
        )
    b.decisions.append(
        f"Grain: one {b.fact} row per {' + '.join(grain)}."
        if grain
        else f"Grain of {b.fact} is unclear (see findings)."
    )

    plans = plan_dimensions(frame, schema)
    moved = {a for p in plans for a in p.attributes}
    for miss in near_misses(frame, schema, moved):
        b.findings.append(
            ModelFinding(
                severity=Severity.WARNING,
                topic="near_dependency",
                table=b.fact,
                column=miss.attribute,
                affected_rows=miss.conflicting_rows,
                message=(
                    f"{miss.attribute} is almost determined by {miss.key} "
                    f"({miss.agreement:.1%} of rows agree), but {_rows(miss.conflicting_rows)} "
                    f"conflict, so it stays in {b.fact}. Fix those rows to move it into the "
                    f"{miss.key} dimension."
                ),
            )
        )
    for plan in plans:
        b.add_entity_dimension(plan)

    fact = frame.select([c for c in frame.columns if c not in moved])
    fact = b.add_date_dimension(fact, BusinessColumns(schema).date)
    b.add_fact(fact, grain, grain_unique)

    dimension_keys = {p.key for p in plans}
    for key in entity_keys(frame, schema):
        if key not in dimension_keys:
            b.decisions.append(
                f"{key} stays in {b.fact} as a degenerate dimension (no attributes of its own)."
            )
    leftover = [
        c.name
        for c in schema.by_role(ColumnRole.DIMENSION_ATTRIBUTE, ColumnRole.IDENTIFIER)
        if c.name not in moved
    ]
    if leftover:
        verb = "stays" if len(leftover) == 1 else "stay"
        b.decisions.append(f"{', '.join(leftover)} {verb} in {b.fact}: no key determines it.")
    b.decisions.append(
        "Relationships use the natural keys from the data (no surrogate keys); many-to-one, "
        "single-direction filtering."
    )

    spec = SemanticModelSpec(
        model=ModelInfo(
            name=pascal(profile.source.name),
            description=f"Star schema generated from {profile.source.name}.",
        ),
        tables=b.specs,
        relationships=b.relationships,
    )
    design = ModelDesign(
        spec=spec,
        fact_table=b.fact,
        grain=grain,
        grain_unique=grain_unique,
        decisions=b.decisions,
        findings=b.findings,
        table_rows={name: t.height for name, t in b.tables.items()},
        diff=diff_specs(previous, spec),
    )
    return design, b.tables
