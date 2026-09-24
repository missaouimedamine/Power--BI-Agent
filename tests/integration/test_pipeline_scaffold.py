"""End-to-end pipeline tests (Data -> Analysis -> Model -> DAX -> PBIP).

Data -> Analysis -> Model runs for real in ``test_analysis_e2e.py`` and
``test_modeling_e2e.py``. Here, each later
phase replaces the ``xfail`` below with real assertions on generated artifacts. Power BI MCP
interactions will be exercised against recorded/mock responses, never a real tenant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from powerbi_agent.core.agent import default_registry
from powerbi_agent.core.orchestrator import Orchestrator
from powerbi_agent.core.planner import RuleBasedPlanner
from powerbi_agent.models.tasks import Capability, TaskStatus
from powerbi_agent.utils.config import Settings

pytestmark = pytest.mark.integration


def test_full_request_is_planned_end_to_end() -> None:
    plan = RuleBasedPlanner().plan(
        "Analyze the sales.xlsx file and create a Power BI sales analytics dashboard."
    )
    capabilities = [s.capability for s in plan.steps]
    assert capabilities[0] is Capability.LOAD_DATA
    assert Capability.GENERATE_PBIP in capabilities
    assert capabilities[-1] is Capability.VALIDATE_REPORT
    assert not plan.requires_confirmation  # generating local artifacts needs no approval


@pytest.mark.xfail(reason="DAX and PBIP agents arrive in Phases 4-6", strict=True)
def test_pipeline_produces_pbip(settings: Settings, sales_csv: Path, tmp_path: Path) -> None:
    orch = Orchestrator(default_registry(), settings)
    task = orch.run(f"generate the semantic model from {sales_csv}", tmp_path)
    assert task.status is TaskStatus.SUCCEEDED
