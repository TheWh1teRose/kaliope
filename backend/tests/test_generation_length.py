"""Episode script limits through the real node/client/OpenAI adapter, without inference."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.llm.base import CompletionRequest, LLMClient, LLMError, Message
from app.llm.openai import OpenAIProvider
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.nodes.script import ScriptNode
from tests.support import StubProvider
from tests.test_script_context import _input


class EpisodeSDK:
    """The second beat needs 70k total tokens, mostly reasoning, in this fixture."""

    def __init__(self, *, hard_limit: bool = False, parseable: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.stub = StubProvider()
        self.hard_limit = hard_limit
        self.parseable = parseable
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        request = CompletionRequest(
            model=kwargs["model"],
            system=kwargs["messages"][0]["content"],
            messages=[Message(**m) for m in kwargs["messages"][1:]],
        )
        full = self.stub.complete(request).text
        needed = 70_000 if len(self.calls) == 2 else 1000
        cutoff = self.hard_limit or kwargs.get("max_completion_tokens", 128_000) < needed
        text = full if not cutoff or self.parseable else '{"segments": ['
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=text),
                    finish_reason="length" if cutoff else "stop",
                )
            ],
            model=kwargs["model"],
            usage=SimpleNamespace(prompt_tokens=200, completion_tokens=needed),
        )


def context(tmp_path: Path, sdk: EpisodeSDK, cap: int | None) -> NodeContext:
    provider = OpenAIProvider()
    provider._client = sdk
    config: dict[str, Any] = {"model": "gpt-6.1-sol", "effort": "medium"}
    if cap is not None:
        config["max_tokens"] = cap
    return NodeContext(
        run_id="episode",
        llm=LLMClient(resolve=lambda _: provider, cost_of=lambda _m, _u: 0),
        artifacts=ArtifactStore(tmp_path / "artifacts"),
        config=config,
        logger=logging.getLogger("test"),
    )


def test_existing_maximum_reproduces_the_length_failure(tmp_path: Path) -> None:
    sdk = EpisodeSDK()
    ctx = context(tmp_path, sdk, 64_000)
    with pytest.raises((NodeError, LLMError), match="cut off"):
        ScriptNode().run(_input(), ctx)
    assert len(sdk.calls) == 2
    assert sdk.calls[-1]["max_completion_tokens"] == 64_000
    assert ctx.llm.traces[-1]["response_text"]


def test_disabled_cap_completes_and_reuses_beats(tmp_path: Path) -> None:
    sdk = EpisodeSDK()
    ctx = context(tmp_path, sdk, 0)
    script = ScriptNode().run(_input(), ctx)
    assert {s.beat_id for s in script.segments} == {"beat000", "beat001", "beat002"}
    assert len(sdk.calls) == 3
    assert all("max_completion_tokens" not in call for call in sdk.calls)
    assert all(call["reasoning_effort"] == "medium" for call in sdk.calls)
    assert ScriptNode().run(_input(), ctx) == script
    assert len(sdk.calls) == 3


@pytest.mark.parametrize("cap", [None, 8000, 64_000])
def test_existing_default_and_numeric_settings_are_preserved(
    tmp_path: Path, cap: int | None
) -> None:
    sdk = EpisodeSDK()
    ctx = context(tmp_path, sdk, cap)
    with pytest.raises((NodeError, LLMError)):
        ScriptNode().run(_input(), ctx)
    assert sdk.calls[0]["max_completion_tokens"] == (8000 if cap is None else cap)


@pytest.mark.parametrize("parseable", [False, True])
def test_provider_cutoff_stays_failed_and_keeps_partial_output(
    tmp_path: Path, parseable: bool
) -> None:
    sdk = EpisodeSDK(hard_limit=True, parseable=parseable)
    ctx = context(tmp_path, sdk, 0)
    with pytest.raises(NodeError, match="cut off") as error:
        ScriptNode().run(_input(), ctx)
    digest = str(error.value).split("partial output artifact: ")[1]
    partial = ctx.artifacts.get_raw(digest)
    assert partial["completion"]["text"] == ctx.llm.traces[-1]["response_text"]
    assert partial["completion"]["stop_reason"] == "length"
    assert partial["beat_id"] == "beat000"
    assert len(sdk.calls) == 1  # No implicit retry, continuation or success cache.
    with pytest.raises(NodeError):
        ScriptNode().run(_input(), ctx)
    assert len(sdk.calls) == 2


@pytest.mark.parametrize("cap, status", [(64_000, "failed"), (0, "completed")])
def test_series_episode_runner_reports_the_real_script_outcome(
    tmp_path: Path, cap: int, status: str
) -> None:
    from app.pipeline.framework.registry import Flow, FlowNode, bootstrap_nodes
    from app.pipeline.framework.runner import FlowRunner
    from app.schemas.pipeline import EpisodeSummary, SeriesContext

    bootstrap_nodes()
    sdk = EpisodeSDK()
    ctx = context(tmp_path, sdk, cap)
    inp = _input().model_copy(
        update={
            "series_context": SeriesContext(
                series_title="Photosynthese",
                episode_index=1,
                episodes=[EpisodeSummary(index=1, title="Licht und Pflanzen")],
            )
        }
    )
    runner = FlowRunner(artifacts=ctx.artifacts, llm=ctx.llm)
    flow = Flow(id="episode", version="1", nodes=[FlowNode(node="script", config=ctx.config)])
    seeds = {name: getattr(inp, name) for name in type(inp).model_fields}
    result = runner.execute(flow, "episode-run", seeds)
    assert result.status == status, result.error
    assert result.manifest.nodes[0].config["max_tokens"] == cap
    if status == "failed":
        assert "script" not in result.artifact_hashes
        assert result.error and "partial output artifact" in result.error
        assert len(sdk.calls) == 2
    else:
        assert "script" in result.artifact_hashes
        assert len(result.bag["script"].segments) > 0
        assert len(sdk.calls) == 3
