from __future__ import annotations

import pytest

from powerbi_agent.core.planner import RuleBasedPlanner, detect_intent, extract_data_paths
from powerbi_agent.models.tasks import Capability, Intent, Risk


@pytest.mark.parametrize(
    ("request_text", "intent"),
    [
        ("Profile ./data/sales.xlsx", Intent.PROFILE),
        ("Analyze the sales.xlsx file", Intent.ANALYZE),
        (
            "Analyze the sales.xlsx file and create a Power BI sales analytics dashboard.",
            Intent.GENERATE_REPORT,
        ),
        ("Design a star schema for orders.csv", Intent.DESIGN_MODEL),
        ("Generate the PBIP semantic model from sales.csv", Intent.GENERATE_MODEL),
        ("Validate ./workspaces/sales", Intent.VALIDATE),
        ("Publish ./workspaces/sales to the workspace", Intent.PUBLISH),
        ("hello there", Intent.UNKNOWN),
    ],
)
def test_detect_intent(request_text: str, intent: Intent) -> None:
    assert detect_intent(request_text) is intent


def test_extract_data_paths_dedupes_and_keeps_order() -> None:
    text = "Compare ./data/sales.xlsx with C:\\data\\targets.csv and ./data/sales.xlsx"
    assert extract_data_paths(text) == ["./data/sales.xlsx", "C:\\data\\targets.csv"]


def test_analysis_plan_is_read_only_and_sequential() -> None:
    plan = RuleBasedPlanner().plan("Analyze sales.xlsx")
    assert [s.capability for s in plan.steps] == [
        Capability.LOAD_DATA,
        Capability.PROFILE_DATA,
        Capability.CHECK_QUALITY,
        Capability.GENERATE_INSIGHTS,
        Capability.VALIDATE_DATA,
    ]
    # Loading is read-only; later steps only write new files into the workspace.
    assert plan.steps[0].risk is Risk.READ_ONLY
    assert {s.risk for s in plan.steps[1:]} == {Risk.LOCAL_WRITE}
    assert not plan.requires_confirmation
    source = plan.steps[0].inputs["sources"][0]
    assert (source["type"], source["name"]) == ("excel", "sales")
    for prev, step in zip(plan.steps, plan.steps[1:], strict=False):
        assert step.depends_on == [prev.id]


def test_profile_plan_validates_its_outputs() -> None:
    plan = RuleBasedPlanner().plan("profile data/orders.csv")
    assert [s.capability for s in plan.steps] == [
        Capability.LOAD_DATA,
        Capability.PROFILE_DATA,
        Capability.VALIDATE_DATA,
    ]


def test_multiple_files_add_note() -> None:
    plan = RuleBasedPlanner().plan("analyze a.csv and b.csv")
    assert len(plan.steps[0].inputs["sources"]) == 2
    assert any("one at a time" in n for n in plan.notes)


def test_publish_plan_requires_confirmation() -> None:
    plan = RuleBasedPlanner().plan("publish workspaces/sales")
    assert plan.requires_confirmation
    assert plan.steps[-1].capability is Capability.PUBLISH
    assert plan.steps[-1].risk is Risk.EXTERNAL


def test_missing_data_source_adds_clarification_note() -> None:
    plan = RuleBasedPlanner().plan("analyze my sales")
    assert any("data source" in n for n in plan.notes)


def test_unknown_intent_produces_empty_plan_with_note() -> None:
    plan = RuleBasedPlanner().plan("what's up")
    assert plan.steps == []
    assert any("clarify" in n for n in plan.notes)
