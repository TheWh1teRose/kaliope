"""The readable text of one collected item, for searching the Sammlung.

``experiment_outputs.text`` is the run's whole answer. A Verbalized Sampling
run has none and keeps every draft in one ``output_json``, so searching either
column would miss a VS draft or match its sibling. This picks the item's own
part of the output and keeps only the words a reader sees.
"""

from __future__ import annotations

from typing import Any

#: Keys that hold bookkeeping, checks or source quotes rather than the answer.
#: The Alembic migration that backfills ``search_text`` keeps a frozen copy.
_SKIP = frozenset(
    {
        "block_id",
        "citation_check",
        "citations",
        "cost",
        "error",
        "flags",
        "id",
        "item",
        "kind",
        "quote",
        "raw",
        "source",
        "stop_reason",
        "variant",
        "warnings",
    }
)


def item_search_text(output_json: dict[str, Any] | None, item: str, text: str | None) -> str:
    if not output_json:
        return text or ""
    scope = _find_item(output_json, item)
    parts: list[str] = []
    if scope is not None:
        _strings(scope, parts)
    else:
        # The raw text repeats a parsed payload as JSON; read it only when
        # nothing was parsed.
        skip = {"text"} if output_json.get("payload") is not None else set()
        _strings({k: v for k, v in output_json.items() if k not in skip}, parts)
    found = "\n".join(part for part in parts if part.strip())
    return found or (text or "")


def _find_item(node: Any, item: str) -> dict[str, Any] | None:
    if isinstance(node, dict):
        if node.get("item") == item:
            return node
        for value in node.values():
            found = _find_item(value, item)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_item(value, item)
            if found is not None:
                return found
    return None


def _strings(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key not in _SKIP:
                _strings(value, out)
    elif isinstance(node, list):
        for value in node:
            _strings(value, out)
