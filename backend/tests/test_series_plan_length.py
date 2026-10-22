"""Series planning on an OpenAI reasoning model, through the real node and adapter.

The provider SDK is stubbed; reasoning tokens count against
``max_completion_tokens``, so a cap the answer fits in can still leave no text.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.llm.base import CompletionRequest, LLMClient, LLMError, Message
from app.llm.openai import OpenAIProvider
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.node import NodeContext
from app.pipeline.framework.spec import node_params
from app.pipeline.nodes.series_plan import SeriesPlanInput, SeriesPlanNode
from app.schemas.pipeline import SeriesRequest
from tests.series_support import AUDIENCE, FORMAT
from tests.support import StubProvider
from tests.test_series_plan import DOC, _budget

#: Reasoning plus the plan, in this fixture; the plan alone is a few thousand.
TOTAL_TOKENS_NEEDED = 40_000


class ReasoningSDK:
    """Answers like a reasoning model whose thinking uses the completion budget."""

    def __init__(self, needed: int = TOTAL_TOKENS_NEEDED) -> None:
        self.calls: list[dict[str, Any]] = []
        self.stub = StubProvider()
        self.needed = needed
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        request = CompletionRequest(
            model=kwargs["model"],
            system=kwargs["messages"][0]["content"],
            messages=[Message(**m) for m in kwargs["messages"][1:]],
        )
        cutoff = kwargs.get("max_completion_tokens", 128_000) < self.needed
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="" if cutoff else self.stub.complete(request).text
                    ),
                    finish_reason="length" if cutoff else "stop",
                )
            ],
            model=kwargs["model"],
            usage=SimpleNamespace(prompt_tokens=3000, completion_tokens=self.needed),
        )


def _run(tmp_path: Path, sdk: ReasoningSDK, config: dict[str, Any] | None = None) -> Any:
    provider = OpenAIProvider()
    provider._client = sdk
    ctx = NodeContext(
        run_id="r",
        llm=LLMClient(resolve=lambda _name: provider, cost_of=lambda _m, _u: 0.0),
        artifacts=ArtifactStore(tmp_path),
        config={"model": "gpt-6.1-sol", "effort": "high", **(config or {})},
        logger=logging.getLogger("test"),
    )
    return SeriesPlanNode().run(
        SeriesPlanInput(
            parsed=DOC,
            budget=_budget(),
            format_spec=FORMAT,
            audience_spec=AUDIENCE,
            series_request=SeriesRequest(minutes_per_episode=4),
        ),
        ctx,
    )


def test_reasoning_tokens_do_not_starve_the_series_plan(tmp_path: Path) -> None:
    sdk = ReasoningSDK()
    plan = _run(tmp_path, sdk)
    assert plan.episodes
    assert len(sdk.calls) == 1
    assert "max_completion_tokens" not in sdk.calls[0]


def test_an_explicit_cap_is_still_sent(tmp_path: Path) -> None:
    sdk = ReasoningSDK(needed=1000)
    _run(tmp_path, sdk, {"max_tokens": 20_000})
    assert sdk.calls[0]["max_completion_tokens"] == 20_000


def test_a_cap_below_what_reasoning_needs_names_the_length_stop(tmp_path: Path) -> None:
    with pytest.raises(LLMError) as caught:
        _run(tmp_path, ReasoningSDK(), {"max_tokens": 16_000})
    assert str(caught.value) == "empty completion (finish_reason=length)"


def test_the_planner_cap_defaults_off_without_an_editor_switch() -> None:
    (param,) = [p for p in node_params(SeriesPlanNode()) if p.key == "max_tokens"]
    assert param.default == 0
    assert param.minimum != 0
