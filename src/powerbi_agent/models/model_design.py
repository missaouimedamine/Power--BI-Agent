"""Result of star-schema design: the spec plus the reasoning behind it.

``specs/semantic_model.json`` holds only the :class:`SemanticModelSpec` (the IR that
renderers consume). ``specs/model_design.json`` holds this richer record for humans
and the LLM: grain, decisions, findings that need a business decision, and the diff
against the previous design.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, NonNegativeInt

from powerbi_agent.models.analysis import Severity
from powerbi_agent.models.semantic_model import SemanticModelSpec


class ModelFinding(BaseModel):
    severity: Severity
    topic: str = Field(description="e.g. null_foreign_keys, attribute_conflicts, grain")
    message: str
    table: str | None = None
    column: str | None = None
    affected_rows: NonNegativeInt = 0


class SpecDiff(BaseModel):
    """What changed between two model specs (names only; no data)."""

    tables_added: list[str] = Field(default_factory=list)
    tables_removed: list[str] = Field(default_factory=list)
    columns_added: list[str] = Field(default_factory=list)  # "Table[Column]"
    columns_removed: list[str] = Field(default_factory=list)
    relationships_added: list[str] = Field(default_factory=list)
    relationships_removed: list[str] = Field(default_factory=list)
    hierarchies_added: list[str] = Field(default_factory=list)  # "Table/Hierarchy"
    hierarchies_removed: list[str] = Field(default_factory=list)
    measures_added: list[str] = Field(default_factory=list)
    measures_removed: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(getattr(self, name) for name in type(self).model_fields)

    @property
    def has_removals(self) -> bool:
        return any(
            getattr(self, name) for name in type(self).model_fields if name.endswith("_removed")
        )


class ModelDesign(BaseModel):
    spec: SemanticModelSpec
    fact_table: str
    grain: list[str] = Field(description="Columns that identify one fact row.")
    grain_unique: bool = Field(description="True if the grain is unique in the raw data.")
    decisions: list[str] = Field(default_factory=list)
    findings: list[ModelFinding] = Field(default_factory=list)
    table_rows: dict[str, int] = Field(default_factory=dict)
    diff: SpecDiff | None = Field(default=None, description="Against the previous design.")
