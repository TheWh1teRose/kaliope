"""Gate report shapes (§8)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

GateStatus = Literal["pass", "warn", "fail", "skipped"]
GateSeverity = Literal["fail", "warn"]


class GateSpec(BaseModel):
    """What a gate checks, stated by the gate itself.

    The console has a view whose whole purpose is showing the reviewer what the
    checks actually do, so the description has to come from the gate rather than
    from a hand-maintained table that drifts the first time a threshold moves.
    """

    id: str
    name: str
    #: What a violation costs. ``warn`` gates never report ``fail``.
    severity: GateSeverity
    #: Node outputs the gate reads. This is what maps a finding back to a node.
    inspects: list[str] = Field(default_factory=list)
    #: One sentence: the condition that must hold.
    rule: str
    #: How the condition is computed — the part that is otherwise invisible.
    method: str
    #: Named constants the gate compares against, with their current values.
    thresholds: dict[str, Any] = Field(default_factory=dict)
    #: When the gate declines to run at all, and why.
    skip_condition: str | None = None
    #: True when the gate makes a model call. Every gate in this build is False.
    uses_model: bool = False


class Violation(BaseModel):
    target_id: str | None = None
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class GateReport(BaseModel):
    id: str
    name: str
    status: GateStatus
    violations: list[Violation] = Field(default_factory=list)
    #: Why the gate did not run, when ``status == "skipped"`` (AC-GATE-4).
    skip_reason: str | None = None
    #: The numbers this gate actually computed, whatever the outcome. A passing
    #: gate that shows its measurement is auditable; one that shows only "pass"
    #: is a claim the reviewer has to take on trust.
    measurements: dict[str, Any] = Field(default_factory=dict)


class GateSuiteReport(BaseModel):
    reports: list[GateReport]

    @property
    def failed(self) -> list[str]:
        return [r.id for r in self.reports if r.status == "fail"]

    @property
    def warned(self) -> list[str]:
        return [r.id for r in self.reports if r.status == "warn"]


class GateDetail(BaseModel):
    """A gate's specification joined with its result for one run."""

    spec: GateSpec
    report: GateReport | None = None
