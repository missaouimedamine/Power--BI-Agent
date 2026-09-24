"""Power BI Modeler agent: design the star schema and materialise its tables.

Writes only into the workspace (``specs/``, ``model_data/``); nothing touches Power BI.
The previous design, if any, is inspected first and the change is reported as a diff.
"""

from __future__ import annotations

import io
import json
from typing import ClassVar

import polars as pl
from pydantic import ValidationError

from powerbi_agent.agents.data_analyst import DATASET, PROFILE, SCHEMA, MissingPrerequisiteError
from powerbi_agent.core.agent import AgentContext, BaseAgent
from powerbi_agent.data.loaders import LoadedDataset
from powerbi_agent.modeling.design_md import render_model_design_md
from powerbi_agent.modeling.diff import summarize_diff
from powerbi_agent.modeling.star_schema import design_star_schema
from powerbi_agent.models.analysis import DatasetProfile, DatasetSchema
from powerbi_agent.models.tasks import Capability, PlanStep, StepResult, TaskStatus
from powerbi_agent.tools.filesystem import Workspace
from powerbi_agent.validation.model_validator import SPEC_PATH, load_spec

DESIGN_JSON = "specs/model_design.json"
DESIGN_MD = "specs/model_design.md"
MODEL_DESIGN = "model_design"


def _parquet(frame: pl.DataFrame) -> bytes:
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    return buffer.getvalue()


class PowerBIModelerAgent(BaseAgent):
    name = "powerbi_modeler"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.DESIGN_STAR_SCHEMA})

    def run(self, step: PlanStep, context: AgentContext) -> StepResult:
        dataset = context.scratch.get(DATASET)
        profile = context.scratch.get(PROFILE)
        schema = context.scratch.get(SCHEMA)
        if not (
            isinstance(dataset, LoadedDataset)
            and isinstance(profile, DatasetProfile)
            and isinstance(schema, DatasetSchema)
        ):
            error = str(MissingPrerequisiteError("load and profile the data first"))
            return self.result(step, TaskStatus.FAILED, error=error)

        # Inspect before modifying: the previous spec is the baseline for the diff.
        try:
            previous = load_spec(context.workspace_dir)
        except (ValidationError, json.JSONDecodeError, UnicodeDecodeError):
            previous = None

        design, tables = design_star_schema(dataset.frame, profile, schema, previous=previous)
        context.scratch[MODEL_DESIGN] = design

        ws = Workspace(context.workspace_dir, run_id=context.task_id)
        paths = []
        for table in design.spec.tables:
            assert table.source is not None
            paths.append(ws.relative(ws.write_bytes(table.source, _parquet(tables[table.name]))))
        paths.append(ws.relative(ws.write_json(SPEC_PATH, design.spec)))
        paths.append(ws.relative(ws.write_json(DESIGN_JSON, design)))
        paths.append(ws.relative(ws.write_text(DESIGN_MD, render_model_design_md(design))))

        missing = [p for p in paths if not ws.path(p).is_file()]
        if missing:
            return self.result(step, TaskStatus.FAILED, error=f"artifacts not written: {missing}")
        return self.result(
            step,
            TaskStatus.SUCCEEDED,
            artifacts=paths,
            output={
                "model": design.spec.model.name,
                "fact_table": design.fact_table,
                "grain": design.grain,
                "tables": [
                    {"name": t.name, "kind": t.kind.value, "rows": design.table_rows[t.name]}
                    for t in design.spec.tables
                ],
                "relationships": [r.key for r in design.spec.relationships],
                "hierarchies": [
                    f"{t.name}: {' > '.join(h.levels)}"
                    for t in design.spec.tables
                    for h in t.hierarchies
                ],
                "findings": [
                    {
                        "severity": f.severity.value,
                        "topic": f.topic,
                        "rows": f.affected_rows,
                        "message": f.message,
                    }
                    for f in design.findings
                ],
                "changes": summarize_diff(design.diff) if design.diff else [],
            },
        )
