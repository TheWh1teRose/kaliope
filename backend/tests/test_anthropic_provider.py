"""Anthropic request-shape regressions for SDK 1.x.

``messages.create`` rejects ``temperature`` / ``top_p`` / ``top_k`` as keyword
arguments and refuses a non-streaming call once ``max_tokens`` would exceed the
10-minute timeout budget (the first failing value is 21334). These tests drive
:class:`AnthropicProvider` through a fake client with that same contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.llm.anthropic import AnthropicProvider
from app.llm.base import CompletionRequest, Message

# Keyword arguments Messages.create/stream accept in anthropic 1.x. Sampling
# parameters are absent on purpose: the SDK raises TypeError for them.
_SDK_KWARGS = frozenset(
    {
        "max_tokens",
        "messages",
        "model",
        "cache_control",
        "container",
        "diagnostics",
        "inference_geo",
        "metadata",
        "output_config",
        "output_format",
        "service_tier",
        "stop_sequences",
        "stream",
        "system",
        "thinking",
        "tool_choice",
        "tools",
        "user_profile_id",
        "workspace_id",
        "extra_headers",
        "extra_query",
        "extra_body",
        "timeout",
    }
)


class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Usage:
    input_tokens = 5
    output_tokens = 2
    cache_read_input_tokens = 1
    cache_creation_input_tokens = 3


class _Message:
    def __init__(self, model: str) -> None:
        self.content = [_Block("ok")]
        self.stop_reason = "end_turn"
        self.model = model
        self.usage = _Usage()


class _FinalMessage:
    """Context manager returned by ``messages.stream``, like MessageStream."""

    def __init__(self, message: _Message) -> None:
        self._message = message

    def __enter__(self) -> _FinalMessage:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def get_final_message(self) -> _Message:
        return self._message


class _Messages:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Message:
        self._reject_unknown("Messages.create", kwargs)
        self.create_calls.append(dict(kwargs))
        return _Message(str(kwargs.get("model", "")))

    def stream(self, **kwargs: Any) -> _FinalMessage:
        self._reject_unknown("Messages.stream", kwargs)
        self.stream_calls.append(dict(kwargs))
        return _FinalMessage(_Message(str(kwargs.get("model", ""))))

    @staticmethod
    def _reject_unknown(method: str, kwargs: dict[str, Any]) -> None:
        unknown = [name for name in kwargs if name not in _SDK_KWARGS]
        if unknown:
            raise TypeError(f"{method}() got an unexpected keyword argument {unknown[0]!r}")


class _Client:
    def __init__(self) -> None:
        self.messages = _Messages()


def _provider() -> tuple[AnthropicProvider, _Messages]:
    provider = AnthropicProvider()
    client = _Client()
    provider._client = client  # type: ignore[attr-defined]
    return provider, client.messages


def _request(model: str, max_tokens: int, temperature: float | None) -> CompletionRequest:
    return CompletionRequest(
        model=model,
        messages=[Message(role="user", content="label the blocks")],
        system="classify",
        max_tokens=max_tokens,
        temperature=temperature,
        json_schema={"type": "object"},
        cache_system=True,
    )


def test_fake_client_rejects_sampling_keywords_and_accepts_extra_body() -> None:
    messages = _Messages()
    with pytest.raises(TypeError, match="unexpected keyword argument 'temperature'"):
        messages.create(model="claude-haiku-4-5", max_tokens=16, messages=[], temperature=0.0)
    with pytest.raises(TypeError, match="unexpected keyword argument 'top_p'"):
        messages.create(model="claude-haiku-4-5", max_tokens=16, messages=[], top_p=1)
    with pytest.raises(TypeError, match="unexpected keyword argument 'top_k'"):
        messages.stream(model="claude-haiku-4-5", max_tokens=16, messages=[], top_k=10)

    messages.create(
        model="claude-haiku-4-5",
        max_tokens=16,
        messages=[],
        extra_body={"temperature": 0.0, "top_p": 0.9, "top_k": 10},
    )
    assert messages.create_calls[0]["extra_body"] == {
        "temperature": 0.0,
        "top_p": 0.9,
        "top_k": 10,
    }


@pytest.mark.parametrize(
    ("max_tokens", "expect_stream"),
    [
        (8_192, False),
        (21_333, False),
        (21_334, True),
        (64_000, True),
    ],
)
def test_temperature_is_sent_via_extra_body(max_tokens: int, expect_stream: bool) -> None:
    provider, messages = _provider()
    completion = provider.complete(
        _request("claude-opus-4-6", max_tokens=max_tokens, temperature=0.0)
    )

    used = messages.stream_calls if expect_stream else messages.create_calls
    unused = messages.create_calls if expect_stream else messages.stream_calls
    assert len(used) == 1
    assert unused == []
    assert "temperature" not in used[0]
    assert used[0]["extra_body"]["temperature"] == 0.0
    assert used[0]["max_tokens"] == max_tokens
    assert completion.text == "ok"
    assert completion.usage.input_tokens == 5
    assert completion.usage.output_tokens == 2
    assert completion.usage.cache_read_tokens == 1
    assert completion.usage.cache_write_tokens == 3
    assert completion.warnings == []


@pytest.mark.parametrize("max_tokens", [1_000, 64_000])
def test_models_without_sampling_drop_temperature(max_tokens: int) -> None:
    provider, messages = _provider()
    completion = provider.complete(
        _request("claude-opus-5", max_tokens=max_tokens, temperature=0.7)
    )

    calls = messages.create_calls + messages.stream_calls
    assert len(calls) == 1
    assert "temperature" not in calls[0]
    assert "extra_body" not in calls[0]
    assert calls[0]["max_tokens"] == max_tokens
    assert completion.warnings == [
        "model 'claude-opus-5' does not accept a temperature; the request "
        "was sent without one. Determinism comes from artifact caching instead."
    ]


def test_cache_breaks_split_the_message_and_mark_the_last_stable_block() -> None:
    provider, messages = _provider()
    content = "running order\n" + "beat one\n" + "this beat"
    first, second = len("running order\n"), len("running order\nbeat one\n")
    provider.complete(
        CompletionRequest(
            model="claude-opus-5",
            messages=[Message(role="user", content=content, cache_breaks=[first, second])],
            max_tokens=1_000,
        )
    )

    sent = messages.create_calls[0]["messages"][0]
    assert sent["role"] == "user"
    assert [block["text"] for block in sent["content"]] == [
        "running order\n",
        "beat one\n",
        "this beat",
    ]
    assert [("cache_control" in block) for block in sent["content"]] == [False, True, False]
    assert sent["content"][1]["cache_control"] == {"type": "ephemeral"}


def test_a_message_without_cache_breaks_is_sent_as_plain_text() -> None:
    provider, messages = _provider()
    provider.complete(_request("claude-opus-5", max_tokens=1_000, temperature=None))
    assert messages.create_calls[0]["messages"] == [{"role": "user", "content": "label the blocks"}]
