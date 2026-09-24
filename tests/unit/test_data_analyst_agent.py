from __future__ import annotations

from pathlib import Path

from powerbi_agent.agents import DataAnalystAgent
from powerbi_agent.core.agent import AgentContext
from powerbi_agent.core.planner import build_plan
from powerbi_agent.models.datasource import DataSource
from powerbi_agent.models.tasks import Intent, TaskStatus
from powerbi_agent.utils.config import Settings


def _ctx(settings: Settings, tmp_path: Path) -> AgentContext:
    return AgentContext(task_id="t", settings=settings, workspace_dir=tmp_path / "ws")


def test_load_without_source_fails_with_clarification(settings: Settings, tmp_path: Path) -> None:
    step = build_plan("x", Intent.ANALYZE, []).steps[0]
    result = DataAnalystAgent().run(step, _ctx(settings, tmp_path))
    assert result.status is TaskStatus.FAILED
    assert "ask the user" in (result.error or "")


def test_multiple_sources_rejected(settings: Settings, tmp_path: Path) -> None:
    sources = [DataSource.from_path("a.csv"), DataSource.from_path("b.csv")]
    step = build_plan("x", Intent.ANALYZE, sources).steps[0]
    result = DataAnalystAgent().run(step, _ctx(settings, tmp_path))
    assert result.status is TaskStatus.FAILED


def test_step_out_of_order_fails_cleanly(settings: Settings, tmp_path: Path) -> None:
    profile_step = build_plan("x", Intent.ANALYZE, []).steps[1]
    result = DataAnalystAgent().run(profile_step, _ctx(settings, tmp_path))
    assert result.status is TaskStatus.FAILED
    assert "earlier steps" in (result.error or "")


def test_empty_file_fails(settings: Settings, tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("a,b\n", encoding="utf-8")
    step = build_plan("x", Intent.ANALYZE, [DataSource.from_path(path)]).steps[0]
    result = DataAnalystAgent().run(step, _ctx(settings, tmp_path))
    assert result.status is TaskStatus.FAILED
    assert "no rows" in (result.error or "")


def test_steps_report_artifacts_that_exist(
    settings: Settings, sales_csv: Path, tmp_path: Path
) -> None:
    agent = DataAnalystAgent()
    ctx = _ctx(settings, tmp_path)
    plan = build_plan("x", Intent.ANALYZE, [DataSource.from_path(sales_csv)])
    results = [agent.run(step, ctx) for step in plan.steps[:4]]
    assert [r.status for r in results] == [TaskStatus.SUCCEEDED] * 4
    for r in results[1:]:
        assert r.artifacts
        assert all((ctx.workspace_dir / a).is_file() for a in r.artifacts)
    assert results[0].output["rows"] == 6
    assert results[2].output["errors"] == 1  # missing CustomerID
