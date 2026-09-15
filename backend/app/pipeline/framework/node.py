"""Node contract and execution context (§7.1).

A node is a typed pipeline step: pydantic input model in, pydantic output model
out. It never touches the database, never knows the flow it is part of, and
never reads configuration from the environment — everything it needs arrives on
:class:`NodeContext`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from app.llm.base import LLMClient
from app.pipeline.framework.artifacts import ArtifactStore


class NodeError(RuntimeError):
    """Raised by a node when it cannot produce a valid output.

    A ``NodeError`` fails the run with a readable message rather than a
    traceback into library internals. ``verdict`` carries a machine-readable
    reason when the failure is a modelled outcome rather than a defect — the
    content budget's ``insufficient`` verdict is the case this exists for
    (§6.1, AC-BL-2).
    """

    def __init__(self, message: str, *, verdict: str | None = None) -> None:
        super().__init__(message)
        self.verdict = verdict


class NodePause(Exception):  # noqa: N818
    """The node needs human input before it can produce an output.

    The runner treats this as a modelled pause, not a failure: the run stops
    at this node, the payload is stored for the console, and a later submit
    resumes from here.
    """

    def __init__(self, message: str, *, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


@dataclass
class NodeContext:
    run_id: str
    llm: LLMClient
    artifacts: ArtifactStore
    config: dict[str, Any]
    logger: logging.Logger
    #: Path to the source PDF. Only the ingest node needs it.
    document_path: Path | None = None
    document_id: str | None = None
    #: Called with a short human-readable progress line.
    progress: Callable[[str], None] = field(default=lambda _message: None)

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def model(self, fallback: str) -> str:
        """Resolve the model for this node.

        Flow config wins, then ``DEFAULT_MODEL`` from the environment, then the
        node's own fallback. Without the middle step ``DEFAULT_MODEL`` would be
        documented in ``.env.example`` and read by nothing.
        """
        from app.config import get_settings

        return str(self.config.get("model") or get_settings().default_model or fallback)


@runtime_checkable
class Node(Protocol):
    name: str
    #: Bump on any behaviour change; it is part of the cache key (§7.2).
    version: str
    Input: type[BaseModel]
    Output: type[BaseModel]
    #: Key this node's output is published under, for downstream nodes.
    produces: str

    # ``Any`` rather than ``BaseModel``: the runner constructs the argument
    # from this node's own ``Input``, so each implementation legitimately
    # narrows the parameter to its own model.
    def run(self, inp: Any, ctx: NodeContext) -> BaseModel: ...


def build_input(node: Node, bag: dict[str, Any]) -> BaseModel:
    """Assemble a node's input model from the run's value bag.

    Wiring is by field name: a node declares what it needs by naming it, and
    the runner supplies it. Adding a node that consumes an existing value needs
    no change to the runner (AC-FW-4). Optional fields are filled when present
    and left at their default otherwise.
    """
    missing = [
        name
        for name, info in node.Input.model_fields.items()
        if name not in bag and info.is_required()
    ]
    if missing:
        raise NodeError(
            f"node '{node.name}' requires {missing}, which no earlier node produced. "
            f"Available: {sorted(bag)}"
        )
    return node.Input(**{name: bag[name] for name in node.Input.model_fields if name in bag})


def input_keys(node: Node) -> list[str]:
    return list(node.Input.model_fields)


def required_input_keys(node: Node) -> list[str]:
    return [name for name, info in node.Input.model_fields.items() if info.is_required()]
