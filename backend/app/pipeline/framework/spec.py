"""How a node describes itself to the pipeline editor (§7.1).

A node already declares its types; what an author editing a flow additionally
needs is *prose* — what the step does, what each knob changes, and how it can
fail — and a typed description of the knobs themselves so the console can render
an editor instead of a JSON blob.

Both are optional. A node that declares neither still appears in the editor,
with its docstring as the description and its existing config shown as free-form
key/value pairs; declaring them is what turns that into an explained form.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: How the console renders a parameter, and what it accepts.
#:
#: ``prompt`` is ``text`` that happens to be a system prompt: same storage, but
#: the editor gives it a monospace full-width box and a "back to default" action,
#: because it is the field authors will spend the most time in.
ParamType = Literal["string", "text", "prompt", "int", "float", "bool", "model", "select"]


class NodeParam(BaseModel):
    """One editable entry of a node's flow config."""

    key: str
    label: str
    type: ParamType = "string"
    #: What the node uses when the flow config does not set this key. It is the
    #: node's own constant, not a copy, so a default that moves in the code
    #: moves in the editor.
    default: Any = None
    description: str | None = None
    #: For ``select``.
    options: list[str] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    #: Folded away by default: correct for almost every flow.
    advanced: bool = False


class NodeDoc(BaseModel):
    """What a node does, in the words of the node itself."""

    #: One line. Shown next to the node everywhere it is listed.
    summary: str
    #: Paragraphs. How the step actually works, in order.
    detail: list[str] = Field(default_factory=list)
    #: ``input key`` → what the node does with it.
    inputs: dict[str, str] = Field(default_factory=dict)
    #: What the published value contains.
    output: str | None = None
    #: The ways this node fails a run, stated as the author would hit them.
    failure_modes: list[str] = Field(default_factory=list)
    #: Whether the step calls a model, and what that costs in practice.
    cost: str | None = None


def node_title(node: Any) -> str:
    return str(getattr(node, "title", None) or node.name)


def node_doc(node: Any) -> NodeDoc:
    """The node's documentation, falling back to its docstring."""
    declared = getattr(node, "doc", None)
    if isinstance(declared, NodeDoc):
        return declared
    text = " ".join((type(node).__doc__ or "").split())
    return NodeDoc(summary=text or f"The '{node.name}' node.")


#: Inserted after a node's model parameter. Empty means the model default and
#: must not be written into a saved flow (the cache key hashes the config).
EFFORT_PARAM = NodeParam(
    key="effort",
    label="Aufwand",
    type="select",
    description="Denkaufwand. Leer verwendet den Standard des gewählten Modells.",
    options=["low", "medium", "high", "xhigh", "max"],
)


def node_params(node: Any) -> list[NodeParam]:
    declared = getattr(node, "params", None)
    if not isinstance(declared, list):
        return []
    params = [p for p in declared if isinstance(p, NodeParam)]
    return _with_effort(params)


def _with_effort(params: list[NodeParam]) -> list[NodeParam]:
    """Offer effort beside the model choice of every node that has one."""
    if any(param.key == "effort" for param in params):
        return params
    if not any(param.type == "model" for param in params):
        return params
    out: list[NodeParam] = []
    placed = False
    for param in params:
        out.append(param)
        if not placed and param.type == "model":
            out.append(EFFORT_PARAM)
            placed = True
    return out


def strip_unsupported_effort(config: dict[str, Any]) -> dict[str, Any]:
    """Drop an effort the resolved model will not send.

    Leaving it in the saved definition would change the node cache key without
    changing the request. An unknown effort is dropped the same way.
    """
    if "effort" not in config:
        return config
    from app.config import get_settings
    from app.llm.registry import accepted_effort

    raw = config.get("effort")
    model = config.get("model")
    model_id = str(model) if isinstance(model, str) and model else get_settings().default_model
    kept = accepted_effort(model_id, raw if isinstance(raw, str) else None)
    if kept is None:
        return {key: value for key, value in config.items() if key != "effort"}
    return config


def param_defaults(node: Any) -> dict[str, Any]:
    return {p.key: p.default for p in node_params(node) if p.default is not None}


def prune_defaults(node: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Drop config entries that only restate the node's own default.

    The node cache key is a hash of the config (§7.2), so writing a default back
    into a flow would invalidate every cached artifact of that node for no
    behavioural change. Saving a flow through the editor therefore stores the
    difference from the defaults, not the resolved values.
    """
    defaults = {p.key: p.default for p in node_params(node)}
    return {
        key: value
        for key, value in config.items()
        if not (key in defaults and _equal(defaults[key], value))
    }


def _equal(default: Any, value: Any) -> bool:
    if isinstance(default, float) or isinstance(value, float):
        try:
            return abs(float(default) - float(value)) < 1e-12
        except (TypeError, ValueError):
            return False
    return bool(default == value)


def coerce(param: NodeParam, value: Any) -> Any:
    """Bring an editor value onto the type the node expects.

    HTML inputs hand back strings. A node reading ``ctx.get("temperature")``
    straight into a request body would then send ``"0.7"``, so the conversion
    happens once, here, on the way in.
    """
    if value is None or value == "":
        return None
    if param.type == "int":
        return int(float(value))
    if param.type == "float":
        return float(value)
    if param.type == "bool":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if param.type in {"string", "text", "prompt", "model", "select"}:
        return str(value)
    return value
