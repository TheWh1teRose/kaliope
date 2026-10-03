"""Flows without a series planner behave exactly as before the series feature.

``golden/baseline_prompts.json`` was recorded on the code before the series
inputs existed. Every prompt the baseline nodes send, every node cache key and
every beat key must still match it byte for byte, so existing caches stay valid
and a single-episode run is unchanged.
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
