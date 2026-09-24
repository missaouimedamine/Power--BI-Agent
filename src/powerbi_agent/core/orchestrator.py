"""Coordinates plan execution across specialised agents.

Guarantees:

* Steps whose risk requires confirmation never run unless the confirmation callback
  explicitly approves that step. No callback means "declined".
* A step is only SUCCEEDED if its agent returned SUCCEEDED. Missing agents yield
  NOT_IMPLEMENTED; exceptions yield FAILED. Dependants of a non-successful step are
  SKIPPED.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from powerbi_agent.core.agent import AgentContext, AgentRegistry
from powerbi_agent.core.events import Event, EventBus, EventType
from powerbi_agent.core.planner import Planner, RuleBasedPlanner
from powerbi_agent.core.state import StateStore
from powerbi_agent.models.tasks import Plan, PlanStep, StepResult, Task, TaskStatus, utcnow
from powerbi_agent.utils.config import Settings
from powerbi_agent.utils.logging import get_logger, log_context
from powerbi_agent.utils.security import redact_text

ConfirmFn = Callable[[Plan, PlanStep], bool]

logger = get_logger("orchestrator")


class Orchestrator:
    def __init__(
        self,
        registry: AgentRegistry,
        settings: Settings,
        *,
        planner: Planner | None = None,
        bus: EventBus | None = None,
        confirm: ConfirmFn | None = None,
        state: StateStore | None = None,
    ) -> None:
        self.registry = registry
        self.settings = settings
        self.planner = planner or RuleBasedPlanner()
        self.bus = bus or EventBus()
        self.confirm = confirm
        self.state = state

    def plan(self, request: str) -> Task:
        task = Task(request=request)
        self._emit(EventType.TASK_CREATED, task)
        task.plan = self.planner.plan(request)
        self._emit(
            EventType.PLAN_CREATED,
            task,
            data={"intent": task.plan.intent.value, "steps": len(task.plan.steps)},
        )
        return task

    def run(self, request: str, workspace_dir: Path) -> Task:
        return self.execute(self.plan(request), workspace_dir)

    def execute(self, task: Task, workspace_dir: Path) -> Task:
        if task.plan is None:
            raise ValueError("task has no plan")
        context = AgentContext(task_id=task.id, settings=self.settings, workspace_dir=workspace_dir)
        task.status = TaskStatus.RUNNING
        status_by_step: dict[str, TaskStatus] = {}

        with log_context(task_id=task.id):
            for step in task.plan.steps:
                result = self._run_step(task, step, context, status_by_step)
                status_by_step[step.id] = result.status
                task.results.append(result)
                if result.status is TaskStatus.SUCCEEDED:
                    context.outputs[step.id] = result.output

            task.status = _overall_status(list(status_by_step.values()))
            task.updated_at = utcnow()
            self._emit(EventType.TASK_FINISHED, task, status=task.status)

        if self.state is not None:
            self.state.save_task(task)
        return task

    def _run_step(
        self,
        task: Task,
        step: PlanStep,
        context: AgentContext,
        status_by_step: dict[str, TaskStatus],
    ) -> StepResult:
        assert task.plan is not None
        blocked = [d for d in step.depends_on if status_by_step.get(d) is not TaskStatus.SUCCEEDED]
        if blocked:
            return _finished(step, TaskStatus.SKIPPED, error=f"dependency not satisfied: {blocked}")

        if step.risk.requires_confirmation:
            self._emit(EventType.CONFIRMATION_REQUESTED, task, action=step.capability.value)
            approved = self.confirm is not None and self.confirm(task.plan, step)
            if not approved:
                self._emit(EventType.CONFIRMATION_DECLINED, task, action=step.capability.value)
                return _finished(step, TaskStatus.CANCELLED, error="confirmation not given")

        agent = self.registry.for_capability(step.capability)
        if agent is None:
            return _finished(
                step,
                TaskStatus.NOT_IMPLEMENTED,
                error=f"no agent registered for capability '{step.capability}'",
            )

        with log_context(agent=agent.name):
            self._emit(EventType.STEP_STARTED, task, agent=agent.name, action=step.capability)
            started = time.perf_counter()
            try:
                result = agent.run(step, context)
            except Exception as exc:
                logger.exception("step failed", extra={"fields": {"step_id": step.id}})
                result = StepResult(
                    step_id=step.id,
                    capability=step.capability,
                    status=TaskStatus.FAILED,
                    agent=agent.name,
                    error=redact_text(f"{type(exc).__name__}: {exc}"),
                )
            result.finished_at = utcnow()
            self._emit(
                EventType.STEP_FINISHED,
                task,
                agent=agent.name,
                action=step.capability,
                status=result.status,
                duration_ms=(time.perf_counter() - started) * 1000,
                error=result.error,
            )
        return result

    def _emit(self, type_: EventType, task: Task, **kwargs: object) -> None:
        self.bus.publish(Event(type=type_, task_id=task.id, **kwargs))


def _finished(step: PlanStep, status: TaskStatus, *, error: str | None = None) -> StepResult:
    return StepResult(
        step_id=step.id,
        capability=step.capability,
        status=status,
        finished_at=utcnow(),
        error=error,
    )


def _overall_status(statuses: list[TaskStatus]) -> TaskStatus:
    for status in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.NOT_IMPLEMENTED):
        if status in statuses:
            return status
    if statuses and all(s is TaskStatus.SUCCEEDED for s in statuses):
        return TaskStatus.SUCCEEDED
    # Nothing ran (e.g. an unknown intent produced an empty plan): never report success.
    return TaskStatus.SKIPPED
