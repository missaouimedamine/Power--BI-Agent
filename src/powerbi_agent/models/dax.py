"""DAX object specifications and validation results."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from powerbi_agent.models.analysis import Severity


class MeasureSpec(BaseModel):
    """A DAX measure, independent of how it is later materialised (TMDL or MCP)."""

    name: str = Field(min_length=1, max_length=128)
    table: str = Field(description="Home table of the measure.")
    expression: str = Field(min_length=1)
    format_string: str | None = None
    description: str = ""
    display_folder: str | None = None
    is_hidden: bool = False

    @field_validator("name")
    @classmethod
    def _no_brackets(cls, value: str) -> str:
        if any(ch in value for ch in "[]"):
            raise ValueError("measure names cannot contain '[' or ']'")
        return value.strip()


class CalculatedColumnSpec(BaseModel):
    name: str
    table: str
    expression: str
    data_type: str = "string"
    justification: str = Field(
        description="Why a calculated column is needed instead of a measure or source column."
    )


class DaxCheck(StrEnum):
    SYNTAX = "syntax"
    REFERENCES = "references"
    DIVIDE_BY_ZERO = "divide_by_zero"
    FILTER_CONTEXT = "filter_context"
    EXPECTED_OUTPUT = "expected_output"
    DUPLICATE_LOGIC = "duplicate_logic"


class DaxFinding(BaseModel):
    check: DaxCheck
    severity: Severity
    message: str


class DaxValidationResult(BaseModel):
    measure: str
    findings: list[DaxFinding] = Field(default_factory=list)
    executed_against_model: bool = Field(
        default=False,
        description="True only when the expression was actually evaluated by a live engine.",
    )

    @property
    def is_valid(self) -> bool:
        return not any(f.severity is Severity.ERROR for f in self.findings)
