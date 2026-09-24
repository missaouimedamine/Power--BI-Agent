"""Structured validation reports shared by all validators."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

from powerbi_agent.models.analysis import Severity


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"  # a check that could not run is never reported as passed


class ValidationFinding(BaseModel):
    check: str
    status: CheckStatus
    severity: Severity = Severity.INFO
    object: str | None = Field(default=None, description="File, table, column or measure.")
    message: str = ""


class ValidationReport(BaseModel):
    area: str = Field(description="data | model | dax | report")
    target: str
    findings: list[ValidationFinding] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return not any(
            f.status is CheckStatus.FAILED and f.severity is Severity.ERROR for f in self.findings
        )

    def add(
        self,
        check: str,
        ok: bool,
        message: str = "",
        *,
        object: str | None = None,
        severity: Severity = Severity.ERROR,
    ) -> None:
        self.findings.append(
            ValidationFinding(
                check=check,
                status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
                severity=Severity.INFO if ok else severity,
                object=object,
                message=message,
            )
        )
