from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from powerbi_agent import __version__
from powerbi_agent.cli import EXIT_BAD_INPUT, EXIT_INCOMPLETE, EXIT_OK, app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_config_redacts_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERBI_CLIENT_SECRET", "do-not-print-me")
    result = runner.invoke(app, ["config", "--json"])
    assert result.exit_code == 0
    assert "do-not-print-me" not in result.stdout


def test_plan_command_prints_steps() -> None:
    result = runner.invoke(app, ["plan", "Analyze sales.xlsx and build a dashboard"])
    assert result.exit_code == 0
    assert "design_report" in result.stdout


def test_analyze_missing_file_is_input_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["analyze", str(tmp_path / "nope.csv")])
    assert result.exit_code == EXIT_BAD_INPUT


def test_analyze_writes_analysis_outputs(sales_csv: Path, tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    result = runner.invoke(app, ["analyze", str(sales_csv), "--workspace", str(ws), "--json"])
    assert result.exit_code == EXIT_OK, result.stdout
    task = json.loads(result.stdout)
    assert task["status"] == "succeeded"
    for name in ["profile.json", "quality_report.json", "schema.json", "insights.md"]:
        assert (ws / "analysis" / name).is_file()
    assert (ws / "validation" / "data_validation.json").is_file()
    assert (ws / ".agent" / "tasks" / f"{task['id']}.json").exists()


def test_analyze_prints_summary_from_tool_outputs(sales_csv: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["analyze", str(sales_csv), "-w", str(tmp_path / "ws")])
    assert result.exit_code == EXIT_OK
    assert "I found:" in result.stdout
    assert "duplicate rows: 1" in result.stdout


def test_design_model_writes_spec_and_tables(sales_csv: Path, tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    result = runner.invoke(app, ["design-model", str(sales_csv), "-w", str(ws), "--json"])
    assert result.exit_code == EXIT_OK, result.stdout
    task = json.loads(result.stdout)
    statuses = {r["capability"]: r["status"] for r in task["results"]}
    assert statuses["design_star_schema"] == statuses["validate_model"] == "succeeded"
    for name in ["semantic_model.json", "model_design.json", "model_design.md"]:
        assert (ws / "specs" / name).is_file()
    assert (ws / "model_data" / "FactSalesSmall.parquet").is_file()
    assert (ws / "validation" / "model_validation.json").is_file()


def test_design_model_summary_promises_no_power_bi_changes(sales_csv: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["design-model", str(sales_csv), "-w", str(tmp_path / "ws")])
    assert result.exit_code == EXIT_OK
    assert "Proposed model" in result.stdout
    assert "Nothing has been written to Power BI" in result.stdout


def test_generate_model_reports_later_phases_honestly(sales_csv: Path, tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    result = runner.invoke(app, ["generate-model", str(sales_csv), "-w", str(ws), "--json"])
    assert result.exit_code == EXIT_INCOMPLETE
    task = json.loads(result.stdout)
    assert task["status"] == "not_implemented"
    statuses = {r["capability"]: r["status"] for r in task["results"]}
    assert statuses["design_star_schema"] == "succeeded"
    assert statuses["generate_dax"] == "not_implemented"
    assert not list(ws.glob("*.pbip"))


def test_unsupported_file_is_input_error(tmp_path: Path) -> None:
    bad = tmp_path / "data.json"
    bad.write_text("{}")
    assert runner.invoke(app, ["analyze", str(bad)]).exit_code == EXIT_BAD_INPUT


def test_sql_options_require_both_parts(tmp_path: Path) -> None:
    result = runner.invoke(app, ["analyze", "--query", "select 1"])
    assert result.exit_code == EXIT_BAD_INPUT


def test_publish_is_disabled(tmp_path: Path) -> None:
    result = runner.invoke(app, ["publish", str(tmp_path)])
    assert result.exit_code == EXIT_INCOMPLETE
    assert "Nothing was sent" in result.stderr


def test_chat_exits_cleanly() -> None:
    result = runner.invoke(app, ["chat"], input="profile sales.csv\nexit\n")
    assert result.exit_code == 0
    assert "profile_data" in result.stdout
