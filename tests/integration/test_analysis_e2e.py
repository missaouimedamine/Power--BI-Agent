"""Data -> Analysis, end to end, against sample data with planted defects."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi_agent.core.agent import default_registry
from powerbi_agent.core.orchestrator import Orchestrator
from powerbi_agent.data.samples import INJECTION_TEXT, generate_sales, write_xlsx
from powerbi_agent.models.analysis import QualityIssueKind, QualityReport
from powerbi_agent.models.tasks import Capability, TaskStatus
from powerbi_agent.utils.config import Settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def workbook(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, int]]:
    frame, defects = generate_sales(800, seed=7, missing_customer_ids=12, duplicate_rows=5)
    path = tmp_path_factory.mktemp("data") / "sales.xlsx"
    write_xlsx(frame, path)
    return path, defects.to_dict()


def _analyze(settings: Settings, data: Path, ws: Path) -> TaskStatus:
    task = Orchestrator(default_registry(), settings).run(f"analyze {data}", ws)
    return task.status


def test_analysis_finds_exactly_the_planted_defects(
    settings: Settings, workbook: tuple[Path, dict[str, int]], tmp_path: Path
) -> None:
    path, defects = workbook
    ws = tmp_path / "ws"
    assert _analyze(settings, path, ws) is TaskStatus.SUCCEEDED

    analysis = ws / "analysis"
    for name in ["profile.json", "quality_report.json", "schema.json", "metrics.json"]:
        assert (analysis / name).is_file(), name
    assert (analysis / "insights.md").is_file()

    profile = json.loads((analysis / "profile.json").read_text(encoding="utf-8"))
    assert profile["rows"] == defects["total_rows"]
    assert profile["duplicate_rows"] == defects["duplicate_rows"]

    quality = QualityReport.model_validate_json(
        (analysis / "quality_report.json").read_text(encoding="utf-8")
    )
    by_kind = {(i.kind, i.column): i.affected_rows for i in quality.issues}
    assert (
        by_kind[(QualityIssueKind.MISSING_VALUES, "CustomerID")]
        == (defects["missing_customer_ids"])
    )
    assert by_kind[(QualityIssueKind.DUPLICATE_ROWS, None)] == defects["duplicate_rows"]
    assert (
        by_kind[(QualityIssueKind.INCONSISTENT_CATEGORIES, "Category")]
        == (defects["inconsistent_category_rows"])
    )
    assert not quality.passed  # missing foreign keys are errors

    schema = json.loads((analysis / "schema.json").read_text(encoding="utf-8"))
    roles = {c["name"]: c["role"] for c in schema["columns"]}
    assert roles["OrderDate"] == "date"
    assert roles["Revenue"] == "measure"
    assert roles["CustomerID"] == "foreign_key"

    metrics = json.loads((analysis / "metrics.json").read_text(encoding="utf-8"))
    kpis = {k["name"] for k in metrics["suggested_kpis"]}
    assert {"Total Revenue", "Total Profit", "Profit Margin", "Orders"} <= kpis

    validation = json.loads((ws / "validation" / "data_validation.json").read_text())
    assert validation["passed"] is True


def test_injection_text_is_never_acted_on(
    settings: Settings, workbook: tuple[Path, dict[str, int]], tmp_path: Path
) -> None:
    path, _ = workbook
    ws = tmp_path / "ws"
    task = Orchestrator(default_registry(), settings).run(f"analyze {path}", ws)
    # Nothing beyond the planned read/local-write steps ran, nothing asked to publish.
    assert [r.capability for r in task.results] == [
        Capability.LOAD_DATA,
        Capability.PROFILE_DATA,
        Capability.CHECK_QUALITY,
        Capability.GENERATE_INSIGHTS,
        Capability.VALIDATE_DATA,
    ]
    assert task.status is TaskStatus.SUCCEEDED
    # The text was profiled as an ordinary value of CustomerName...
    profile = json.loads((ws / "analysis" / "profile.json").read_text(encoding="utf-8"))
    names = next(c for c in profile["column_profiles"] if c["name"] == "CustomerName")
    assert names["logical_type"] == "string"
    # ...and it appears in insights.md only inside a quoted code span, if at all.
    md = (ws / "analysis" / "insights.md").read_text(encoding="utf-8")
    assert f"`{INJECTION_TEXT}`" in md or INJECTION_TEXT not in md


def test_rerun_archives_previous_outputs(
    settings: Settings, workbook: tuple[Path, dict[str, int]], tmp_path: Path
) -> None:
    path, _ = workbook
    ws = tmp_path / "ws"
    assert _analyze(settings, path, ws) is TaskStatus.SUCCEEDED
    assert _analyze(settings, path, ws) is TaskStatus.SUCCEEDED
    history = list((ws / ".agent" / "history").glob("*/analysis/profile.json"))
    assert len(history) == 1
    # Same input, same output: every analysis file is byte-identical across runs.
    previous = history[0].parent
    for name in [
        "profile.json",
        "schema.json",
        "quality_report.json",
        "metrics.json",
        "insights.md",
    ]:
        assert (previous / name).read_bytes() == (ws / "analysis" / name).read_bytes(), name
