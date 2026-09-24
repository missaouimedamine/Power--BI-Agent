from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from powerbi_agent.core.agent import AgentContext, AgentRegistry, BaseAgent
from powerbi_agent.core.events import Event, EventBus, EventType
from powerbi_agent.core.orchestrator import Orchestrator
from powerbi_agent.core.planner import build_plan
from powerbi_agent.core.state import StateStore
from powerbi_agent.models.datasource import DataSource
from powerbi_agent.models.tasks import (
    Capability,
    Intent,
    Plan,
    PlanStep,
    StepResult,
    Task,
    TaskStatus,
)
from powerbi_agent.utils.config import Settings


class EchoAgent(BaseAgent):
    name = "echo"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(Capability)

    def __init__(self, fail_on: Capability | None = None) -> None:
        self.fail_on = fail_on
        self.calls: list[Capability] = []

    def run(self, step: PlanStep, context: AgentContext) -> StepResult:
        self.calls.append(step.capability)
        if step.capability is self.fail_on:
            raise RuntimeError("boom password=hunter2")
        return self.result(step, TaskStatus.SUCCEEDED, output={"seen": len(context.outputs)})


def _orchestrator(
    agent: BaseAgent | None, settings: Settings, **kwargs: object
) -> tuple[Orchestrator, list[Event]]:
    registry = AgentRegistry()
    if agent is not None:
        registry.register(agent)
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(events.append)
    return Orchestrator(registry, settings, bus=bus, **kwargs), events  # type: ignore[arg-type]


def _task(intent: Intent) -> Task:
    return Task(request="r", plan=build_plan("r", intent, [DataSource.from_path("sales.csv")]))


def test_all_steps_succeed_and_outputs_flow(settings: Settings, tmp_path: Path) -> None:
    agent = EchoAgent()
    orch, events = _orchestrator(agent, settings)
    task = orch.execute(_task(Intent.ANALYZE), tmp_path)
    assert task.status is TaskStatus.SUCCEEDED
    assert [r.output["seen"] for r in task.results] == [0, 1, 2, 3, 4]
    assert events[-1].type is EventType.TASK_FINISHED


def test_missing_agent_is_not_implemented_never_success(settings: Settings, tmp_path: Path) -> None:
    orch, _ = _orchestrator(None, settings)
    task = orch.execute(_task(Intent.ANALYZE), tmp_path)
    assert task.status is TaskStatus.NOT_IMPLEMENTED
    assert task.results[0].status is TaskStatus.NOT_IMPLEMENTED
    assert {r.status for r in task.results[1:]} == {TaskStatus.SKIPPED}


def test_failure_skips_dependants_and_redacts_error(settings: Settings, tmp_path: Path) -> None:
    agent = EchoAgent(fail_on=Capability.PROFILE_DATA)
    orch, _ = _orchestrator(agent, settings)
    task = orch.execute(_task(Intent.ANALYZE), tmp_path)
    assert task.status is TaskStatus.FAILED
    statuses = [r.status for r in task.results]
    assert statuses == [
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.SKIPPED,
        TaskStatus.SKIPPED,
        TaskStatus.SKIPPED,
    ]
    assert "hunter2" not in (task.results[1].error or "")


def test_external_step_blocked_without_confirmation(settings: Settings, tmp_path: Path) -> None:
    agent = EchoAgent()
    orch, events = _orchestrator(agent, settings)  # no confirm callback -> declined
    task = orch.execute(_task(Intent.PUBLISH), tmp_path)
    assert Capability.PUBLISH not in agent.calls
    assert task.results[-1].status is TaskStatus.CANCELLED
    assert task.status is TaskStatus.CANCELLED
    assert any(e.type is EventType.CONFIRMATION_DECLINED for e in events)


def test_external_step_runs_after_explicit_confirmation(settings: Settings, tmp_path: Path) -> None:
    asked: list[Capability] = []

    def confirm(plan: Plan, step: PlanStep) -> bool:
        asked.append(step.capability)
        return True

    agent = EchoAgent()
    orch, _ = _orchestrator(agent, settings, confirm=confirm)
    task = orch.execute(_task(Intent.PUBLISH), tmp_path)
    assert asked == [Capability.PUBLISH]  # read-only steps never prompt
    assert task.status is TaskStatus.SUCCEEDED


def test_empty_plan_is_not_reported_as_success(settings: Settings, tmp_path: Path) -> None:
    orch, _ = _orchestrator(EchoAgent(), settings)
    task = orch.execute(Task(request="?", plan=build_plan("?", Intent.UNKNOWN)), tmp_path)
    assert task.status is TaskStatus.SKIPPED


def test_task_is_persisted(settings: Settings, tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    orch, _ = _orchestrator(EchoAgent(), settings, state=store)
    task = orch.execute(_task(Intent.PROFILE), tmp_path)
    loaded = store.load_task(task.id)
    assert loaded is not None
    assert loaded.status is TaskStatus.SUCCEEDED
    assert task.id in store.load().task_ids


def test_registry_rejects_conflicting_agents() -> None:
    registry = AgentRegistry()
    registry.register(EchoAgent())
    with pytest.raises(ValueError, match="already provided"):
        registry.register(EchoAgent())
