"""Model registry: id → provider, pricing and capabilities (§7.4).

Pricing lives here so the run manifest can report cost in USD. Capabilities
live here too, because provider APIs differ in ways a node must not have to
know about: current frontier models reject ``temperature`` outright, while the
cheap models used for classification still accept it. The client adapts the
request and records a warning rather than letting a node crash on a 400.

An unknown model id is usable — the provider is inferred from its prefix and
the cost is recorded as 0 with a loud warning — so that adding a model is a
config change, not a code change.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel

from app.llm.base import Provider, Usage

logger = logging.getLogger(__name__)

ProviderName = Literal["anthropic", "openai", "google"]


class ModelSpec(BaseModel):
    id: str
    provider: ProviderName
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    #: Multipliers applied to the input rate for cached tokens.
    cache_read_multiplier: float = 0.1
    cache_write_multiplier: float = 1.25
    context_window: int = 200_000
    max_output_tokens: int = 16_000
    #: ``False`` for models whose API rejects temperature/top_p/top_k.
    supports_sampling: bool = True
    #: Native JSON-schema-constrained output.
    supports_structured_outputs: bool = True
    supports_prompt_caching: bool = True
    #: Suitable as a cheap, fast classifier (§5.6 ``ZONE_MODEL``).
    small: bool = False


MODELS: dict[str, ModelSpec] = {
    spec.id: spec
    for spec in [
        # --- Anthropic -------------------------------------------------
        ModelSpec(
            id="claude-opus-5",
            provider="anthropic",
            input_usd_per_mtok=5.0,
            output_usd_per_mtok=25.0,
            context_window=1_000_000,
            max_output_tokens=64_000,
            supports_sampling=False,
        ),
        ModelSpec(
            id="claude-opus-4-8",
            provider="anthropic",
            input_usd_per_mtok=5.0,
            output_usd_per_mtok=25.0,
            context_window=1_000_000,
            max_output_tokens=64_000,
            supports_sampling=False,
        ),
        ModelSpec(
            id="claude-opus-4-6",
            provider="anthropic",
            input_usd_per_mtok=5.0,
            output_usd_per_mtok=25.0,
            context_window=1_000_000,
            max_output_tokens=64_000,
        ),
        ModelSpec(
            id="claude-sonnet-5",
            provider="anthropic",
            input_usd_per_mtok=3.0,
            output_usd_per_mtok=15.0,
            context_window=1_000_000,
            max_output_tokens=64_000,
            supports_sampling=False,
        ),
        ModelSpec(
            id="claude-sonnet-4-6",
            provider="anthropic",
            input_usd_per_mtok=3.0,
            output_usd_per_mtok=15.0,
            context_window=1_000_000,
            max_output_tokens=64_000,
        ),
        ModelSpec(
            id="claude-haiku-4-5",
            provider="anthropic",
            input_usd_per_mtok=1.0,
            output_usd_per_mtok=5.0,
            context_window=200_000,
            max_output_tokens=32_000,
            small=True,
        ),
        ModelSpec(
            id="claude-haiku-4-5-20251001",
            provider="anthropic",
            input_usd_per_mtok=1.0,
            output_usd_per_mtok=5.0,
            context_window=200_000,
            max_output_tokens=32_000,
            small=True,
        ),
        # --- OpenAI ----------------------------------------------------
        ModelSpec(
            id="gpt-4.1",
            provider="openai",
            input_usd_per_mtok=2.0,
            output_usd_per_mtok=8.0,
            context_window=1_000_000,
            max_output_tokens=32_000,
        ),
        ModelSpec(
            id="gpt-4.1-mini",
            provider="openai",
            input_usd_per_mtok=0.4,
            output_usd_per_mtok=1.6,
            context_window=1_000_000,
            max_output_tokens=32_000,
            small=True,
        ),
        # --- Google ----------------------------------------------------
        ModelSpec(
            id="gemini-2.5-pro",
            provider="google",
            input_usd_per_mtok=1.25,
            output_usd_per_mtok=10.0,
            context_window=1_000_000,
            max_output_tokens=64_000,
        ),
        ModelSpec(
            id="gemini-2.5-flash",
            provider="google",
            input_usd_per_mtok=0.3,
            output_usd_per_mtok=2.5,
            context_window=1_000_000,
            max_output_tokens=64_000,
            small=True,
        ),
    ]
}

#: Fallback classifier when ``ZONE_MODEL`` is unset.
DEFAULT_SMALL_MODEL = "claude-haiku-4-5"

_PROVIDER_PREFIXES: tuple[tuple[str, ProviderName], ...] = (
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("gemini", "google"),
)


def spec_for(model_id: str) -> ModelSpec | None:
    return MODELS.get(model_id)


def provider_name_for(model_id: str) -> ProviderName:
    spec = MODELS.get(model_id)
    if spec is not None:
        return spec.provider
    for prefix, provider in _PROVIDER_PREFIXES:
        if model_id.startswith(prefix):
            logger.warning(
                "model '%s' is not in the registry; inferring provider '%s' from its prefix. "
                "Cost for this model will be recorded as 0.",
                model_id,
                provider,
            )
            return provider
    raise KeyError(
        f"cannot determine a provider for model '{model_id}'. "
        f"Add it to app/llm/registry.py or use one of: {', '.join(sorted(MODELS))}"
    )


def cost_usd(model_id: str, usage: Usage) -> float:
    """Cost of one call. Unknown models cost 0 — and say so at registration."""
    spec = MODELS.get(model_id)
    if spec is None:
        return 0.0
    per_token_in = spec.input_usd_per_mtok / 1_000_000
    per_token_out = spec.output_usd_per_mtok / 1_000_000
    total = usage.input_tokens * per_token_in
    total += usage.output_tokens * per_token_out
    total += usage.cache_read_tokens * per_token_in * spec.cache_read_multiplier
    total += usage.cache_write_tokens * per_token_in * spec.cache_write_multiplier
    return round(total, 8)


def max_output_for(model_id: str, requested: int) -> int:
    spec = MODELS.get(model_id)
    if spec is None:
        return requested
    return min(requested, spec.max_output_tokens)


def supports_sampling(model_id: str) -> bool:
    spec = MODELS.get(model_id)
    return spec.supports_sampling if spec else True


def supports_structured_outputs(model_id: str) -> bool:
    spec = MODELS.get(model_id)
    return spec.supports_structured_outputs if spec else False


def catalogue() -> list[dict[str, object]]:
    return [
        {
            "id": s.id,
            "provider": s.provider,
            "input_usd_per_mtok": s.input_usd_per_mtok,
            "output_usd_per_mtok": s.output_usd_per_mtok,
            "context_window": s.context_window,
            "small": s.small,
        }
        for s in MODELS.values()
    ]


# --------------------------------------------------------------- providers

_providers: dict[ProviderName, Provider] = {}


def register_provider(name: ProviderName, provider: Provider) -> None:
    _providers[name] = provider


def reset_providers() -> None:
    _providers.clear()


def resolve_provider(model_id: str) -> Provider:
    name = provider_name_for(model_id)
    provider = _providers.get(name)
    if provider is None:
        provider = _build_provider(name)
        _providers[name] = provider
    if not provider.available():
        raise KeyError(
            f"provider '{name}' is not configured. Set the corresponding API key "
            f"(see .env.example) to use model '{model_id}'."
        )
    return provider


def _build_provider(name: ProviderName) -> Provider:
    if name == "anthropic":
        from app.llm.anthropic import AnthropicProvider

        return AnthropicProvider()
    if name == "openai":
        from app.llm.openai import OpenAIProvider

        return OpenAIProvider()
    from app.llm.google import GoogleProvider

    return GoogleProvider()
