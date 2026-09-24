"""Tasks, plans and their lifecycle."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Intent(StrEnum):
    ANALYZE = "analyze"
    PROFILE = "profile"
    DESIGN_MODEL = "design_model"
    GENERATE_MODEL = "generate_model"
    GENERATE_DAX = "generate_dax"
    GENERATE_REPORT = "generate_report"
    VALIDATE = "validate"
    PUBLISH = "publish"
    UNKNOWN = "unknown"


class Risk(StrEnum):
    """How much damage a step could do. Drives the confirmation policy."""

    READ_ONLY = "read_only"  # inspects data/models; no side effects
    LOCAL_WRITE = "local_write"  # writes new files inside the workspace
    DESTRUCTIVE = "destructive"  # overwrites/deletes local or model objects
    EXTERNAL = "external"  # talks to Power BI / Fabric or any remote service

    @property
    def requires_confirmation(self) -> bool:
        return self in {Risk.DESTRUCTIVE, Risk.EXTERNAL}


class Capability(StrEnum):
    """Units of work that specialised agents register for."""

    LOAD_DATA = "load_data"
    PROFILE_DATA = "profile_data"
    CHECK_QUALITY = "check_quality"
    GENERATE_INSIGHTS = "generate_insights"
    VALIDATE_DATA = "validate_data"
    TRANSFORM_DATA = "transform_data"
    DESIGN_STAR_SCHEMA = "design_star_schema"
    GENERATE_DAX = "generate_dax"
    VALIDATE_DAX = "validate_dax"
    GENERATE_PBIP = "generate_pbip"
    VALIDATE_MODEL = "validate_model"
    DESIGN_REPORT = "design_report"
    VALIDATE_REPORT = "validate_report"
    PUBLISH = "publish"


class TaskStatus(StrEnum):
    PENDING = "pending"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    NOT_IMPLEMENTED = "not_implemented"

    @property
    def is_terminal(self) -> bool:
        return self not in {
            TaskStatus.PENDING,
            TaskStatus.AWAITING_CONFIRMATION,
            TaskStatus.RUNNING,
        }


class PlanStep(BaseModel):
    id: str = Field(default_factory=new_id)
    capability: Capability
    description: str
    risk: Risk = Risk.READ_ONLY
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    id: str = Field(default_factory=new_id)
    request: str
    intent: Intent
    steps: list[PlanStep] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def requires_confirmation(self) -> bool:
        return any(step.risk.requires_confirmation for step in self.steps)


class StepResult(BaseModel):
    step_id: str
    capability: Capability
    status: TaskStatus
    agent: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    error: str | None = None

    @property
    def duration_ms(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds() * 1000


class Task(BaseModel):
    id: str = Field(default_factory=new_id)
    request: str
    plan: Plan | None = None
    status: TaskStatus = TaskStatus.PENDING
    results: list[StepResult] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    error: str | None = None
