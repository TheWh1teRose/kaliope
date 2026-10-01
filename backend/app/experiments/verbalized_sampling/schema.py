"""Structured-output schemas for the VS call and the baseline call.

Each VS response carries ``segments`` in exactly the script node's shape, so
drafts follow the same grounding contract as production. No numeric or array
length constraints: providers reject most of them, and the parser checks count
and range instead.
"""

from __future__ import annotations

import copy
from typing import Any

from app.pipeline.nodes.script import _SCHEMA as SCRIPT_SCHEMA


def segments_schema() -> dict[str, Any]:
    return copy.deepcopy(SCRIPT_SCHEMA["properties"]["segments"])


def baseline_schema() -> dict[str, Any]:
    return copy.deepcopy(SCRIPT_SCHEMA)


def vs_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "responses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "segments": segments_schema(),
                        # After segments, so the model writes the beat before it estimates it.
                        "probability": {"type": "number"},
                    },
                    "required": ["segments", "probability"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["responses"],
        "additionalProperties": False,
    }
