"""Intermediate representation of a Power BI semantic model.

Business reasoning produces a :class:`SemanticModelSpec`; renderers (TMDL/PBIP, MCP)
turn it into a Power BI implementation. The spec is validated structurally here;
data-dependent checks (orphan keys, cardinality) live in ``powerbi_agent.validation``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from powerbi_agent.models.dax import MeasureSpec


class TableKind(StrEnum):
    FACT = "fact"
    DIMENSION = "dimension"
    DATE = "date"
    BRIDGE = "bridge"
    PARAMETER = "parameter"


class DataType(StrEnum):
    """TMDL column data types."""

    STRING = "string"
    INT64 = "int64"
    DOUBLE = "double"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATETIME = "dateTime"


class SummarizeBy(StrEnum):
    NONE = "none"
    SUM = "sum"
    COUNT = "count"
    MIN = "min"
    MAX = "max"
    AVERAGE = "average"


class Cardinality(StrEnum):
    MANY_TO_ONE = "many_to_one"
    ONE_TO_ONE = "one_to_one"
    MANY_TO_MANY = "many_to_many"


class CrossFilter(StrEnum):
    SINGLE = "single"
    BOTH = "both"


class StorageMode(StrEnum):
    IMPORT = "import"
    DIRECT_QUERY = "directQuery"
    DIRECT_LAKE = "directLake"


class ColumnSpec(BaseModel):
    name: str = Field(min_length=1)
    data_type: DataType
    source_column: str | None = None
    is_key: bool = False
    is_hidden: bool = False
    format_string: str | None = None
    summarize_by: SummarizeBy = SummarizeBy.NONE
    sort_by_column: str | None = None
    description: str = ""


class HierarchySpec(BaseModel):
    name: str
    levels: list[str] = Field(min_length=1, description="Column names, top level first.")


class TableSpec(BaseModel):
    name: str = Field(min_length=1)
    kind: TableKind
    columns: list[ColumnSpec] = Field(default_factory=list)
    hierarchies: list[HierarchySpec] = Field(default_factory=list)
    source: str | None = Field(default=None, description="DataSource name feeding this table.")
    description: str = ""
    is_hidden: bool = False

    def column(self, name: str) -> ColumnSpec | None:
        return next((c for c in self.columns if c.name.lower() == name.lower()), None)

    @model_validator(mode="after")
    def _check_columns(self) -> TableSpec:
        _assert_unique([c.name for c in self.columns], f"column in table '{self.name}'")
        names = {c.name.lower() for c in self.columns}
        for h in self.hierarchies:
            missing = [lvl for lvl in h.levels if lvl.lower() not in names]
            if missing:
                raise ValueError(f"hierarchy '{h.name}' references unknown columns {missing}")
        for c in self.columns:
            if c.sort_by_column and c.sort_by_column.lower() not in names:
                raise ValueError(f"column '{c.name}' sorts by unknown '{c.sort_by_column}'")
        return self


class RelationshipSpec(BaseModel):
    """``from`` is the many side (usually the fact), ``to`` is the one side."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: Cardinality = Cardinality.MANY_TO_ONE
    cross_filter: CrossFilter = CrossFilter.SINGLE
    is_active: bool = True

    @property
    def key(self) -> str:
        return f"{self.from_table}[{self.from_column}] -> {self.to_table}[{self.to_column}]"


class ModelInfo(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    culture: str = "en-US"
    storage_mode: StorageMode = StorageMode.IMPORT
    compatibility_level: int = Field(default=1702, ge=1500)


class SemanticModelSpec(BaseModel):
    model: ModelInfo
    tables: list[TableSpec] = Field(default_factory=list)
    relationships: list[RelationshipSpec] = Field(default_factory=list)
    measures: list[MeasureSpec] = Field(default_factory=list)

    def table(self, name: str) -> TableSpec | None:
        return next((t for t in self.tables if t.name.lower() == name.lower()), None)

    def measure(self, name: str) -> MeasureSpec | None:
        return next((m for m in self.measures if m.name.lower() == name.lower()), None)

    @model_validator(mode="after")
    def _check_references(self) -> SemanticModelSpec:
        # Power BI object names are case-insensitive.
        _assert_unique([t.name for t in self.tables], "table")
        _assert_unique([m.name for m in self.measures], "measure")

        for rel in self.relationships:
            for table_name, column_name in (
                (rel.from_table, rel.from_column),
                (rel.to_table, rel.to_column),
            ):
                table = self.table(table_name)
                if table is None:
                    raise ValueError(f"relationship {rel.key}: unknown table '{table_name}'")
                if table.column(column_name) is None:
                    raise ValueError(
                        f"relationship {rel.key}: unknown column '{table_name}[{column_name}]'"
                    )
        _assert_unique([r.key for r in self.relationships], "relationship")

        for m in self.measures:
            table = self.table(m.table)
            if table is None:
                raise ValueError(f"measure '{m.name}' is homed in unknown table '{m.table}'")
            if table.column(m.name) is not None:
                raise ValueError(f"measure '{m.name}' collides with a column in '{m.table}'")
        return self


def _assert_unique(names: list[str], what: str) -> None:
    seen: set[str] = set()
    for name in names:
        key = name.lower()
        if key in seen:
            raise ValueError(f"duplicate {what} name: '{name}'")
        seen.add(key)
