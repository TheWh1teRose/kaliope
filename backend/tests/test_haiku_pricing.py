"""Offline regressions for the 2026-10-07 Haiku release and Sonnet cache rates."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from anthropic.types import Usage as AnthropicUsage

from app.config import get_settings
from app.llm import registry
from app.llm.anthropic import AnthropicProvider
from app.llm.base import CompletionRequest, LLMClient, Message, Usage
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.framework.node import NodeContext
from app.pipeline.nodes.ingest import build_classifier
from app.schemas.document import Block


def provider_with_usage(raw: Any, text: str = "ok") -> tuple[AnthropicProvider, list[dict]]:
    calls: list[dict] = []

    def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(
            model=kwargs["model"],
            usage=raw,
            content=[
                SimpleNamespace(type="thinking", thinking="ignored"),
                SimpleNamespace(type="text", text=text),
            ],
            stop_reason="end_turn",
        )

    provider = AnthropicProvider()
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    return provider, calls


@pytest.mark.parametrize(
    ("uncached", "read", "written", "expected"),
    [(1000, 0, 0, 0.012), (1000, 6000, 3000, 0.0201)],
)
def test_sonnet_provider_usage_cost(
    uncached: int, read: int, written: int, expected: float
) -> None:
    raw = AnthropicUsage(
        input_tokens=uncached,
        output_tokens=1000,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=written,
    )
    provider, _ = provider_with_usage(raw)
    client = LLMClient(resolve=lambda _: provider, cost_of=registry.cost_usd)
    result = client.complete(
        CompletionRequest(
            model="claude-sonnet-5-5", messages=[Message(role="user", content="hello")]
        )
    )
    assert result.usage == Usage(
        input_tokens=uncached,
        output_tokens=1000,
        cache_read_tokens=read,
        cache_write_tokens=written,
    )
    assert registry.cost_usd(result.model_id, result.usage) == pytest.approx(expected)
    assert client.total_cost_usd == pytest.approx(expected)


@pytest.mark.parametrize(
    ("uncached", "read", "written", "expected"),
    [
        (100_000, 0, 0, 0.0105),
        (100_001, 0, 0, 0.0525005),
        (1, 100_000, 0, 0.0075005),
        (1, 0, 100_000, 0.0650005),
    ],
)
def test_haiku_prompt_tier_counts_all_input_categories(
    uncached: int, read: int, written: int, expected: float
) -> None:
    raw = SimpleNamespace(
        input_tokens=uncached,
        output_tokens=1000,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=written,
    )
    provider, _ = provider_with_usage(raw)
    client = LLMClient(resolve=lambda _: provider, cost_of=registry.cost_usd)
    completion = client.complete(
        CompletionRequest(
            model="claude-haiku-5-5", messages=[Message(role="user", content="hello")]
        )
    )
    assert registry.cost_usd(completion.model_id, completion.usage) == pytest.approx(expected)
    assert client.total_cost_usd == pytest.approx(expected)
    # Pricing is selected per request, never from a run's accumulated usage.
    client.complete(
        CompletionRequest(
            model="claude-haiku-5-5", messages=[Message(role="user", content="again")]
        )
    )
    assert client.total_cost_usd == pytest.approx(2 * expected)


@pytest.mark.parametrize(
    ("node_model", "env_model", "expected"),
    [
        (None, "", "claude-haiku-5-5"),
        (None, None, "claude-haiku-5-5"),
        (None, "claude-haiku-4-5", "claude-haiku-4-5"),
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5", "claude-haiku-4-5-20251001"),
    ],
)
def test_ingest_classifier_default_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    node_model: str | None,
    env_model: str | None,
    expected: str,
) -> None:
    if env_model is None:
        env_file = Path(__file__).resolve().parents[2] / ".env.example"
        docker_env = dict(
            line.lstrip().split("=", 1)
            for line in env_file.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        env_model = docker_env["ZONE_MODEL"]
        assert env_model == ""
    monkeypatch.setattr(get_settings(), "zone_model", env_model)
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    provider, calls = provider_with_usage(
        SimpleNamespace(input_tokens=100, output_tokens=20),
        '{"labels":[{"block_index":0,"zone":"body","confidence":0.99}]}',
    )
    client = LLMClient(resolve=lambda _: provider, cost_of=registry.cost_usd)
    ctx = NodeContext(
        run_id="classifier-test",
        llm=client,
        artifacts=ArtifactStore(tmp_path / "artifacts"),
        config={"zone_model": node_model},
        logger=logging.getLogger(__name__),
    )
    classifier = build_classifier(ctx)
    block = Block(id="b1", ordinal=0, type="paragraph", text="A paragraph to classify.", page=1)
    result = classifier.classify([block], [], {1: 12.0}, [(600.0, 800.0)])
    assert result.model_calls == 1
    assert result.labels["b1"][1] == 0.99
    assert calls[0]["model"] == expected
    if expected == "claude-haiku-5-5":
        assert "extra_body" not in calls[0]  # Classifier's temperature is adapted away.
        assert "thinking" not in calls[0]  # API default: adaptive thinking.
        assert "format" in calls[0]["output_config"]
    assert classifier.classify([block], [], {1: 12.0}, [(600.0, 800.0)]).cache_hits == 1
    assert len(calls) == 1


def test_haiku_catalogue_and_parameter_adaptation() -> None:
    entry = next(m for m in registry.catalogue() if m["id"] == "claude-haiku-5-5")
    assert registry.MODELS["claude-haiku-5-5"].small is True
    assert registry.MODELS["claude-haiku-5-5"].context_window == 1_000_000
    assert entry["max_output_tokens"] == 128_000
    assert entry["default_effort"] == "medium"
    assert entry["thinking_modes"] == ["adaptive", "off"]
    assert entry["thinking_default"] == "on"
    assert entry["supports_sampling"] is False
    assert entry["effort_levels"] == ["low", "medium", "high", "xhigh", "max"]
    params = registry.adapt_parameters(
        CompletionRequest(model="claude-haiku-5-5", messages=[], thinking="off", effort="high")
    )
    assert params.thinking == "off"
    assert params.effort == "high"
