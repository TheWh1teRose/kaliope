"""Node cache keys (§7.2).

    node_key = sha256(name | version | canonical_json(config) | model_id |
                      sorted(input_artifact_hashes))

Because a node's inputs are identified by artifact hash, changing one node's
config invalidates that node and everything downstream of it — and nothing else
(AC-FW-2). Nothing here needs to know the flow's shape to get that right.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.pipeline.framework.artifacts import canonical_json


def node_key(
    *,
    name: str,
    version: str,
    config: dict[str, Any],
    model_id: str | None,
    input_hashes: list[str],
) -> str:
    parts = [
        name,
        version,
        canonical_json(config),
        model_id or "",
        canonical_json(sorted(input_hashes)),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
