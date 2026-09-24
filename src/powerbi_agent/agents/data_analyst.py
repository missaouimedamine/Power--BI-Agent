"""Data Analyst agent: load, profile, quality-check and summarise one dataset.

Each step writes its own artifacts under ``analysis/`` and verifies they exist
before reporting success. Loaded frames are passed between steps via
``AgentContext.scratch``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from pydantic import ValidationError

from powerbi_agent.core.agent import AgentContext, BaseAgent
from powerbi_agent.data.analyzer import BusinessColumns, compute_metrics, generate_insights
from powerbi_agent.data.charts import matplotlib_available, render_charts
from powerbi_agent.data.loaders import DataLoadError, LoadedDataset, load_source
from powerbi_agent.data.outputs import render_insights_md
from powerbi_agent.data.profiler import profile_dataset
from powerbi_agent.data.quality import check_quality
from powerbi_agent.data.schema import build_schema
from powerbi_agent.models.analysis import (
    ColumnRole,
    DatasetProfile,
    DatasetSchema,
    QualityReport,
    Severity,
)
from powerbi_agent.models.datasource import DataSource
from powerbi_agent.models.tasks import Capability, PlanStep, StepResult, TaskStatus
from powerbi_agent.tools.filesystem import Workspace
from powerbi_agent.utils.logging import get_logger

logger = get_logger("agents.data_analyst")

DATASET, PROFILE, SCHEMA, QUALITY = "dataset", "profile", "schema", "quality"


class MissingPrerequisiteError(RuntimeError):
    pass


def _require[T](context: AgentContext, key: str, kind: type[T]) -> T:
    value = context.scratch.get(key)
    if not isinstance(value, kind):
        raise MissingPrerequisiteError(f"'{key}' is not available; run the earlier steps first")
    return value


class DataAnalystAgent(BaseAgent):
    name = "data_analyst"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {
            Capability.LOAD_DATA,
            Capability.PROFILE_DATA,
            Capability.CHECK_QUALITY,
            Capability.GENERATE_INSIGHTS,
        }
    )

    def run(self, step: PlanStep, context: AgentContext) -> StepResult:
        handlers: dict[Capability, Callable[[PlanStep, AgentContext], StepResult]] = {
            Capability.LOAD_DATA: self._load,
            Capability.PROFILE_DATA: self._profile,
            Capability.CHECK_QUALITY: self._quality,
            Capability.GENERATE_INSIGHTS: self._insights,
        }
        try:
            return handlers[step.capability](step, context)
        except MissingPrerequisiteError as exc:
            return self.result(step, TaskStatus.FAILED, error=str(exc))

    def _workspace(self, context: AgentContext) -> Workspace:
        return Workspace(context.workspace_dir, run_id=context.task_id)

    def _written(
        self, step: PlanStep, ws: Workspace, paths: list[str], **output: object
    ) -> StepResult:
        missing = [p for p in paths if not ws.path(p).is_file()]
        if missing:
            return self.result(step, TaskStatus.FAILED, error=f"artifacts not written: {missing}")
        return self.result(step, TaskStatus.SUCCEEDED, artifacts=paths, output=dict(output))

    # --- steps --------------------------------------------------------------------------
    def _load(self, step: PlanStep, context: AgentContext) -> StepResult:
        raw = step.inputs.get("sources", [])
        try:
            sources = [DataSource.model_validate(s) for s in raw]
        except ValidationError as exc:
            return self.result(step, TaskStatus.FAILED, error=f"invalid data source: {exc}")
        if len(sources) != 1:
            msg = (
                "no data source given; ask the user which file or query to analyse"
                if not sources
                else f"{len(sources)} sources given; analyse one dataset per run for now"
            )
            return self.result(step, TaskStatus.FAILED, error=msg)
        try:
            dataset = load_source(sources[0])
        except DataLoadError as exc:
            return self.result(step, TaskStatus.FAILED, error=str(exc))
        if dataset.frame.height == 0 or dataset.frame.width == 0:
            return self.result(step, TaskStatus.FAILED, error="the dataset has no rows or columns")

        context.scratch[DATASET] = dataset
        return self.result(
            step,
            TaskStatus.SUCCEEDED,
            output={
                "dataset": dataset.name,
                "rows": dataset.frame.height,
                "columns": dataset.frame.width,
                "sheet": dataset.source.sheet,
                "available_sheets": dataset.available_sheets,
                "notes": dataset.notes,
            },
        )

    def _profile(self, step: PlanStep, context: AgentContext) -> StepResult:
        dataset = _require(context, DATASET, LoadedDataset)
        profile = profile_dataset(dataset)
        schema = build_schema(profile)
        context.scratch[PROFILE], context.scratch[SCHEMA] = profile, schema

        ws = self._workspace(context)
        paths = [
            ws.relative(ws.write_json("analysis/profile.json", profile)),
            ws.relative(ws.write_json("analysis/schema.json", schema)),
        ]
        roles: dict[str, int] = {}
        for col in schema.columns:
            roles[col.role.value] = roles.get(col.role.value, 0) + 1
        return self._written(
            step,
            ws,
            paths,
            rows=profile.rows,
            columns=profile.columns,
            missing_values=profile.missing_values,
            duplicate_rows=profile.duplicate_rows,
            roles=roles,
        )

    def _quality(self, step: PlanStep, context: AgentContext) -> StepResult:
        dataset = _require(context, DATASET, LoadedDataset)
        profile = _require(context, PROFILE, DatasetProfile)
        schema = _require(context, SCHEMA, DatasetSchema)
        report = check_quality(dataset.frame, profile, schema)
        context.scratch[QUALITY] = report

        ws = self._workspace(context)
        path = ws.relative(ws.write_json("analysis/quality_report.json", report))
        return self._written(
            step,
            ws,
            [path],
            passed=report.passed,
            errors=report.count(Severity.ERROR),
            warnings=report.count(Severity.WARNING),
            info=report.count(Severity.INFO),
            issues=[
                {"kind": i.kind.value, "column": i.column, "rows": i.affected_rows}
                for i in report.issues
            ],
        )

    def _insights(self, step: PlanStep, context: AgentContext) -> StepResult:
        dataset = _require(context, DATASET, LoadedDataset)
        profile = _require(context, PROFILE, DatasetProfile)
        schema = _require(context, SCHEMA, DatasetSchema)
        quality = _require(context, QUALITY, QualityReport)
        frame = dataset.frame
        cols = BusinessColumns(schema)
        metrics = compute_metrics(frame, profile, schema, cols)
        insights = generate_insights(frame, profile, schema, quality, cols)

        ws = self._workspace(context)
        notes = list(dataset.notes)
        charts = render_charts(frame, profile, schema, cols, ws)
        if not matplotlib_available():
            notes.append("Charts skipped: install the 'viz' extra (matplotlib) to generate them.")
        markdown = render_insights_md(profile, schema, quality, metrics, insights, notes, charts)
        paths = [
            ws.relative(ws.write_json("analysis/metrics.json", metrics)),
            ws.relative(ws.write_text("analysis/insights.md", markdown)),
            *charts,
        ]
        return self._written(
            step,
            ws,
            paths,
            candidate_dimensions=[
                c.name
                for c in schema.by_role(
                    ColumnRole.DIMENSION_ATTRIBUTE, ColumnRole.DATE, ColumnRole.FOREIGN_KEY
                )
            ],
            candidate_measures=cols.measures,
            kpis=[
                {"name": k.name, "value": k.value, "format": k.format_hint}
                for k in metrics.suggested_kpis
            ],
            insights=[i.title for i in insights],
            charts=len([c for c in charts if c.endswith(".png")]),
        )
