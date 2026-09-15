"""Static checks on a flow definition, before anyone spends a model call on it.

The runner wires nodes by field name: a node declares an input, and whatever
earlier node published a value under that name supplies it (§7.1). That rule is
simple enough to run over a draft without executing anything, which is what this
module does — it walks the node list in order, tracks what has been published,
and reports the first thing that would go wrong.

Everything here is derived from the node classes themselves. A node added later
is checked without a change in this file.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from app.pipeline.framework.registry import get_node, node_names
from app.pipeline.gates import ALL_GATE_IDS

#: What the worker seeds every run's value bag with before the first node runs.
#: Mirrors ``Worker.execute_run``; a node consuming any of these needs no
#: upstream node to supply it.
RUN_SEED_KEYS: tuple[str, ...] = (
    "document_ref",
    "target_minutes",
    "format_spec",
    "audience_spec",
)

#: Keys ``Worker._finish_run`` reads off the bag once the flow completes: the
#: gate suite runs over them and the script is what gets persisted. A flow that
#: never publishes one of these executes fine and then fails at the finish line,
#: which is exactly the failure this check exists to move forward in time.
REQUIRED_OUTPUT_KEYS: tuple[str, ...] = (
    "parsed",
    "budget",
    "selection",
    "outline",
    "script",
)


class WiringOut(BaseModel):
    """One input of one node, and where its value comes from."""

    key: str
    #: The node that publishes this key, ``None`` when it comes from the seeds.
    source: str | None = None
    from_seed: bool = False
    satisfied: bool = True


class NodeCheckOut(BaseModel):
    node: str
    position: int
    exists: bool = True
    produces: str | None = None
    wiring: list[WiringOut] = Field(default_factory=list)
    #: Inputs nothing upstream provides. Each one would fail the run at this node.
    missing: list[str] = Field(default_factory=list)
    #: Config keys the node declares no parameter for. Harmless — nodes read
    #: config by name — but almost always a typo, so it is reported.
    unknown_config: list[str] = Field(default_factory=list)


class FlowValidationOut(BaseModel):
    valid: bool
    #: Blocking: the flow cannot run.
    errors: list[str] = Field(default_factory=list)
    #: Non-blocking: the flow runs, but probably not as intended.
    warnings: list[str] = Field(default_factory=list)
    nodes: list[NodeCheckOut] = Field(default_factory=list)
    #: Seed keys this flow actually consumes.
    seeds: list[str] = Field(default_factory=list)


def validate_flow(
    nodes: list[dict[str, Any]] | list[Any],
    gates: list[str],
    *,
    as_pipeline: bool = True,
    extra_seeds: Sequence[str] = (),
) -> FlowValidationOut:
    """Check a draft flow's node order, wiring, gates and config keys.

    ``nodes`` is a list of ``{"node": name, "config": {...}}`` mappings or of
    objects with the same two attributes.

    ``as_pipeline`` is the production-run check: the flow must publish every
    key the finish line reads, and an empty gate list is a warning. The bench
    turns that off — a single node or a short chain is a valid experiment.

    ``extra_seeds`` are keys already in the value bag. The bench treats them
    as available the same way a run treats ``document_ref``.
    """
    entries = [_entry(item) for item in nodes]
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[NodeCheckOut] = []

    published: dict[str, str] = {}
    consumed_seeds: list[str] = []
    seen: set[str] = set()
    bag_seeds = frozenset(extra_seeds)

    if not entries:
        errors.append(
            "A pipeline needs at least one node."
            if as_pipeline
            else "The bench needs at least one node to run."
        )

    for position, (name, config) in enumerate(entries):
        if name in seen:
            errors.append(
                f"'{name}' appears more than once. A node runs at most once per flow: its "
                f"output is published under a single key, and a second instance would "
                f"overwrite the first."
            )
        seen.add(name)

        try:
            node = get_node(name)
        except KeyError:
            errors.append(
                f"'{name}' is not a registered node. Available: {', '.join(node_names())}."
            )
            checks.append(NodeCheckOut(node=name, position=position, exists=False))
            continue

        wiring: list[WiringOut] = []
        missing: list[str] = []
        for key, info in node.Input.model_fields.items():
            source = published.get(key)
            if source is not None:
                wiring.append(WiringOut(key=key, source=source))
                continue
            if key in RUN_SEED_KEYS or key in bag_seeds:
                if key not in consumed_seeds:
                    consumed_seeds.append(key)
                wiring.append(WiringOut(key=key, from_seed=True))
                continue
            if not info.is_required():
                wiring.append(WiringOut(key=key, satisfied=True))
                continue
            missing.append(key)
            wiring.append(WiringOut(key=key, satisfied=False))

        if missing:
            errors.append(
                f"'{name}' needs {', '.join(missing)}, which nothing before it publishes. "
                f"Move a node that produces it earlier, or add one."
            )

        declared = {p.key for p in _params(node)}
        unknown = sorted(k for k in config if declared and k not in declared)
        if unknown:
            warnings.append(
                f"'{name}' is configured with {', '.join(unknown)}, which it declares no "
                f"parameter for. The value is stored and ignored."
            )

        checks.append(
            NodeCheckOut(
                node=name,
                position=position,
                produces=node.produces,
                wiring=wiring,
                missing=missing,
                unknown_config=unknown,
            )
        )
        published[node.produces] = name

    if as_pipeline:
        for key in REQUIRED_OUTPUT_KEYS:
            if key not in published:
                errors.append(
                    f"Nothing in this pipeline publishes '{key}'. The run would execute and then "
                    f"fail when the quality gates and the script are assembled."
                )

    unknown_gates = [g for g in gates if g not in ALL_GATE_IDS]
    if unknown_gates:
        errors.append(
            f"Unknown quality check(s): {', '.join(unknown_gates)}. "
            f"Known: {', '.join(ALL_GATE_IDS)}."
        )
    if as_pipeline and not gates:
        warnings.append(
            "No quality checks are selected. The run produces a script that nothing reports on."
        )

    return FlowValidationOut(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        nodes=checks,
        seeds=consumed_seeds,
    )


def _entry(item: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(item, dict):
        return str(item.get("node", "")), dict(item.get("config") or {})
    return str(getattr(item, "node", "")), dict(getattr(item, "config", None) or {})


def _params(node: Any) -> list[Any]:
    from app.pipeline.framework.spec import node_params

    return node_params(node)
