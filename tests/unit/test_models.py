from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from powerbi_agent.models import (
    ColumnProfile,
    DataSource,
    DataSourceType,
    FieldRef,
    LogicalType,
    MeasureSpec,
    QualityIssue,
    QualityIssueKind,
    QualityReport,
    ReportSpec,
    SemanticModelSpec,
    Severity,
    VisualSpec,
)


# --- data sources ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("sales.csv", DataSourceType.CSV),
        ("sales.XLSX", DataSourceType.EXCEL),
        ("sales.parquet", DataSourceType.PARQUET),
    ],
)
def test_datasource_from_path(name: str, kind: DataSourceType) -> None:
    src = DataSource.from_path(Path("data") / name)
    assert src.type is kind
    assert src.name == "sales"


def test_datasource_rejects_unknown_extension() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        DataSource.from_path("sales.json")


def test_sql_source_requires_env_reference_not_credentials() -> None:
    with pytest.raises(ValidationError):
        DataSource(name="db", type=DataSourceType.SQL, query="select 1")
    src = DataSource(name="db", type=DataSourceType.SQL, query="select 1", connection_env="DB_URL")
    assert src.connection_env == "DB_URL"


def test_sheet_only_for_excel() -> None:
    with pytest.raises(ValidationError):
        DataSource(name="x", type=DataSourceType.CSV, path=Path("x.csv"), sheet="S1")


# --- analysis -------------------------------------------------------------------------------
def test_column_profile_null_ratio() -> None:
    col = ColumnProfile(
        name="CustomerID",
        physical_type="Utf8",
        logical_type=LogicalType.STRING,
        count=4,
        null_count=1,
        distinct_count=3,
    )
    assert col.null_ratio == 0.25
    assert col.model_dump()["null_ratio"] == 0.25


def test_quality_report_passed_only_without_errors() -> None:
    warn = QualityIssue(
        kind=QualityIssueKind.DUPLICATE_ROWS, severity=Severity.WARNING, description="dupes"
    )
    error = QualityIssue(
        kind=QualityIssueKind.ORPHAN_KEYS, severity=Severity.ERROR, description="orphans"
    )
    assert QualityReport(dataset="s", issues=[warn]).passed
    assert not QualityReport(dataset="s", issues=[warn, error]).passed


# --- semantic model -------------------------------------------------------------------------
def _model_dict() -> dict[str, Any]:
    return {
        "model": {"name": "Sales Analytics"},
        "tables": [
            {
                "name": "FactSales",
                "kind": "fact",
                "columns": [
                    {"name": "CustomerKey", "data_type": "int64", "is_hidden": True},
                    {"name": "Revenue", "data_type": "decimal", "summarize_by": "sum"},
                    {"name": "Profit", "data_type": "decimal"},
                ],
            },
            {
                "name": "DimCustomer",
                "kind": "dimension",
                "columns": [
                    {"name": "CustomerKey", "data_type": "int64", "is_key": True},
                    {"name": "Country", "data_type": "string"},
                    {"name": "City", "data_type": "string"},
                ],
                "hierarchies": [{"name": "Geography", "levels": ["Country", "City"]}],
            },
        ],
        "relationships": [
            {
                "from_table": "FactSales",
                "from_column": "CustomerKey",
                "to_table": "DimCustomer",
                "to_column": "CustomerKey",
            }
        ],
        "measures": [
            {
                "name": "Total Revenue",
                "table": "FactSales",
                "expression": "SUM(FactSales[Revenue])",
            },
            {
                "name": "Profit Margin",
                "table": "FactSales",
                "expression": "DIVIDE([Total Profit], [Total Revenue])",
                "format_string": "0.0%",
            },
        ],
    }


def test_valid_semantic_model_round_trips() -> None:
    spec = SemanticModelSpec.model_validate(_model_dict())
    assert spec.table("factsales") is not None  # case-insensitive lookup
    assert spec.measure("profit margin") is not None
    assert SemanticModelSpec.model_validate_json(spec.model_dump_json()) == spec


def test_relationship_to_unknown_column_rejected() -> None:
    d = _model_dict()
    d["relationships"][0]["to_column"] = "Missing"
    with pytest.raises(ValidationError, match="unknown column"):
        SemanticModelSpec.model_validate(d)


def test_duplicate_measure_names_rejected_case_insensitively() -> None:
    d = _model_dict()
    d["measures"].append({"name": "total revenue", "table": "FactSales", "expression": "1"})
    with pytest.raises(ValidationError, match="duplicate measure"):
        SemanticModelSpec.model_validate(d)


def test_measure_cannot_shadow_column() -> None:
    d = _model_dict()
    d["measures"].append({"name": "Revenue", "table": "FactSales", "expression": "1"})
    with pytest.raises(ValidationError, match="collides"):
        SemanticModelSpec.model_validate(d)


def test_hierarchy_levels_must_exist() -> None:
    d = _model_dict()
    d["tables"][1]["hierarchies"][0]["levels"].append("Street")
    with pytest.raises(ValidationError, match="unknown columns"):
        SemanticModelSpec.model_validate(d)


def test_measure_name_cannot_contain_brackets() -> None:
    with pytest.raises(ValidationError):
        MeasureSpec(name="Bad[Name]", table="T", expression="1")


# --- report ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "table", "name"),
    [
        ("DimDate[Month]", "DimDate", "Month"),
        ("'Dim Date'[Month Name]", "Dim Date", "Month Name"),
        ("[Total Revenue]", None, "Total Revenue"),
    ],
)
def test_field_ref_parse(text: str, table: str | None, name: str) -> None:
    ref = FieldRef.parse(text)
    assert (ref.table, ref.name) == (table, name)


@pytest.mark.parametrize("bad", ["Total Revenue", "Table[", "[a][b]"])
def test_field_ref_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        FieldRef.parse(bad)


def test_line_visual_requires_axis_and_value() -> None:
    with pytest.raises(ValidationError):
        VisualSpec(id="v1", type="line", values=["[Total Revenue]"])  # type: ignore[arg-type]


def test_report_spec_collects_all_field_refs() -> None:
    report = ReportSpec.model_validate(
        {
            "name": "Sales Analytics",
            "semantic_model": "Sales",
            "pages": [
                {
                    "name": "Executive Overview",
                    "visuals": [
                        {"id": "kpi1", "type": "kpi", "values": "[Total Revenue]"},
                        {
                            "id": "line1",
                            "type": "line",
                            "category": "DimDate[Month]",
                            "values": ["[Total Revenue]"],
                        },
                    ],
                    "filters": [{"field": "DimRegion[Region]"}],
                }
            ],
        }
    )
    refs = {str(r) for r in report.all_field_refs()}
    assert refs == {"[Total Revenue]", "DimDate[Month]", "DimRegion[Region]"}


def test_duplicate_visual_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate visual"):
        ReportSpec.model_validate(
            {
                "name": "R",
                "semantic_model": "M",
                "pages": [
                    {
                        "name": "P",
                        "visuals": [
                            {"id": "a", "type": "card", "values": "[X]"},
                            {"id": "a", "type": "card", "values": "[Y]"},
                        ],
                    }
                ],
            }
        )
