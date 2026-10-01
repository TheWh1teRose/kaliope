"""Checks a draft gets without a real parse: citations against pasted passages.

The passages arrive by copy-paste from the Werkbank, either as JSON blocks
(``[{"id": …, "text": …}]``, or a parsed document with ``blocks``) or as text
with ``[block-id]`` headers, which is what the script prompt itself shows the
model. Quotes are located with the script node's own ``locate_quote``, so a
quote counts as found here exactly when production would anchor it.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from app.experiments.verbalized_sampling.parse import CandidateSegment
from app.pipeline.nodes.script import locate_quote

_HEADER = re.compile(r"^\s*\[([^\[\]\n]+)\]\s*$", re.MULTILINE)


class CitationCheck(BaseModel):
    cited: int = 0
    located: int = 0
    #: Cited a block id that is not among the passages.
    unknown_block: int = 0
    #: Block exists but the quote was not found in it.
    unlocated: int = 0

    @property
    def ok(self) -> bool:
        return self.cited == self.located


def parse_passages(text: str) -> dict[str, str]:
    """Block id → block text. Empty when no ids can be found."""
    stripped = text.strip()
    if not stripped:
        return {}
    if stripped[0] in "[{":
        try:
            blocks = _blocks_from_json(json.loads(stripped))
        except json.JSONDecodeError:
            blocks = None
        if blocks is not None:
            return blocks

    headers = list(_HEADER.finditer(text))
    passages: dict[str, str] = {}
    for position, header in enumerate(headers):
        end = headers[position + 1].start() if position + 1 < len(headers) else len(text)
        body = text[header.end() : end].strip()
        if body:
            passages[header.group(1).strip()] = body
    return passages


def check_citations(segments: list[CandidateSegment], passages: dict[str, str]) -> CitationCheck:
    check = CitationCheck()
    for segment in segments:
        for citation in segment.citations:
            check.cited += 1
            block = passages.get(citation.block_id)
            if block is None:
                check.unknown_block += 1
            elif locate_quote(block, citation.quote) is None:
                check.unlocated += 1
            else:
                check.located += 1
    return check


def _blocks_from_json(data: Any) -> dict[str, str] | None:
    if isinstance(data, dict) and isinstance(data.get("blocks"), list):
        data = data["blocks"]
    if isinstance(data, dict):
        if all(isinstance(v, str) for v in data.values()):
            return {str(k): v for k, v in data.items() if v.strip()}
        return None
    if not isinstance(data, list):
        return None
    out: dict[str, str] = {}
    for entry in data:
        if isinstance(entry, dict) and entry.get("id") and isinstance(entry.get("text"), str):
            out[str(entry["id"])] = entry["text"]
    return out or None
