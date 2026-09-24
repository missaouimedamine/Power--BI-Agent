"""Data -> Analysis -> Model, end to end, against sample data with planted defects."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from powerbi_agent.core.agent import default_registry
from powerbi_agent.core.orchestrator import Orchestrator
from powerbi_agent.data.samples import generate_sales, write_xlsx
from powerbi_agent.models.model_design import ModelDesign
from powerbi_agent.models.tasks import TaskStatus
from powerbi_agent.utils.config import Settings
from powerbi_agent.validation.model_validator import validate_model

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def workbook(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, int]]:
    frame, defects = generate_sales(1_200, seed=11, missing_customer_ids=12, duplicate_rows=5)
    path = tmp_path_factory.mktemp("data") / "sales.xlsx"
    write_xlsx(frame, path)
    return path, defects.to_dict()


def _design(settings: Settings, data: Path, ws: Path) -> TaskStatus:
    return (
        Orchestrator(default_registry(), settings)
        .run(f"design a star schema for {data}", ws)
        .status
    )


def test_star_schema_from_sample(
    settings: Settings, workbook: tuple[Path, dict[str, int]], tmp_path: Path
) -> None:
    path, defects = workbook
    ws = tmp_path / "ws"
    assert _design(settings, path, ws) is TaskStatus.SUCCEEDED

    design = ModelDesign.model_validate_json(
        (ws / "specs" / "model_design.json").read_text(encoding="utf-8")
    )
    assert design.fact_table == "FactSales"
    assert design.grain == ["OrderID", "OrderLine"] and not design.grain_unique
    assert {t.name for t in design.spec.tables} == {
        "FactSales",
        "DimCustomer",
        "DimProduct",
        "DimDate",
    }
    levels = {h.name: h.levels for t in design.spec.tables for h in t.hierarchies}
    assert levels["Category Hierarchy"] == ["Category", "Subcategory", "ProductName"]
    assert levels["Region Hierarchy"][:2] == ["Region", "Country"]

    findings = {(f.topic, f.column): f.affected_rows for f in design.findings}
    assert findings[("null_foreign_keys", "CustomerID")] == defects["missing_customer_ids"]
    assert findings[("attribute_conflicts", "Category")] == defects["inconsistent_category_rows"]
    assert findings[("grain", None)] == defects["duplicate_rows"]

    # Fact keeps every source row (duplicates are reported, not silently dropped).
    fact = pl.read_parquet(ws / "model_data" / "FactSales.parquet")
    assert fact.height == defects["total_rows"]
    # Planted category variants are resolved in the dimension, as reported.
    products = pl.read_parquet(ws / "model_data" / "DimProduct.parquet")
    assert set(products.get_column("Category")) == {
        "Bikes",
        "Accessories",
        "Clothing",
        "Components",
    }

    report = json.loads((ws / "validation" / "model_validation.json").read_text())
    assert report["passed"] is True
    assert validate_model(ws).passed


def test_redesign_reports_no_changes_and_archives(
    settings: Settings, workbook: tuple[Path, dict[str, int]], tmp_path: Path
) -> None:
    path, _ = workbook
    ws = tmp_path / "ws"
    assert _design(settings, path, ws) is TaskStatus.SUCCEEDED
    assert _design(settings, path, ws) is TaskStatus.SUCCEEDED
    design = json.loads((ws / "specs" / "model_design.json").read_text(encoding="utf-8"))
    assert all(not v for v in design["diff"].values())
    assert list((ws / ".agent" / "history").glob("*/specs/semantic_model.json"))
    assert list((ws / ".agent" / "history").glob("*/model_data/FactSales.parquet"))
