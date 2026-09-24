"""Validator agent. Phase 2 covers data validation; model/DAX/report checks follow."""

from __future__ import annotations

from typing import ClassVar

from powerbi_agent.core.agent import AgentContext, BaseAgent
from powerbi_agent.models.analysis import Severity
from powerbi_agent.models.tasks import Capability, PlanStep, StepResult, TaskStatus
from powerbi_agent.models.validation import CheckStatus
from powerbi_agent.tools.filesystem import Workspace
from powerbi_agent.validation.data_validator import validate_analysis

REPORT_PATH = "validation/data_validation.json"


class ValidatorAgent(BaseAgent):
    name = "validator"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.VALIDATE_DATA})

    def run(self, step: PlanStep, context: AgentContext) -> StepResult:
        report = validate_analysis(context.workspace_dir)
        ws = Workspace(context.workspace_dir, run_id=context.task_id)
        path = ws.relative(ws.write_json(REPORT_PATH, report))

        failed = [f for f in report.findings if f.status is CheckStatus.FAILED]
        output = {
            "passed": report.passed,
            "checks_passed": sum(f.status is CheckStatus.PASSED for f in report.findings),
            "checks_failed": len(failed),
            "checks_not_run": sum(f.status is CheckStatus.NOT_RUN for f in report.findings),
        }
        if not report.passed:
            errors = [f for f in failed if f.severity is Severity.ERROR]
            detail = "; ".join(f"{f.check} ({f.object}): {f.message}" for f in errors[:3])
            return self.result(
                step,
                TaskStatus.FAILED,
                artifacts=[path],
                output=output,
                error=f"data validation failed: {detail}",
            )
        return self.result(step, TaskStatus.SUCCEEDED, artifacts=[path], output=output)
