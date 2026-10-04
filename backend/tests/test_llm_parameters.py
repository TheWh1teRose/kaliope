"""Model parameters per model: top_p, top_k, thinking and effort (slice 0b).

The providers are driven through fake SDK clients that record what would be
sent. The Anthropic fake binds every call against the installed SDK's real
``Messages.create`` signature, so a keyword SDK 1.x no longer accepts (such as
``temperature``) fails here the way it fails in production.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import session_scope
from app.llm import registry
from app.llm.anthropic import AnthropicProvider
from app.llm.base import CompletionRequest, LLMError, Message
from app.llm.google import GoogleProvider
from app.llm.openai import OpenAIProvider
from app.main import create_app
from app.models import User
from app.security import hash_password


def _request(model: str, **params: Any) -> CompletionRequest:
    return CompletionRequest(
        model=model, messages=[Message(role="user", content="Hallo")], **params
    )


# ------------------------------------------------------------ fake clients


class _FakeAnthropic:
    def __init__(self) -> None:
        import anthropic

        self._signature = inspect.signature(anthropic.resources.Messages.create)
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        # Raises TypeError for keywords the installed SDK does not take.
        self._signature.bind(None, **kwargs)
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            stop_reason="end_turn",
            model=kwargs["model"],
            usage=SimpleNamespace(input_tokens=3, output_tokens=1),
        )


class _FakeOpenAI:
    def __init__(self, usage: Any = None) -> None:
        import openai

        self._signature = inspect.signature(openai.resources.chat.Completions.create)
        self._usage = usage
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        # Raises TypeError for keywords the installed SDK does not take.
        self._signature.bind(None, **kwargs)
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
            model=kwargs["model"],
            usage=self._usage,
        )


class _FakeGoogle:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, *, model: str, contents: Any, config: dict[str, Any]) -> Any:
        from google.genai import types

        # Rejects a misspelt or unknown config field.
        types.GenerateContentConfig(**config)
        self.calls.append(config)
        return SimpleNamespace(text="ok", usage_metadata=None)


@pytest.fixture
def anthropic_fake() -> tuple[AnthropicProvider, _FakeAnthropic]:
    provider = AnthropicProvider()
    fake = _FakeAnthropic()
    provider._client = fake
    return provider, fake


# --------------------------------------------------------------- anthropic


def test_sampling_goes_in_extra_body_on_models_that_sample(anthropic_fake: Any) -> None:
    provider, fake = anthropic_fake
    completion = provider.complete(
        _request("claude-sonnet-4-6", temperature=0.7, top_p=0.9, top_k=40)
    )
    sent = fake.calls[-1]
    assert sent["extra_body"] == {"temperature": 0.7, "top_p": 0.9, "top_k": 40}
    assert not {"temperature", "top_p", "top_k", "thinking", "output_config"} & set(sent)
    assert completion.warnings == []


def test_sampling_is_dropped_with_a_warning_where_rejected(anthropic_fake: Any) -> None:
    provider, fake = anthropic_fake
    completion = provider.complete(_request("claude-opus-5", temperature=0.7))
    assert "extra_body" not in fake.calls[-1]
    assert any("does not accept a temperature" in w for w in completion.warnings)


def test_adaptive_thinking_drops_temperature_and_top_k(anthropic_fake: Any) -> None:
    provider, fake = anthropic_fake
    completion = provider.complete(
        _request("claude-sonnet-4-6", thinking="adaptive", temperature=0.5, top_k=10)
    )
    sent = fake.calls[-1]
    assert sent["thinking"] == {"type": "adaptive"}
    assert "extra_body" not in sent
    assert any("is thinking" in w for w in completion.warnings)


def test_thinking_off_is_refused_above_the_allowed_effort(anthropic_fake: Any) -> None:
    provider, fake = anthropic_fake
    with pytest.raises(LLMError, match="thinking off up to effort 'high'"):
        provider.complete(_request("claude-opus-5", thinking="off", effort="max"))
    assert fake.calls == []

    provider.complete(_request("claude-opus-5", thinking="off", effort="high"))
    assert fake.calls[-1]["thinking"] == {"type": "disabled"}
    assert fake.calls[-1]["output_config"] == {"effort": "high"}


def test_effort_and_schema_share_one_output_config(anthropic_fake: Any) -> None:
    provider, fake = anthropic_fake
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    provider.complete(_request("claude-opus-5", effort="low", json_schema=schema))
    assert fake.calls[-1]["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": schema},
    }


def test_haiku_has_no_effort_and_a_minimum_thinking_budget(anthropic_fake: Any) -> None:
    provider, fake = anthropic_fake
    completion = provider.complete(_request("claude-haiku-4-5", effort="high"))
    assert "output_config" not in fake.calls[-1]
    assert any("does not accept an effort" in w for w in completion.warnings)

    with pytest.raises(LLMError, match="at least 1024"):
        provider.complete(_request("claude-haiku-4-5", thinking="budget", thinking_budget=512))

    provider.complete(_request("claude-haiku-4-5", thinking="budget", thinking_budget=2048))
    assert fake.calls[-1]["thinking"] == {"type": "enabled", "budget_tokens": 2048}


def test_thinking_budget_must_stay_below_max_tokens(anthropic_fake: Any) -> None:
    provider, _fake = anthropic_fake
    with pytest.raises(LLMError, match="smaller than max_tokens"):
        provider.complete(
            _request("claude-haiku-4-5", thinking="budget", thinking_budget=4000, max_tokens=4000)
        )


def test_unsupported_thinking_mode_and_effort_level_fall_back(anthropic_fake: Any) -> None:
    provider, fake = anthropic_fake
    completion = provider.complete(_request("claude-opus-4-6", thinking="budget", effort="xhigh"))
    sent = fake.calls[-1]
    assert "thinking" not in sent
    assert "output_config" not in sent
    assert len(completion.warnings) == 2


def test_production_requests_send_nothing_new(anthropic_fake: Any) -> None:
    provider, fake = anthropic_fake
    provider.complete(_request("claude-opus-5"))
    assert not {"extra_body", "thinking", "output_config"} & set(fake.calls[-1])


# ------------------------------------------------------------ openai/google


def test_openai_sends_temperature_and_top_p_only() -> None:
    provider = OpenAIProvider()
    fake = _FakeOpenAI()
    provider._client = fake
    completion = provider.complete(
        _request("gpt-4.1", temperature=0.3, top_p=0.8, top_k=5, thinking="adaptive", effort="low")
    )
    sent = fake.calls[-1]
    assert sent["temperature"] == 0.3
    assert sent["top_p"] == 0.8
    assert not {"top_k", "thinking", "reasoning_effort"} & set(sent)
    assert len(completion.warnings) == 3


def _openai(usage: Any = None) -> tuple[OpenAIProvider, _FakeOpenAI]:
    provider = OpenAIProvider()
    fake = _FakeOpenAI(usage)
    provider._client = fake
    return provider, fake


def test_openai_sends_reasoning_effort_and_none_for_thinking_off() -> None:
    provider, fake = _openai()
    completion = provider.complete(_request("gpt-6-sol", effort="max"))
    assert fake.calls[-1]["reasoning_effort"] == "max"
    assert completion.warnings == []

    completion = provider.complete(_request("gpt-6-luna", thinking="off", effort="high"))
    assert fake.calls[-1]["reasoning_effort"] == "none"
    assert any("effort 'high' was not sent" in warning for warning in completion.warnings)

    provider.complete(_request("gpt-5.6-terra"))
    assert "reasoning_effort" not in fake.calls[-1]


def test_openai_samples_only_with_thinking_off() -> None:
    provider, fake = _openai()
    completion = provider.complete(_request("gpt-5.6-sol", temperature=0.3, top_p=0.8))
    assert not {"temperature", "top_p"} & set(fake.calls[-1])
    assert any("thinking" in warning for warning in completion.warnings)

    provider.complete(_request("gpt-5.6-sol", thinking="off", temperature=0.3, top_p=0.8))
    sent = fake.calls[-1]
    assert (sent["temperature"], sent["top_p"], sent["reasoning_effort"]) == (0.3, 0.8, "none")


@pytest.mark.parametrize("model_id", ["gpt-6-astra", "gpt-6.1-sol"])
def test_openai_models_without_none_effort_keep_thinking(model_id: str) -> None:
    provider, fake = _openai()
    completion = provider.complete(_request(model_id, thinking="off", temperature=0.2))
    sent = fake.calls[-1]
    assert not {"reasoning_effort", "temperature"} & set(sent)
    assert any("thinking" in warning for warning in completion.warnings)
    assert any("temperature" in warning for warning in completion.warnings)


def test_openai_drops_an_effort_the_model_lacks() -> None:
    provider, fake = _openai()
    completion = provider.complete(_request("gpt-5.5", effort="max"))
    assert "reasoning_effort" not in fake.calls[-1]
    assert any("effort 'max'" in warning for warning in completion.warnings)


def test_openai_usage_separates_cache_reads_and_writes() -> None:
    usage = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=50,
        prompt_tokens_details=SimpleNamespace(cached_tokens=600, cache_write_tokens=300),
    )
    provider, _fake = _openai(usage)
    completion = provider.complete(_request("gpt-6-astra"))
    assert completion.usage.input_tokens == 100
    assert completion.usage.cache_read_tokens == 600
    assert completion.usage.cache_write_tokens == 300
    assert completion.usage.output_tokens == 50


def test_google_maps_sampling_and_thinking_budget() -> None:
    provider = GoogleProvider()
    fake = _FakeGoogle()
    provider._client = fake

    provider.complete(
        _request(
            "gemini-2.5-flash",
            temperature=0.4,
            top_p=0.9,
            top_k=20,
            thinking="budget",
            thinking_budget=512,
        )
    )
    sent = fake.calls[-1]
    assert (sent["temperature"], sent["top_p"], sent["top_k"]) == (0.4, 0.9, 20)
    assert sent["thinking_config"] == {"thinking_budget": 512}

    provider.complete(_request("gemini-2.5-flash", thinking="off"))
    assert fake.calls[-1]["thinking_config"] == {"thinking_budget": 0}

    completion = provider.complete(_request("gemini-2.5-pro", thinking="off", effort="high"))
    assert "thinking_config" not in fake.calls[-1]
    assert len(completion.warnings) == 2


def test_traces_record_what_was_sent() -> None:
    from app.llm.base import LLMClient
    from app.schemas.bench import LLMTraceOut

    provider = AnthropicProvider()
    provider._client = _FakeAnthropic()
    client = LLMClient(resolve=lambda _model: provider, cost_of=registry.cost_usd)
    client.complete(_request("claude-sonnet-4-6", top_p=0.9, thinking="off", effort="medium"))
    trace = client.traces[-1]
    assert (trace["top_p"], trace["thinking"], trace["effort"]) == (0.9, "off", "medium")
    published = LLMTraceOut.model_validate(trace)
    assert (published.top_p, published.thinking, published.effort) == (0.9, "off", "medium")

    client.complete(_request("claude-opus-5", temperature=0.7, top_p=0.5))
    dropped = client.traces[-1]
    assert (dropped["temperature"], dropped["top_p"]) == (None, None)
    assert LLMTraceOut.model_validate(dropped).temperature is None


# ---------------------------------------------------------------- registry


@pytest.mark.parametrize("spec", list(registry.MODELS.values()), ids=lambda s: s.id)
def test_every_model_declares_consistent_capabilities(spec: registry.ModelSpec) -> None:
    assert spec.id in registry._CAPABILITIES, f"add '{spec.id}' to registry._CAPABILITIES"
    if spec.supports_top_k:
        assert spec.supports_sampling
    assert "default" not in spec.thinking_modes
    if not spec.effort_levels:
        assert spec.default_effort is None
    elif spec.default_effort is None:
        # Only where the provider does not document the default.
        assert spec.id == "gpt-6-astra"
    if spec.default_effort is not None:
        assert spec.default_effort in spec.effort_levels
    if spec.thinking_off_max_effort is not None:
        assert "off" in spec.thinking_modes
    if spec.provider == "openai":
        # Reasoning effort is the only thinking control; "off" sends "none".
        assert not spec.supports_top_k
        assert set(spec.thinking_modes) <= {"off"}
        if spec.thinking_modes:
            assert spec.supports_sampling


@pytest.mark.parametrize(
    "model_id",
    ["claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5", "claude-fable-5-1"],
)
def test_unpriced_models_cannot_disable_thinking_or_sample(
    anthropic_fake: Any, model_id: str
) -> None:
    provider, fake = anthropic_fake
    completion = provider.complete(_request(model_id, thinking="off", temperature=0.2))
    sent = fake.calls[-1]
    assert "thinking" not in sent
    assert "extra_body" not in sent
    assert any("thinking" in warning for warning in completion.warnings)
    assert any("temperature" in warning or "sampling" in warning for warning in completion.warnings)


def test_unpriced_opus_4_7_rejects_sampling_but_allows_thinking_off(anthropic_fake: Any) -> None:
    provider, fake = anthropic_fake
    completion = provider.complete(_request("claude-opus-4-7", thinking="off", temperature=0.2))
    sent = fake.calls[-1]
    assert sent["thinking"] == {"type": "disabled"}
    assert "extra_body" not in sent
    assert any("temperature" in warning or "sampling" in warning for warning in completion.warnings)


def test_temperature_outside_the_provider_range_is_rejected_before_the_call(
    anthropic_fake: Any,
) -> None:
    provider, fake = anthropic_fake
    with pytest.raises(LLMError, match="temperature"):
        provider.complete(_request("claude-sonnet-4-6", temperature=1.5))
    assert fake.calls == []
    provider.complete(_request("claude-haiku-4-5", temperature=1))
    assert fake.calls[-1]["extra_body"]["temperature"] == 1

    openai = OpenAIProvider()
    openai_fake = _FakeOpenAI()
    openai._client = openai_fake
    with pytest.raises(LLMError, match="temperature"):
        openai.complete(_request("gpt-4.1", temperature=3))
    assert openai_fake.calls == []
    openai.complete(_request("gpt-4.1", temperature=2))
    assert openai_fake.calls[-1]["temperature"] == 2

    google = GoogleProvider()
    google_fake = _FakeGoogle()
    google._client = google_fake
    with pytest.raises(LLMError, match="temperature"):
        google.complete(_request("gemini-2.5-flash", temperature=2.1))
    assert google_fake.calls == []


def test_models_that_cannot_stop_thinking_say_so() -> None:
    for model_id in ("claude-opus-5", "claude-sonnet-5"):
        assert registry.MODELS[model_id].thinking_default == "on"
    assert registry.MODELS["claude-opus-4-8"].thinking_default == "off"
    for model_id in (
        "claude-fable-5-1",
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "gpt-6-astra",
        "gpt-6.1-sol",
    ):
        assert "off" not in registry._CAPABILITIES[model_id].get("thinking_modes", [])  # type: ignore[operator]


def test_catalogue_exposes_capabilities() -> None:
    entry = next(m for m in registry.catalogue() if m["id"] == "claude-haiku-4-5")
    assert entry["max_output_tokens"] == registry.MODELS["claude-haiku-4-5"].max_output_tokens
    assert entry["supports_sampling"] is True
    assert entry["thinking_modes"] == ["budget"]
    assert entry["effort_levels"] == []


# --------------------------------------------------------------------- API

EMAIL = "models@kalliope.test"
PASSWORD = "models-password-1"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def test_model_catalogue_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/models").status_code == 401


def test_model_catalogue_lists_models_and_providers(client: TestClient) -> None:
    with session_scope() as session:
        session.add(
            User(email=EMAIL, name="Models", password_hash=hash_password(PASSWORD), role="admin")
        )
    assert client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}).is_success

    body = client.get("/api/models").json()
    assert {m["id"] for m in body["models"]} == set(registry.MODELS)
    opus = next(m for m in body["models"] if m["id"] == "claude-opus-5")
    assert opus["thinking_default"] == "on"
    assert opus["thinking_off_max_effort"] == "high"
    assert {p["name"] for p in body["providers"]} == {"anthropic", "openai", "google"}


def test_disabled_cap_on_google_uses_the_catalogue_model_limit() -> None:
    provider = GoogleProvider()
    fake = _FakeGoogle()
    provider._client = fake
    provider.complete(_request("gemini-2.5-pro", max_tokens=0))
    assert fake.calls[0]["max_output_tokens"] == registry.MODELS["gemini-2.5-pro"].max_output_tokens


def test_disabled_required_cap_needs_a_known_model_limit() -> None:
    with pytest.raises(LLMError, match="set max_tokens explicitly"):
        registry.max_output_for("claude-unknown", 0)
    assert registry.max_output_for("claude-unknown", 42) == 42


def test_disabled_openai_cap_omits_optional_sdk_argument() -> None:
    provider = OpenAIProvider()
    fake = _FakeOpenAI()
    provider._client = fake
    provider.complete(_request("gpt-6.1-sol", max_tokens=0))
    assert "max_completion_tokens" not in fake.calls[0]
