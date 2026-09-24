"""Specialised agent interface and registry.

An agent owns one or more :class:`Capability` values and executes plan steps for them.
Agents are deterministic Python components; LLM reasoning (OpenCode agents) sits on
top and calls into them through tools, so every factual output is tool-produced.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from powerbi_agent.models.tasks import Capability, PlanStep, StepResult, TaskStatus
from powerbi_agent.utils.config import Settings


@dataclass
class AgentContext:
    """Everything a step needs besides its own inputs."""

    task_id: str
    settings: Settings
    workspace_dir: Path
    # Outputs of previously completed steps, keyed by step id.
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # In-memory objects shared between steps of one task (e.g. loaded DataFrames).
    # Never persisted; step outputs must stay JSON-serialisable.
    scratch: dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    name: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]]

    @abstractmethod
    def run(self, step: PlanStep, context: AgentContext) -> StepResult:
        """Execute ``step``. Must only report SUCCEEDED when the work was verified done."""

    def result(self, step: PlanStep, status: TaskStatus, **kwargs: Any) -> StepResult:
        return StepResult(
            step_id=step.id, capability=step.capability, status=status, agent=self.name, **kwargs
        )


class AgentRegistry:
    def __init__(self) -> None:
        self._by_capability: dict[Capability, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        for capability in agent.capabilities:
            existing = self._by_capability.get(capability)
            if existing is not None and existing is not agent:
                raise ValueError(
                    f"capability {capability} already provided by agent '{existing.name}'"
                )
            self._by_capability[capability] = agent

    def for_capability(self, capability: Capability) -> BaseAgent | None:
        return self._by_capability.get(capability)

    @property
    def capabilities(self) -> set[Capability]:
        return set(self._by_capability)


def default_registry() -> AgentRegistry:
    """Registry with every agent implemented so far.

    Each phase registers its agents here. Steps with no registered agent are reported
    as NOT_IMPLEMENTED, never faked.
    """
    # Imported here: agents depend on this module.
    from powerbi_agent.agents import DataAnalystAgent, PowerBIModelerAgent, ValidatorAgent

    registry = AgentRegistry()
    registry.register(DataAnalystAgent())  # Phase 2
    registry.register(ValidatorAgent())  # Phase 2 data, Phase 3 model validation
    registry.register(PowerBIModelerAgent())  # Phase 3
    return registry
