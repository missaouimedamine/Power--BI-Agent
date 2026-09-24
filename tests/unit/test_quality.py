from __future__ import annotations

from pathlib import Path

import polars as pl

from powerbi_agent.data.loaders import LoadedDataset
from powerbi_agent.data.profiler import profile_dataset
from powerbi_agent.data.quality import check_quality
from powerbi_agent.data.schema import build_schema
from powerbi_agent.models.analysis import QualityIssueKind, QualityReport, Severity
from powerbi_agent.models.datasource import DataSource


def _report(frame: pl.DataFrame) -> QualityReport:
    ds = LoadedDataset(source=DataSource.from_path(Path("t.csv")), frame=frame)
    profile = profile_dataset(ds)
    return check_quality(frame, profile, build_schema(profile))


def _issues(report: QualityReport, kind: QualityIssueKind) -> list:  # type: ignore[type-arg]
    return [i for i in report.issues if i.kind is kind]


def test_missing_key_is_error_missing_attribute_is_warning() -> None:
    frame = pl.DataFrame(
        {
            "CustomerID": ["C1", None, "C2", "C1"],
            "Region": ["N", "S", None, "N"],
        }
    )
    report = _report(frame)
    missing = {i.column: i for i in _issues(report, QualityIssueKind.MISSING_VALUES)}
    assert missing["CustomerID"].severity is Severity.ERROR
    assert missing["CustomerID"].affected_rows == 1
    assert missing["Region"].severity is Severity.WARNING
    assert not report.passed


def test_duplicate_rows_with_examples() -> None:
    frame = pl.DataFrame({"a": [1, 1, 1, 2], "b": ["x", "x", "x", "y"]})
    (issue,) = _issues(_report(frame), QualityIssueKind.DUPLICATE_ROWS)
    assert issue.affected_rows == 2
    assert issue.examples == [{"a": 1, "b": "x"}]


def test_inconsistent_categories_counts_non_canonical_rows() -> None:
    frame = pl.DataFrame(
        {"Category": ["Bikes"] * 10 + ["bikes", " Bikes ", "BIKES"] + ["Clothing"] * 5}
    )
    (issue,) = _issues(_report(frame), QualityIssueKind.INCONSISTENT_CATEGORIES)
    assert issue.affected_rows == 3
    assert issue.examples[0]["canonical"] == "Bikes"
    assert sorted(issue.examples[0]["variants"]) == sorted(["bikes", " Bikes ", "BIKES"])


def test_outliers_are_info_and_not_removed() -> None:
    values = [10.0 + (i % 5) for i in range(40)] + [10_000.0]
    frame = pl.DataFrame({"Amount": values})
    (issue,) = _issues(_report(frame), QualityIssueKind.OUTLIERS)
    assert issue.severity is Severity.INFO
    assert issue.affected_rows == 1
    assert issue.examples == [10_000.0]
    assert frame.height == 41


def test_negative_values_flagged() -> None:
    frame = pl.DataFrame({"Quantity": [1, 2, -1, 3]})
    (issue,) = _issues(_report(frame), QualityIssueKind.NEGATIVE_VALUES)
    assert issue.affected_rows == 1


def test_numbers_stored_as_text_with_invalid_values() -> None:
    values = [f"{i}.50" for i in range(40)] + ["n/a"]
    frame = pl.DataFrame({"Amount": values})
    (issue,) = _issues(_report(frame), QualityIssueKind.TYPE_MISMATCH)
    assert "numbers" in issue.description
    assert issue.affected_rows == 1
    assert issue.examples == ["n/a"]


def test_dates_stored_as_text() -> None:
    frame = pl.DataFrame({"Shipped": [f"2025-01-{d:02d}" for d in range(1, 29)]})
    (issue,) = _issues(_report(frame), QualityIssueKind.TYPE_MISMATCH)
    assert "dates" in issue.description


def test_blank_strings_and_constant_columns() -> None:
    frame = pl.DataFrame({"Note": ["a", "  ", "b"], "Flag": ["Y", "Y", "Y"]})
    report = _report(frame)
    (blank,) = _issues(report, QualityIssueKind.BLANK_STRINGS)
    assert blank.affected_rows == 1
    (const,) = _issues(report, QualityIssueKind.CONSTANT_COLUMN)
    assert const.column == "Flag"


def test_clean_data_passes_and_records_checks() -> None:
    frame = pl.DataFrame({"Region": ["N", "S"], "Revenue": [1.0, 2.0]})
    report = _report(frame)
    assert report.passed
    assert report.issues == []
    assert "missing_values" in report.checks_run
    assert report.rows_checked == 2


def test_prompt_injection_text_is_just_a_value(sales_csv: Path) -> None:
    from powerbi_agent.data.loaders import load_source

    ds = load_source(DataSource.from_path(sales_csv))
    profile = profile_dataset(ds)
    report = check_quality(ds.frame, profile, build_schema(profile))
    # It's profiled like any other string; nothing about the run changes.
    name = profile.column("CustomerName")
    assert name is not None and name.distinct_count == 4
    assert any("Ignore previous instructions" in str(v) for v, _ in name.top_values)
    assert report.rows_checked == 6
