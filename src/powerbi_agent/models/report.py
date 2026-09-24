"""Intermediate report specification.

Visuals are declared against named fields (``Table[Column]``) and measures
(``[Measure]``); a renderer later converts the spec into PBIR files. The LLM never
writes PBIR directly.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

_COLUMN_REF = re.compile(r"^\s*'?(?P<table>[^'\[\]]+?)'?\s*\[(?P<column>[^\[\]]+)\]\s*$")
_MEASURE_REF = re.compile(r"^\s*\[(?P<measure>[^\[\]]+)\]\s*$")


class FieldRef(BaseModel):
    """A reference to a column (``Table[Column]``) or a measure (``[Measure]``)."""

    table: str | None = None
    name: str

    @property
    def is_measure(self) -> bool:
        return self.table is None

    @classmethod
    def parse(cls, text: str) -> Self:
        if m := _MEASURE_REF.match(text):
            return cls(name=m["measure"].strip())
        if m := _COLUMN_REF.match(text):
            return cls(table=m["table"].strip(), name=m["column"].strip())
        raise ValueError(
            f"not a field reference: {text!r} (expected 'Table[Column]' or '[Measure]')"
        )

    def __str__(self) -> str:
        return f"[{self.name}]" if self.table is None else f"{self.table}[{self.name}]"


class VisualType(StrEnum):
    CARD = "card"
    KPI = "kpi"
    LINE = "line"
    BAR = "bar"
    COLUMN = "column"
    AREA = "area"
    PIE = "pie"
    DONUT = "donut"
    TABLE = "table"
    MATRIX = "matrix"
    SCATTER = "scatter"
    MAP = "map"
    SLICER = "slicer"
    TEXT = "text"


# Visuals that need a category axis and at least one value.
_AXIS_VISUALS = {VisualType.LINE, VisualType.BAR, VisualType.COLUMN, VisualType.AREA}


class Position(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class VisualSpec(BaseModel):
    id: str
    type: VisualType
    title: str = ""
    description: str = ""  # used as alt text for accessibility
    category: list[FieldRef] = Field(default_factory=list)
    values: list[FieldRef] = Field(default_factory=list)
    legend: FieldRef | None = None
    position: Position | None = None

    @field_validator("category", "values", mode="before")
    @classmethod
    def _parse_refs(cls, value: object) -> object:
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            return [FieldRef.parse(v) if isinstance(v, str) else v for v in value]
        return value

    @field_validator("legend", mode="before")
    @classmethod
    def _parse_legend(cls, value: object) -> object:
        return FieldRef.parse(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _check_shape(self) -> VisualSpec:
        if self.type in _AXIS_VISUALS and (not self.category or not self.values):
            raise ValueError(f"{self.type} visual '{self.id}' needs a category and a value")
        if self.type in {VisualType.CARD, VisualType.KPI} and len(self.values) != 1:
            raise ValueError(f"{self.type} visual '{self.id}' needs exactly one value")
        if self.type is VisualType.SLICER and len(self.category) != 1:
            raise ValueError(f"slicer '{self.id}' needs exactly one field")
        return self

    def field_refs(self) -> list[FieldRef]:
        refs = [*self.category, *self.values]
        if self.legend is not None:
            refs.append(self.legend)
        return refs


class FilterSpec(BaseModel):
    field: FieldRef
    values: list[str] = Field(default_factory=list)

    @field_validator("field", mode="before")
    @classmethod
    def _parse(cls, value: object) -> object:
        return FieldRef.parse(value) if isinstance(value, str) else value


class PageSpec(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    visuals: list[VisualSpec] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    drillthrough_fields: list[FieldRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_visual_ids(self) -> PageSpec:
        ids = [v.id for v in self.visuals]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate visual ids on page '{self.name}'")
        return self


class ReportSpec(BaseModel):
    name: str = Field(min_length=1)
    semantic_model: str
    pages: list[PageSpec] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    theme: str | None = None

    @model_validator(mode="after")
    def _unique_pages(self) -> ReportSpec:
        names = [p.name.lower() for p in self.pages]
        if len(names) != len(set(names)):
            raise ValueError("duplicate page names")
        return self

    def all_field_refs(self) -> list[FieldRef]:
        refs: list[FieldRef] = [f.field for f in self.filters]
        for page in self.pages:
            refs.extend(f.field for f in page.filters)
            refs.extend(page.drillthrough_fields)
            for visual in page.visuals:
                refs.extend(visual.field_refs())
        return refs
