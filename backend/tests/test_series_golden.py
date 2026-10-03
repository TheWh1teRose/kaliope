"""Flows without a series planner keep a pinned prompt and cache fingerprint.

Dialogue expansion changed the content-budget artifact (higher supportable
minutes, node version 1.1), so cache keys moved once and this file was
re-recorded. An unclamped short target still speaks the same number of words.
Later series work must not move the fingerprint again.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.series_support import fingerprint, run_baseline
from tests.support import StubProvider

GOLDEN = Path(__file__).parent / "golden" / "baseline_prompts.json"


def test_baseline_prompts_and_cache_keys_are_unchanged(tmp_path: Path) -> None:
    provider = StubProvider()
    result = run_baseline(tmp_path, provider)
    assert result.status == "completed", result.error
    assert fingerprint(provider, result, tmp_path) == json.loads(GOLDEN.read_text())
