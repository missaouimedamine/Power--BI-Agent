"""Validator agent: data (Phase 2) and semantic model (Phase 3) validation.

DAX and report validation arrive with Phases 4 and 7.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from powerbi_agent.core.agent import AgentContext, BaseAgent
from powerbi_agent.models.analysis import Severity
from powerbi_agent.models.tasks import Capability, PlanStep, StepResult, TaskStatus
from powerbi_agent.models.validation import CheckStatus, ValidationReport
from powerbi_agent.tools.filesystem import Workspace
from powerbi_agent.validation.data_validator import validate_analysis
from powerbi_agent.validation.model_validator import validate_model

_VALIDATORS: dict[Capability, tuple[Callable[[Path], ValidationReport], str]] = {
    Capability.VALIDATE_DATA: (validate_analysis, "validation/data_validation.json"),
    Capability.VALIDATE_MODEL: (validate_model, "validation/model_validation.json"),
}


class ValidatorAgent(BaseAgent):
    name = "validator"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(_VALIDATORS)

    def run(self, step: PlanStep, context: AgentContext) -> StepResult:
        validate, report_path = _VALIDATORS[step.capability]
        report = validate(context.workspace_dir)
        ws = Workspace(context.workspace_dir, run_id=context.task_id)
        path = ws.relative(ws.write_json(report_path, report))

        failed = [f for f in report.findings if f.status is CheckStatus.FAILED]
        warnings = [f for f in failed if f.severity is Severity.WARNING]
        output = {
            "area": report.area,
            "passed": report.passed,
            "checks_passed": sum(f.status is CheckStatus.PASSED for f in report.findings),
            "checks_failed": len(failed),
            "checks_not_run": sum(f.status is CheckStatus.NOT_RUN for f in report.findings),
            "warnings": [f"{f.check} ({f.object}): {f.message}" for f in warnings][:10],
        }
        if not report.passed:
            errors = [f for f in failed if f.severity is Severity.ERROR]
            detail = "; ".join(f"{f.check} ({f.object}): {f.message}" for f in errors[:3])
            return self.result(
                step,
                TaskStatus.FAILED,
                artifacts=[path],
                output=output,
                error=f"{report.area} validation failed: {detail}",
            )
        return self.result(step, TaskStatus.SUCCEEDED, artifacts=[path], output=output)
