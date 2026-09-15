"""Node and flow registries (§7.5).

Flows are YAML files. Adding a flow over existing nodes is a new file and no
Python change (AC-FW-4).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from app.pipeline.framework.node import Node

logger = logging.getLogger(__name__)

_NODES: dict[str, Node] = {}


def flow_directory() -> Path:
    """Where the flows that ship with the build live."""
    return Path(__file__).resolve().parents[1] / "flows"


def register_node(node: Node) -> Node:
    if node.name in _NODES and _NODES[node.name] is not node:
        raise ValueError(f"node '{node.name}' is already registered")
    _NODES[node.name] = node
    return node


def get_node(name: str) -> Node:
    try:
        return _NODES[name]
    except KeyError as exc:
        raise KeyError(
            f"unknown node '{name}'. Registered nodes: {', '.join(sorted(_NODES)) or 'none'}"
        ) from exc


def node_names() -> list[str]:
    return sorted(_NODES)


class FlowNode(BaseModel):
    node: str
    config: dict[str, Any] = Field(default_factory=dict)


class Flow(BaseModel):
    id: str
    version: str
    description: str | None = None
    nodes: list[FlowNode]
    gates: list[str] = Field(default_factory=list)
    #: Absolute path the flow was loaded from.
    path: str | None = None


def load_flow(path: Path) -> Flow:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"flow file {path} is not a mapping")
    data.setdefault("path", str(path))
    flow = Flow.model_validate(data)
    for entry in flow.nodes:
        get_node(entry.node)  # fail fast on an unknown node
    return flow


def discover_flows(directory: Path) -> dict[str, Flow]:
    flows: dict[str, Flow] = {}
    if not directory.exists():
        return flows
    for path in sorted(directory.glob("*.yaml")):
        try:
            flow = load_flow(path)
        except Exception:  # noqa: BLE001 - a broken flow must not stop boot
            logger.exception("failed to load flow %s", path)
            continue
        flows[flow.id] = flow
    return flows


def bootstrap_nodes() -> None:
    """Import the node modules so their registrations run."""
    from app.pipeline import nodes  # noqa: F401
