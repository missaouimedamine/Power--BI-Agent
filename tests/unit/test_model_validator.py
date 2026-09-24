from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from powerbi_agent.agents import DataAnalystAgent, PowerBIModelerAgent
from powerbi_agent.core.agent import AgentContext
from powerbi_agent.core.planner import build_plan
from powerbi_agent.models.analysis import Severity
from powerbi_agent.models.datasource import DataSource
from powerbi_agent.models.tasks import Intent, TaskStatus
from powerbi_agent.models.validation import CheckStatus, ValidationReport
from powerbi_agent.utils.config import Settings
from powerbi_agent.validation.model_validator import SPEC_PATH, validate_model


@pytest.fixture
def designed(settings: Settings, tmp_path: Path) -> Path:
    rows = [
        {
            "OrderID": f"SO{i // 2}",
            "OrderLine": i % 2 + 1,
            "OrderDate": date(2025, 1 + i % 12, 1 + i % 28),
            "CustomerID": f"C{(i // 2) % 5}",
            "Segment": ["A", "B"][((i // 2) % 5) % 2],
            "Revenue": float(i),
        }
        for i in range(60)
    ]
    data = tmp_path / "sales.parquet"
    pl.DataFrame(rows).write_parquet(data)
    ws = tmp_path / "ws"
    ctx = AgentContext(task_id="t1", settings=settings, workspace_dir=ws)
    plan = build_plan("x", Intent.DESIGN_MODEL, [DataSource.from_path(data)])
    analyst, modeler = DataAnalystAgent(), PowerBIModelerAgent()
    for step in plan.steps[:4]:
        assert analyst.run(step, ctx).status is TaskStatus.SUCCEEDED
    assert modeler.run(plan.steps[5], ctx).status is TaskStatus.SUCCEEDED
    return ws


def _failed(report: ValidationReport, severity: Severity = Severity.ERROR) -> set[str]:
    return {
        f.check
        for f in report.findings
        if f.status is CheckStatus.FAILED and f.severity is severity
    }


def _edit_spec(ws: Path, fn: object) -> None:
    path = ws / SPEC_PATH
    data = json.loads(path.read_text())
    fn(data)  # type: ignore[operator]
    path.write_text(json.dumps(data))


def test_generated_model_passes(designed: Path) -> None:
    report = validate_model(designed)
    assert report.passed, _failed(report)
    checks = {f.check for f in report.findings}
    assert {"no_orphan_keys", "key_unique_not_null", "date_table_contiguous"} <= checks
    assert "fact_rows_match_source" in checks


def test_missing_spec(tmp_path: Path) -> None:
    report = validate_model(tmp_path)
    assert not report.passed
    assert _failed(report) == {"spec_present"}


def test_corrupt_spec(designed: Path) -> None:
    _edit_spec(designed, lambda d: d["relationships"][0].update(to_column="Nope"))
    report = validate_model(designed)
    assert _failed(report) == {"spec_parses"}


def test_orphan_keys_detected(designed: Path) -> None:
    path = designed / "model_data" / "DimCustomer.parquet"
    pl.read_parquet(path).filter(pl.col("CustomerID") != "C0").write_parquet(path)
    report = validate_model(designed)
    assert "no_orphan_keys" in _failed(report)
    orphan = next(
        f for f in report.findings if f.check == "no_orphan_keys" and f.status is CheckStatus.FAILED
    )
    assert "C0" in orphan.message


def test_duplicate_dimension_key_detected(designed: Path) -> None:
    path = designed / "model_data" / "DimCustomer.parquet"
    dim = pl.read_parquet(path)
    pl.concat([dim, dim.head(1)]).write_parquet(path)
    failed = _failed(validate_model(designed))
    assert {"key_unique_not_null", "cardinality_matches_data"} <= failed


def test_type_mismatch_detected(designed: Path) -> None:
    path = designed / "model_data" / "DimCustomer.parquet"
    pl.read_parquet(path).with_columns(pl.col("Segment").cast(pl.Categorical)).write_parquet(path)
    assert validate_model(designed).passed  # categorical is still text
    _edit_spec(
        designed,
        lambda d: next(t for t in d["tables"] if t["name"] == "DimCustomer")["columns"][1].update(
            data_type="int64"
        ),
    )
    assert "columns_match_data" in _failed(validate_model(designed))


def test_date_gap_detected(designed: Path) -> None:
    path = designed / "model_data" / "DimDate.parquet"
    pl.read_parquet(path).filter(pl.col("Date") != date(2025, 6, 1)).write_parquet(path)
    assert "date_table_contiguous" in _failed(validate_model(designed))


def test_missing_table_data(designed: Path) -> None:
    (designed / "model_data" / "DimDate.parquet").unlink()
    assert "table_data_present" in _failed(validate_model(designed))


def test_fact_row_count_must_match_profile(designed: Path) -> None:
    path = designed / "model_data" / "FactSales.parquet"
    pl.read_parquet(path).head(10).write_parquet(path)
    assert "fact_rows_match_source" in _failed(validate_model(designed))


def test_ambiguous_paths_detected(designed: Path) -> None:
    def add_cycle(d: dict) -> None:  # type: ignore[type-arg]
        dim = next(t for t in d["tables"] if t["name"] == "DimCustomer")
        dim["columns"].append({"name": "Day", "data_type": "dateTime"})
        d["relationships"].append(
            {
                "from_table": "DimCustomer",
                "from_column": "Day",
                "to_table": "DimDate",
                "to_column": "Date",
            }
        )

    _edit_spec(designed, add_cycle)
    report = validate_model(designed, check_data=False)
    assert "no_ambiguous_paths" in _failed(report)
    assert "relationship_direction" in _failed(report, Severity.WARNING)


def test_naming_warnings_do_not_fail(designed: Path) -> None:
    def rename(d: dict) -> None:  # type: ignore[type-arg]
        for t in d["tables"]:
            if t["name"] == "DimCustomer":
                t["name"] = "Customers"
        for r in d["relationships"]:
            if r["to_table"] == "DimCustomer":
                r["to_table"] = "Customers"

    _edit_spec(designed, rename)
    report = validate_model(designed, check_data=False)
    assert report.passed
    assert "table_naming" in _failed(report, Severity.WARNING)
