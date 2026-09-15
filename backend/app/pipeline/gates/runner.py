"""Gate suite execution (§8).

Gates inform; they do not block review. A gate that raises is reported as a
failure of that gate alone — one broken check must not hide the other eight.
"""

from __future__ import annotations

import logging
from typing import Any

from app.pipeline.gates.base import Gate, GateContext
from app.pipeline.gates.deterministic import (
    G0IngestionConfidence,
    G1AnchorsResolve,
    G2ClaimsCited,
    G3NoApparatusLeak,
    G4LengthBand,
    G5ObjectiveCoverage,
    G6NoBoilerplate,
    G8Structure,
)
from app.pipeline.gates.g7_readability import G7Readability
from app.schemas.gates import GateReport, GateSpec, GateSuiteReport, Violation

logger = logging.getLogger(__name__)

_REGISTERED: list[Gate] = [
    G0IngestionConfidence(),
    G1AnchorsResolve(),
    G2ClaimsCited(),
    G3NoApparatusLeak(),
    G4LengthBand(),
    G5ObjectiveCoverage(),
    G6NoBoilerplate(),
    G7Readability(),
    G8Structure(),
]

GATES: dict[str, Gate] = {gate.id: gate for gate in _REGISTERED}

ALL_GATE_IDS: list[str] = sorted(GATES)


def gate_catalogue(gate_ids: list[str] | None = None) -> list[GateSpec]:
    """What every registered gate checks, and how.

    Derived from the gates themselves, so a threshold that moves in the gate
    moves here too. Nothing else in the system enumerates the gates (§15).
    """
    return [GATES[gate_id].spec() for gate_id in (gate_ids or ALL_GATE_IDS) if gate_id in GATES]


def run_gates(
    ctx: GateContext,
    gate_ids: list[str] | None = None,
    gate_config: dict[str, dict[str, Any]] | None = None,
) -> GateSuiteReport:
    selected = gate_ids or ALL_GATE_IDS
    configs = gate_config or {}
    reports: list[GateReport] = []

    for gate_id in selected:
        gate = GATES.get(gate_id)
        if gate is None:
            reports.append(
                GateReport(
                    id=gate_id,
                    name="unknown",
                    status="skipped",
                    skip_reason=f"no gate is registered under id '{gate_id}'",
                )
            )
            continue
        scoped = GateContext(
            parsed=ctx.parsed,
            script=ctx.script,
            outline=ctx.outline,
            selection=ctx.selection,
            budget=ctx.budget,
            format_spec=ctx.format_spec,
            config=configs.get(gate_id, {}),
        )
        try:
            reports.append(gate.check(scoped))
        except Exception as exc:  # noqa: BLE001 - one broken gate must not hide the rest
            logger.exception("gate %s raised", gate_id)
            reports.append(
                GateReport(
                    id=gate.id,
                    name=gate.name,
                    status="fail",
                    violations=[
                        Violation(message=f"The gate itself failed: {type(exc).__name__}: {exc}")
                    ],
                )
            )

    return GateSuiteReport(reports=reports)
