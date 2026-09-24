from __future__ import annotations

import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from powerbi_agent.agents import DataAnalystAgent
from powerbi_agent.core.agent import AgentContext
from powerbi_agent.core.planner import build_plan
from powerbi_agent.models.datasource import DataSource
from powerbi_agent.models.tasks import Intent, TaskStatus
from powerbi_agent.models.validation import CheckStatus
from powerbi_agent.utils.config import Settings
from powerbi_agent.validation.data_validator import check_referential_integrity, validate_analysis


@pytest.fixture
def analysed(settings: Settings, sales_csv: Path, tmp_path: Path) -> Path:
    """Workspace with a full analysis of a copy of the fixture CSV."""
    data = tmp_path / "sales.csv"
    shutil.copy(sales_csv, data)
    ws = tmp_path / "ws"
    agent = DataAnalystAgent()
    ctx = AgentContext(task_id="t1", settings=settings, workspace_dir=ws)
    plan = build_plan("x", Intent.ANALYZE, [DataSource.from_path(data)])
    for step in plan.steps[:4]:
        assert agent.run(step, ctx).status is TaskStatus.SUCCEEDED
    return ws


def _statuses(ws: Path) -> dict[str, CheckStatus]:
    return {f"{f.check}:{f.object}": f.status for f in validate_analysis(ws).findings}


def test_valid_analysis_passes(analysed: Path) -> None:
    report = validate_analysis(analysed)
    assert report.passed
    assert all(f.status is CheckStatus.PASSED for f in report.findings)
    assert any(f.check == "row_count_matches_source" for f in report.findings)


def test_missing_required_file_fails(analysed: Path) -> None:
    (analysed / "analysis" / "schema.json").unlink()
    report = validate_analysis(analysed)
    assert not report.passed
    assert _statuses(analysed)["file_present:analysis/schema.json"] is CheckStatus.FAILED


def test_optional_file_missing_is_not_run(analysed: Path) -> None:
    (analysed / "analysis" / "metrics.json").unlink()
    report = validate_analysis(analysed)
    assert report.passed
    assert _statuses(analysed)["file_present:analysis/metrics.json"] is CheckStatus.NOT_RUN


def test_tampered_profile_is_caught(analysed: Path) -> None:
    path = analysed / "analysis" / "profile.json"
    data = json.loads(path.read_text())
    data["missing_values"] = 999
    path.write_text(json.dumps(data))
    assert not validate_analysis(analysed).passed


def test_corrupt_json_is_caught(analysed: Path) -> None:
    (analysed / "analysis" / "profile.json").write_text("{not json")
    assert not validate_analysis(analysed).passed


def test_changed_source_is_caught(analysed: Path, tmp_path: Path) -> None:
    with (tmp_path / "sales.csv").open("a", encoding="utf-8") as fh:
        fh.write("1006,2025-03-01,C004,New,North,P01,Bikes,1,100.00,50.00\n")
    report = validate_analysis(analysed)
    assert not report.passed
    assert any(
        f.check == "row_count_matches_source" and f.status is CheckStatus.FAILED
        for f in report.findings
    )


def test_missing_source_is_not_run_not_failed(analysed: Path, tmp_path: Path) -> None:
    (tmp_path / "sales.csv").unlink()
    report = validate_analysis(analysed)
    assert report.passed
    finding = next(f for f in report.findings if f.check == "row_count_matches_source")
    assert finding.status is CheckStatus.NOT_RUN


def test_metrics_are_cross_checked_with_sql(analysed: Path) -> None:
    report = validate_analysis(analysed)
    sql_checks = [f for f in report.findings if f.check == "metrics_match_sql"]
    assert {f.object for f in sql_checks} >= {"analysis/metrics.json:Revenue"}
    assert all(f.status is CheckStatus.PASSED for f in sql_checks)


def test_tampered_metric_is_caught_by_sql(analysed: Path) -> None:
    path = analysed / "analysis" / "metrics.json"
    data = json.loads(path.read_text())
    data["measures"]["Revenue"]["sum"] += 1.0
    path.write_text(json.dumps(data))
    report = validate_analysis(analysed)
    assert not report.passed
    failed = [f for f in report.findings if f.status is CheckStatus.FAILED]
    assert [f.object for f in failed] == ["analysis/metrics.json:Revenue"]


def test_referential_integrity() -> None:
    fact = pl.DataFrame({"CustomerID": ["C1", "C2", "C9", None]})
    dim = pl.DataFrame({"CustomerID": ["C1", "C2"]})
    finding = check_referential_integrity(fact, "CustomerID", dim, "CustomerID")
    assert finding.status is CheckStatus.FAILED
    assert "C9" in finding.message
    ok = check_referential_integrity(fact.head(2), "CustomerID", dim, "CustomerID")
    assert ok.status is CheckStatus.PASSED
