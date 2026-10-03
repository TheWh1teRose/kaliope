"""Node config effort is sent on the model request, or dropped when unsupported."""

from __future__ import annotations

from pathlib import Path

from app.pipeline.framework.artifacts import ArtifactStore
from tests.test_audio_script import ScriptedProvider, _echo, _run, _script


def test_a_configured_effort_is_sent_with_the_request(tmp_path: Path) -> None:
    provider = ScriptedProvider(_echo)
    _run(ArtifactStore(tmp_path), provider, _script(), config={"effort": "low"})
    assert provider.calls
    assert {call.effort for call in provider.calls} == {"low"}


def test_the_default_effort_is_not_sent(tmp_path: Path) -> None:
    provider = ScriptedProvider(_echo)
    _run(ArtifactStore(tmp_path), provider, _script())
    assert provider.calls
    assert all(call.effort is None for call in provider.calls)


def test_an_unsupported_effort_is_reset_before_the_request(tmp_path: Path) -> None:
    provider = ScriptedProvider(_echo)
    _run(
        ArtifactStore(tmp_path),
        provider,
        _script(),
        config={"model": "claude-haiku-4-5", "effort": "high"},
    )
    assert provider.calls
    assert all(call.model == "claude-haiku-4-5" for call in provider.calls)
    assert all(call.effort is None for call in provider.calls)
