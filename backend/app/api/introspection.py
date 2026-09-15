"""Reading a node's types back out for the console.

Both the run graph and the pipeline editor describe nodes by their pydantic
models rather than by a hand-maintained table, so the two views cannot drift
apart from the code or from each other.
"""

from __future__ import annotations

from typing import Any, TypeGuard

from pydantic import BaseModel

from app.pipeline.framework.registry import get_node, node_names
from app.schemas.api import FieldOut, PortOut


def type_name(annotation: Any) -> str:
    if annotation is None:
        return "None"
    name = getattr(annotation, "__name__", None)
    return name if isinstance(name, str) else str(annotation).replace("typing.", "")


def is_model(annotation: Any) -> TypeGuard[type[BaseModel]]:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def fields_of(model: type[BaseModel]) -> list[FieldOut]:
    return [
        FieldOut(
            name=name,
            type=type_name(info.annotation),
            required=info.is_required(),
            description=info.description,
        )
        for name, info in model.model_fields.items()
    ]


def port_for_key(key: str) -> PortOut:
    """Type a value by whichever registered node declares it as an input.

    Run seeds have no producing node, so their shape is only knowable from the
    consumer's side. Scanning every node rather than one flow's nodes means the
    editor can type a seed before any pipeline has been assembled.
    """
    for name in node_names():
        info = get_node(name).Input.model_fields.get(key)
        if info is None:
            continue
        annotation = info.annotation
        return PortOut(
            key=key,
            model=type_name(annotation),
            fields=fields_of(annotation) if is_model(annotation) else [],
            produced_by=None,
        )
    return PortOut(key=key, model="unknown", produced_by=None)
