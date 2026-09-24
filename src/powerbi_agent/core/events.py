"""In-process event bus used for observability and (later) API progress streaming."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from powerbi_agent.models.tasks import TaskStatus, utcnow
from powerbi_agent.utils.logging import get_logger


class EventType(StrEnum):
    TASK_CREATED = "task_created"
    PLAN_CREATED = "plan_created"
    CONFIRMATION_REQUESTED = "confirmation_requested"
    CONFIRMATION_DECLINED = "confirmation_declined"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    TASK_FINISHED = "task_finished"


class Event(BaseModel):
    type: EventType
    task_id: str
    timestamp: datetime = Field(default_factory=utcnow)
    agent: str | None = None
    tool: str | None = None
    action: str | None = None
    status: TaskStatus | None = None
    duration_ms: float | None = None
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        self._subscribers.append(fn)
        return lambda: self._subscribers.remove(fn)

    def publish(self, event: Event) -> None:
        for fn in list(self._subscribers):
            try:
                fn(event)
            except Exception:  # a broken subscriber must never break the task
                get_logger("events").exception("event subscriber failed")


def log_event(event: Event) -> None:
    """Default subscriber: emit every event as a structured log record."""
    fields = event.model_dump(mode="json", exclude_none=True, exclude={"timestamp"})
    fields.pop("data", None)
    fields.update(event.data)
    level = 40 if event.status is TaskStatus.FAILED else 20
    get_logger("events").log(level, event.type.value, extra={"fields": fields})
