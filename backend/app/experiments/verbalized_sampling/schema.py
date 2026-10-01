"""Structured-output schemas for the VS call and the baseline call.

Each VS response carries ``segments`` in exactly the script node's shape, so
drafts follow the same grounding contract as production. No numeric or array
length constraints: providers reject most of them, and the parser checks count
and range instead.
"""

from __future__ import annotations

import copy
from typing import Any

from app.experiments.verbalized_sampling.options import Variant
from app.pipeline.nodes.script import _SCHEMA as SCRIPT_SCHEMA


def segments_schema() -> dict[str, Any]:
    return copy.deepcopy(SCRIPT_SCHEMA["properties"]["segments"])


def baseline_schema() -> dict[str, Any]:
    return copy.deepcopy(SCRIPT_SCHEMA)


def vs_schema(variant: Variant) -> dict[str, Any]:
    item_properties: dict[str, Any] = {"segments": segments_schema()}
    item_required = ["segments"]
    if variant != "list":
        # After segments, so the model writes the beat before it estimates it.
        item_properties["probability"] = {"type": "number"}
        item_required.append("probability")

    properties: dict[str, Any] = {}
    required: list[str] = []
    if variant == "cot":
        properties["reasoning"] = {"type": "string"}
        required.append("reasoning")
    properties["responses"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": item_properties,
            "required": item_required,
            "additionalProperties": False,
        },
    }
    required.append("responses")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
