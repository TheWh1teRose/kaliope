"""Gate contract (§8).

Gates run after the script node, never mutate anything, and produce a
:class:`GateReport`. They inform; they do not block review.

Every gate also describes itself through :meth:`Gate.spec` and reports the
numbers it computed in ``GateReport.measurements``. That is what lets the
console show what a check does and what it measured, rather than a bare verdict
the reviewer has to trust.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.schemas.document import ParsedDocument
from app.schemas.gates import GateReport, GateSpec, Violation
from app.schemas.pipeline import ContentBudget, FormatSpec, Outline, Script, Selection

__all__ = [
    "Gate",
    "GateContext",
    "GateReport",
    "GateSpec",
    "Violation",
    "failed",
    "ngrams",
    "passed",
    "skipped",
    "warned",
    "words",
]

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass
class GateContext:
    parsed: ParsedDocument
    script: Script
    outline: Outline
    selection: Selection
    budget: ContentBudget
    format_spec: FormatSpec
    config: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)


class Gate(Protocol):
    id: str
    name: str

    def check(self, ctx: GateContext) -> GateReport: ...

    def spec(self) -> GateSpec: ...


def passed(gate: Gate, measurements: dict[str, Any] | None = None) -> GateReport:
    return GateReport(id=gate.id, name=gate.name, status="pass", measurements=measurements or {})


def failed(
    gate: Gate, violations: list[Violation], measurements: dict[str, Any] | None = None
) -> GateReport:
    return GateReport(
        id=gate.id,
        name=gate.name,
        status="fail",
        violations=violations,
        measurements=measurements or {},
    )


def warned(
    gate: Gate, violations: list[Violation], measurements: dict[str, Any] | None = None
) -> GateReport:
    return GateReport(
        id=gate.id,
        name=gate.name,
        status="warn",
        violations=violations,
        measurements=measurements or {},
    )


def skipped(gate: Gate, reason: str, measurements: dict[str, Any] | None = None) -> GateReport:
    return GateReport(
        id=gate.id,
        name=gate.name,
        status="skipped",
        skip_reason=reason,
        measurements=measurements or {},
    )


def words(text: str) -> list[str]:
    return [m.group(0).casefold() for m in _WORD.finditer(text)]


def ngrams(tokens: list[str], size: int) -> set[tuple[str, ...]]:
    if len(tokens) < size:
        return set()
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}
