"""Data profiling, quality and insight results (the ``analysis/`` output contract)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, NonNegativeInt, computed_field

from powerbi_agent.models.datasource import DataSource


class LogicalType(StrEnum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    STRING = "string"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    UNKNOWN = "unknown"

    @property
    def is_numeric(self) -> bool:
        return self in {LogicalType.INTEGER, LogicalType.DECIMAL}

    @property
    def is_temporal(self) -> bool:
        return self in {LogicalType.DATE, LogicalType.DATETIME}


class ColumnRole(StrEnum):
    """Analytical role a column is likely to play in a semantic model."""

    KEY = "key"  # unique in this dataset
    FOREIGN_KEY = "foreign_key"  # id-like, repeats: references an entity
    DIMENSION_ATTRIBUTE = "dimension_attribute"
    MEASURE = "measure"
    DATE = "date"
    IDENTIFIER = "identifier"  # high-cardinality text, e.g. free text or unique labels
    UNKNOWN = "unknown"


class ColumnProfile(BaseModel):
    name: str
    physical_type: str
    logical_type: LogicalType
    count: NonNegativeInt
    null_count: NonNegativeInt
    distinct_count: NonNegativeInt
    min: Any | None = None
    max: Any | None = None
    mean: float | None = None
    std: float | None = None
    median: float | None = None
    top_values: list[tuple[Any, int]] = Field(default_factory=list)
    suggested_role: ColumnRole = ColumnRole.UNKNOWN

    @computed_field  # type: ignore[prop-decorator]
    @property
    def null_ratio(self) -> float:
        return self.null_count / self.count if self.count else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def distinct_ratio(self) -> float:
        non_null = self.count - self.null_count
        return self.distinct_count / non_null if non_null else 0.0


class DatasetProfile(BaseModel):
    """Written to ``analysis/profile.json``."""

    source: DataSource
    rows: NonNegativeInt
    columns: NonNegativeInt
    missing_values: NonNegativeInt = Field(description="Total null cells across all columns.")
    duplicate_rows: NonNegativeInt = Field(description="Rows that exactly repeat an earlier row.")
    column_profiles: list[ColumnProfile] = Field(default_factory=list)

    def column(self, name: str) -> ColumnProfile | None:
        return next((c for c in self.column_profiles if c.name == name), None)


class ColumnSchema(BaseModel):
    name: str
    physical_type: str
    logical_type: LogicalType
    nullable: bool
    role: ColumnRole
    reason: str = Field(description="Why this role was suggested (deterministic rule).")
    additive: bool | None = Field(
        default=None,
        description="Measures only: True if summing is meaningful (amounts, quantities), "
        "False for prices, rates and ratios (average instead).",
    )


class DatasetSchema(BaseModel):
    """Written to ``analysis/schema.json``."""

    dataset: str
    columns: list[ColumnSchema] = Field(default_factory=list)

    def by_role(self, *roles: ColumnRole) -> list[ColumnSchema]:
        return [c for c in self.columns if c.role in roles]


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class QualityIssueKind(StrEnum):
    MISSING_VALUES = "missing_values"
    BLANK_STRINGS = "blank_strings"
    DUPLICATE_ROWS = "duplicate_rows"
    DUPLICATE_KEYS = "duplicate_keys"
    OUTLIERS = "outliers"
    INVALID_VALUES = "invalid_values"
    INCONSISTENT_CATEGORIES = "inconsistent_categories"
    NEGATIVE_VALUES = "negative_values"
    TYPE_MISMATCH = "type_mismatch"
    CONSTANT_COLUMN = "constant_column"
    ORPHAN_KEYS = "orphan_keys"


class QualityIssue(BaseModel):
    kind: QualityIssueKind
    severity: Severity
    column: str | None = None
    affected_rows: NonNegativeInt = 0
    description: str
    examples: list[Any] = Field(default_factory=list, max_length=10)


class QualityReport(BaseModel):
    """Written to ``analysis/quality_report.json``."""

    dataset: str
    rows_checked: NonNegativeInt = 0
    checks_run: list[str] = Field(default_factory=list)
    issues: list[QualityIssue] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return not any(i.severity is Severity.ERROR for i in self.issues)

    def count(self, severity: Severity) -> int:
        return sum(1 for i in self.issues if i.severity is severity)


class SuggestedKpi(BaseModel):
    name: str
    definition: str = Field(description="Plain-language definition.")
    based_on: list[str] = Field(description="Source columns.")
    aggregation: str = Field(description="sum | distinct_count | ratio | difference | average")
    format_hint: str = Field(default="number", description="number | integer | percent")
    value: float | None = Field(
        default=None, description="Value over the full dataset, computed by the analyzer."
    )


class DatasetMetrics(BaseModel):
    """Written to ``analysis/metrics.json``. Values are ground truth for later DAX checks."""

    dataset: str
    rows: NonNegativeInt
    measures: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    date_ranges: dict[str, tuple[str, str]] = Field(default_factory=dict)
    suggested_kpis: list[SuggestedKpi] = Field(default_factory=list)


class Insight(BaseModel):
    title: str
    detail: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class AnalysisResult(BaseModel):
    """Everything produced by one ``analyze`` run."""

    profile: DatasetProfile
    dataset_schema: DatasetSchema
    quality: QualityReport
    metrics: DatasetMetrics
    candidate_dimensions: list[str] = Field(default_factory=list)
    candidate_measures: list[str] = Field(default_factory=list)
    insights: list[Insight] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
