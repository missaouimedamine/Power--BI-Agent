"""Turn a user request into a validated :class:`Plan`.

The rule-based planner is deterministic and needs no LLM. An LLM-backed planner can
implement the same :class:`Planner` protocol later; its output must still be parsed
into :class:`Plan` so step capabilities and risks are always from a closed set.
"""

from __future__ import annotations

import re
from typing import Protocol

from powerbi_agent.models.datasource import DataSource
from powerbi_agent.models.tasks import Capability, Intent, Plan, PlanStep, Risk

_DATA_FILE = re.compile(
    r"""(?P<path>(?:[A-Za-z]:)?[\w./\\~-]*[\w-]+\.(?:csv|tsv|xlsx|xlsm|xls|parquet|pq))\b""",
    re.IGNORECASE,
)

# Checked in order: the most far-reaching intent mentioned wins.
_INTENT_KEYWORDS: list[tuple[Intent, tuple[str, ...]]] = [
    (Intent.PUBLISH, ("publish", "deploy", "upload to power bi", "push to fabric")),
    (Intent.GENERATE_REPORT, ("dashboard", "report", "visual", "page")),
    (Intent.GENERATE_MODEL, ("semantic model", "pbip", "tmdl", "power bi model", "build model")),
    (Intent.GENERATE_DAX, ("dax", "measure")),
    (Intent.DESIGN_MODEL, ("star schema", "design model", "design a model", "data model")),
    (Intent.VALIDATE, ("validate", "check model", "verify")),
    (Intent.PROFILE, ("profile", "profiling")),
    (Intent.ANALYZE, ("analy", "insight", "explore", "eda", "quality")),
]

_LOAD = (Capability.LOAD_DATA, "Load and inspect the data source", Risk.READ_ONLY)
_PROFILE = (
    Capability.PROFILE_DATA,
    "Profile columns, types and roles (profile.json, schema.json)",
    Risk.LOCAL_WRITE,
)
_QUALITY = (
    Capability.CHECK_QUALITY,
    "Detect data-quality issues (quality_report.json)",
    Risk.LOCAL_WRITE,
)
_INSIGHTS = (
    Capability.GENERATE_INSIGHTS,
    "Identify KPIs, dimensions and insights (metrics.json, insights.md, charts/)",
    Risk.LOCAL_WRITE,
)
_CHECK_DATA = (Capability.VALIDATE_DATA, "Validate the analysis outputs", Risk.LOCAL_WRITE)
_ANALYSIS = [_LOAD, _PROFILE, _QUALITY, _INSIGHTS, _CHECK_DATA]
_DESIGN = [
    (Capability.DESIGN_STAR_SCHEMA, "Design the star-schema semantic model spec", Risk.READ_ONLY),
    (Capability.GENERATE_DAX, "Generate DAX measures", Risk.READ_ONLY),
    (Capability.VALIDATE_DAX, "Validate DAX measures", Risk.READ_ONLY),
]
_BUILD = [
    (Capability.GENERATE_PBIP, "Write PBIP/TMDL artifacts to the workspace", Risk.LOCAL_WRITE),
    (Capability.VALIDATE_MODEL, "Validate the generated semantic model", Risk.READ_ONLY),
]
_REPORT = [
    (Capability.DESIGN_REPORT, "Design the report specification", Risk.LOCAL_WRITE),
    (Capability.VALIDATE_REPORT, "Validate the report specification", Risk.READ_ONLY),
]
_VALIDATE = [
    _CHECK_DATA,
    (Capability.VALIDATE_MODEL, "Validate the semantic model", Risk.READ_ONLY),
    (Capability.VALIDATE_REPORT, "Validate the report", Risk.READ_ONLY),
]
_PUBLISH = [
    (Capability.PUBLISH, "Publish to the Power BI / Fabric workspace", Risk.EXTERNAL),
]

_TEMPLATES: dict[Intent, list[tuple[Capability, str, Risk]]] = {
    Intent.PROFILE: [_LOAD, _PROFILE, _CHECK_DATA],
    Intent.ANALYZE: _ANALYSIS,
    Intent.DESIGN_MODEL: _ANALYSIS + _DESIGN[:1],
    Intent.GENERATE_DAX: _ANALYSIS + _DESIGN,
    Intent.GENERATE_MODEL: _ANALYSIS + _DESIGN + _BUILD,
    Intent.GENERATE_REPORT: _ANALYSIS + _DESIGN + _BUILD + _REPORT,
    Intent.VALIDATE: _VALIDATE,
    Intent.PUBLISH: _VALIDATE + _PUBLISH,
}

_NEEDS_DATA = {
    Intent.PROFILE,
    Intent.ANALYZE,
    Intent.DESIGN_MODEL,
    Intent.GENERATE_DAX,
    Intent.GENERATE_MODEL,
    Intent.GENERATE_REPORT,
}


class Planner(Protocol):
    def plan(self, request: str) -> Plan: ...


def detect_intent(request: str) -> Intent:
    text = request.lower()
    for intent, keywords in _INTENT_KEYWORDS:
        if any(k in text for k in keywords):
            return intent
    return Intent.UNKNOWN


def extract_data_paths(request: str) -> list[str]:
    seen: dict[str, None] = {}
    for m in _DATA_FILE.finditer(request):
        seen.setdefault(m["path"], None)
    return list(seen)


def build_plan(
    request: str,
    intent: Intent,
    sources: list[DataSource] | None = None,
    *,
    notes: list[str] | None = None,
) -> Plan:
    """Build the canonical plan for ``intent``. Steps run sequentially."""
    sources = sources or []
    plan = Plan(request=request, intent=intent, notes=list(notes or []))
    previous: PlanStep | None = None
    for capability, description, risk in _TEMPLATES.get(intent, []):
        inputs: dict[str, object] = {}
        if capability is Capability.LOAD_DATA:
            inputs["sources"] = [s.model_dump(mode="json") for s in sources]
        step = PlanStep(
            capability=capability,
            description=description,
            risk=risk,
            inputs=inputs,
            depends_on=[previous.id] if previous else [],
        )
        plan.steps.append(step)
        previous = step

    if intent is Intent.UNKNOWN:
        plan.notes.append("Could not determine what to do; ask the user to clarify the goal.")
    if intent in _NEEDS_DATA and not sources:
        plan.notes.append("No data file found in the request; ask the user for the data source.")
    if intent in _NEEDS_DATA and len(sources) > 1:
        plan.notes.append("Several data files found; analysis currently handles one at a time.")
    if plan.requires_confirmation:
        plan.notes.append("Contains external/destructive steps: explicit confirmation required.")
    return plan


def sources_from_paths(paths: list[str]) -> tuple[list[DataSource], list[str]]:
    sources, notes = [], []
    for path in paths:
        try:
            sources.append(DataSource.from_path(path))
        except ValueError as exc:
            notes.append(f"Ignored {path}: {exc}")
    return sources, notes


class RuleBasedPlanner:
    """Keyword intent detection + fixed per-intent step templates."""

    def plan(self, request: str) -> Plan:
        sources, notes = sources_from_paths(extract_data_paths(request))
        return build_plan(request, detect_intent(request), sources, notes=notes)
