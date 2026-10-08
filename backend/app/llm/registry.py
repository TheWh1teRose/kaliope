"""Model registry: id → provider, pricing and capabilities (§7.4).

Pricing lives here so the run manifest can report cost in USD. Capabilities
live here too, because provider APIs differ in ways a node must not have to
know about: current Claude models reject ``temperature`` outright, while
older small models still accept it. The client adapts the
request and records a warning rather than letting a node crash on a 400.

An unknown model id is usable — the provider is inferred from its prefix and
the cost is recorded as 0 with a loud warning — so that adding a model is a
config change, not a code change. Providers requiring an output cap need an
explicit positive ``max_tokens`` for unknown models, since no catalogue limit
is available when the app cap is disabled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, get_args

from pydantic import BaseModel

from app.llm.base import CompletionRequest, Effort, LLMError, Provider, ThinkingMode, Usage

logger = logging.getLogger(__name__)

ProviderName = Literal["anthropic", "openai", "google"]


class ModelSpec(BaseModel):
    id: str
    provider: ProviderName
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    #: Multipliers applied to the input rate for cached tokens. Anthropic writes
    #: use the default five-minute ephemeral TTL requested by our provider;
    #: one-hour cache writes are not requested or separately accounted for.
    cache_read_multiplier: float = 0.1
    cache_write_multiplier: float = 1.25
    #: Some models charge higher input AND output rates above this prompt size.
    long_prompt_threshold: int | None = None
    long_prompt_multiplier: float = 1.0
    context_window: int = 200_000
    max_output_tokens: int = 16_000
    #: ``False`` for models whose API rejects temperature/top_p/top_k.
    supports_sampling: bool = True
    #: ``top_k`` on top of temperature/top_p (OpenAI has none).
    supports_top_k: bool = False
    #: Whether temperature/top_p/top_k may be set while the model thinks.
    #: Anthropic requires the defaults then.
    sampling_with_thinking: bool = False
    #: Thinking modes besides ``default`` (which sends nothing).
    thinking_modes: list[ThinkingMode] = []
    #: What ``default`` does: whether the model thinks when nothing is sent.
    thinking_default: Literal["on", "off"] = "off"
    min_thinking_budget: int = 1024
    #: Highest effort at which ``thinking="off"`` is accepted; ``None`` = any.
    thinking_off_max_effort: Effort | None = None
    #: ``output_config.effort`` levels; empty when the model rejects effort.
    effort_levels: list[Effort] = []
    default_effort: Effort | None = None
    #: Native JSON-schema-constrained output.
    supports_structured_outputs: bool = True
    supports_prompt_caching: bool = True
    #: Suitable as a cheap, fast classifier (§5.6 ``ZONE_MODEL``).
    small: bool = False


MODELS: dict[str, ModelSpec] = {
    spec.id: spec
    for spec in [
        # --- Anthropic -------------------------------------------------
        # Prices are USD per million tokens (Claude API reference, 2026-09-25).
        # Frontier ids omit the date suffix and allow 128K output. The dated
        # Haiku id stays so existing references keep resolving.
        ModelSpec(
            id="claude-fable-5-1",
            provider="anthropic",
            input_usd_per_mtok=10.0,
            output_usd_per_mtok=50.0,
            # Cache read is $0.25/MTok, not the usual 0.1× input rate ($1.00).
            cache_read_multiplier=0.025,
            context_window=1_000_000,
            max_output_tokens=128_000,
            supports_sampling=False,
        ),
        ModelSpec(
            id="claude-fable-5",
            provider="anthropic",
            input_usd_per_mtok=10.0,
            output_usd_per_mtok=50.0,
            context_window=1_000_000,
            max_output_tokens=128_000,
            supports_sampling=False,
        ),
        ModelSpec(
            id="claude-opus-5-5",
            provider="anthropic",
            input_usd_per_mtok=4.0,
            output_usd_per_mtok=20.0,
            # Cache read is $0.20/MTok, not the usual 0.1× input rate ($0.40).
            cache_read_multiplier=0.05,
            context_window=1_000_000,
            max_output_tokens=128_000,
            supports_sampling=False,
        ),
        ModelSpec(
            id="claude-opus-5",
            provider="anthropic",
            input_usd_per_mtok=5.0,
            output_usd_per_mtok=25.0,
            context_window=1_000_000,
            max_output_tokens=128_000,
            supports_sampling=False,
        ),
        ModelSpec(
            id="claude-opus-4-8",
            provider="anthropic",
            input_usd_per_mtok=5.0,
            output_usd_per_mtok=25.0,
            context_window=1_000_000,
            max_output_tokens=128_000,
            supports_sampling=False,
        ),
        ModelSpec(
            id="claude-opus-4-7",
            provider="anthropic",
            input_usd_per_mtok=5.0,
            output_usd_per_mtok=25.0,
            context_window=1_000_000,
            max_output_tokens=128_000,
            supports_sampling=False,
        ),
        ModelSpec(
            id="claude-opus-4-6",
            provider="anthropic",
            input_usd_per_mtok=5.0,
            output_usd_per_mtok=25.0,
            context_window=1_000_000,
            max_output_tokens=128_000,
        ),
        ModelSpec(
            id="claude-sonnet-5-5",
            provider="anthropic",
            input_usd_per_mtok=2.0,
            output_usd_per_mtok=10.0,
            # Cache hits fell from $0.20 to $0.10/MTok on 2026-10-07.
            cache_read_multiplier=0.05,
            context_window=1_000_000,
            max_output_tokens=128_000,
            supports_sampling=False,
        ),
        ModelSpec(
            id="claude-sonnet-5",
            provider="anthropic",
            input_usd_per_mtok=2.0,
            output_usd_per_mtok=10.0,
            context_window=1_000_000,
            max_output_tokens=128_000,
            supports_sampling=False,
        ),
        ModelSpec(
            id="claude-sonnet-4-6",
            provider="anthropic",
            input_usd_per_mtok=3.0,
            output_usd_per_mtok=15.0,
            context_window=1_000_000,
            max_output_tokens=128_000,
        ),
        # Claude API model/pricing pages verified 2026-10-08.
        ModelSpec(
            id="claude-haiku-5-5",
            provider="anthropic",
            input_usd_per_mtok=0.10,
            output_usd_per_mtok=0.50,
            long_prompt_threshold=100_000,
            long_prompt_multiplier=5.0,
            context_window=1_000_000,
            max_output_tokens=128_000,
            supports_sampling=False,
            small=True,
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
        # Prices are USD per million tokens for prompts up to 272K input
        # tokens (OpenAI models and pricing pages, 2026-10-02); longer prompts
        # cost 2x input and 1.5x output, which is not modelled. Cached input
        # is 0.1x input unless noted. GPT-6 and GPT-5.6 bill cache writes at
        # 1.25x input; GPT-5.5 and GPT-4.1 have no cache-write price.
        ModelSpec(
            id="gpt-6-astra",
            provider="openai",
            input_usd_per_mtok=10.0,
            output_usd_per_mtok=50.0,
            context_window=1_050_000,
            max_output_tokens=128_000,
            supports_sampling=False,
        ),
        ModelSpec(
            id="gpt-6.1-sol",
            provider="openai",
            input_usd_per_mtok=2.0,
            output_usd_per_mtok=10.0,
            # Cached input is $0.10/MTok, 5% of the input rate.
            cache_read_multiplier=0.05,
            context_window=1_050_000,
            max_output_tokens=128_000,
            supports_sampling=False,
        ),
        ModelSpec(
            id="gpt-6-sol",
            provider="openai",
            input_usd_per_mtok=2.0,
            output_usd_per_mtok=10.0,
            context_window=1_050_000,
            max_output_tokens=128_000,
        ),
        ModelSpec(
            id="gpt-6-luna",
            provider="openai",
            input_usd_per_mtok=0.1,
            output_usd_per_mtok=0.5,
            context_window=1_050_000,
            max_output_tokens=128_000,
            small=True,
        ),
        ModelSpec(
            id="gpt-5.6-sol",
            provider="openai",
            # Promotional price, available at least through 2026-11-21.
            input_usd_per_mtok=4.0,
            output_usd_per_mtok=20.0,
            context_window=1_050_000,
            max_output_tokens=128_000,
        ),
        ModelSpec(
            id="gpt-5.6-terra",
            provider="openai",
            input_usd_per_mtok=2.0,
            output_usd_per_mtok=12.0,
            context_window=1_050_000,
            max_output_tokens=128_000,
        ),
        ModelSpec(
            id="gpt-5.6-luna",
            provider="openai",
            input_usd_per_mtok=0.2,
            output_usd_per_mtok=1.2,
            context_window=1_050_000,
            max_output_tokens=128_000,
            small=True,
        ),
        ModelSpec(
            id="gpt-5.5",
            provider="openai",
            input_usd_per_mtok=5.0,
            output_usd_per_mtok=30.0,
            cache_write_multiplier=1.0,
            context_window=1_050_000,
            max_output_tokens=128_000,
        ),
        ModelSpec(
            id="gpt-4.1",
            provider="openai",
            input_usd_per_mtok=2.0,
            output_usd_per_mtok=8.0,
            cache_write_multiplier=1.0,
            context_window=1_000_000,
            max_output_tokens=32_000,
        ),
        ModelSpec(
            id="gpt-4.1-mini",
            provider="openai",
            input_usd_per_mtok=0.4,
            output_usd_per_mtok=1.6,
            cache_write_multiplier=1.0,
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

_EFFORT_ALL: list[Effort] = ["low", "medium", "high", "xhigh", "max"]
_EFFORT_46: list[Effort] = ["low", "medium", "high", "max"]
_EFFORT_NO_MAX: list[Effort] = ["low", "medium", "high", "xhigh"]

#: Request parameters per model beyond sampling (Claude API reference, cached
#: 2026-09-25). Kept apart from the price table so model and price updates do
#: not touch it. A row applies even when the id is not in ``MODELS`` yet.
_CAPABILITIES: dict[str, dict[str, object]] = {
    # Thinking is always on; it cannot be disabled.
    **{
        model_id: {
            "supports_sampling": False,
            "thinking_modes": ["adaptive"],
            "thinking_default": "on",
            "effort_levels": _EFFORT_ALL,
            "default_effort": "high",
        }
        for model_id in ("claude-fable-5-1", "claude-fable-5", "claude-sonnet-5-5")
    },
    "claude-haiku-5-5": {
        "supports_sampling": False,
        "thinking_modes": ["adaptive", "off"],
        "thinking_default": "on",
        "thinking_off_max_effort": "high",
        "effort_levels": _EFFORT_ALL,
        "default_effort": "medium",
    },
    "claude-opus-5-5": {
        "supports_sampling": False,
        "thinking_modes": ["adaptive"],
        "thinking_default": "on",
        "effort_levels": _EFFORT_ALL,
        "default_effort": "medium",
    },
    "claude-opus-5": {
        "supports_sampling": False,
        "thinking_modes": ["adaptive", "off"],
        "thinking_default": "on",
        "thinking_off_max_effort": "high",
        "effort_levels": _EFFORT_ALL,
        "default_effort": "high",
    },
    "claude-sonnet-5": {
        "supports_sampling": False,
        "thinking_modes": ["adaptive", "off"],
        "thinking_default": "on",
        "effort_levels": _EFFORT_ALL,
        "default_effort": "high",
    },
    **{
        model_id: {
            "supports_sampling": False,
            "thinking_modes": ["adaptive", "off"],
            "effort_levels": _EFFORT_ALL,
            "default_effort": "high",
        }
        for model_id in ("claude-opus-4-8", "claude-opus-4-7")
    },
    **{
        model_id: {
            "supports_top_k": True,
            "thinking_modes": ["adaptive", "off"],
            "effort_levels": _EFFORT_46,
            "default_effort": "high",
        }
        for model_id in ("claude-opus-4-6", "claude-sonnet-4-6")
    },
    **{
        model_id: {"supports_top_k": True, "thinking_modes": ["budget"]}
        for model_id in ("claude-haiku-4-5", "claude-haiku-4-5-20251001")
    },
    # OpenAI reasoning models (OpenAI model pages and GPT-6 guide, 2026-10-02).
    # ``reasoning_effort`` is the only thinking control: thinking "off" sends
    # ``none``. Temperature and top_p are accepted only at ``none``; OpenAI
    # has no top_k. GPT-6 Astra and GPT-6.1 Sol reject ``none``. Astra's
    # default effort is not documented.
    "gpt-6-astra": {
        "supports_sampling": False,
        "thinking_default": "on",
        "effort_levels": _EFFORT_ALL,
    },
    "gpt-6.1-sol": {
        "supports_sampling": False,
        "thinking_default": "on",
        "effort_levels": _EFFORT_ALL,
        "default_effort": "medium",
    },
    **{
        model_id: {
            "thinking_modes": ["off"],
            "thinking_default": "on",
            "effort_levels": _EFFORT_ALL,
            "default_effort": "medium",
        }
        for model_id in ("gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
    },
    "gpt-5.5": {
        "thinking_modes": ["off"],
        "thinking_default": "on",
        "effort_levels": _EFFORT_NO_MAX,
        "default_effort": "medium",
    },
    # GPT-4.1 does not reason: no top_k, thinking or effort.
    "gpt-4.1": {},
    "gpt-4.1-mini": {},
    # Gemini 2.5 thinks dynamically unless given a budget; only Flash can stop.
    "gemini-2.5-pro": {
        "supports_top_k": True,
        "sampling_with_thinking": True,
        "thinking_modes": ["budget"],
        "thinking_default": "on",
        "min_thinking_budget": 128,
    },
    "gemini-2.5-flash": {
        "supports_top_k": True,
        "sampling_with_thinking": True,
        "thinking_modes": ["off", "budget"],
        "thinking_default": "on",
        "min_thinking_budget": 1,
    },
}

for _model_id, _capabilities in _CAPABILITIES.items():
    if _model_id in MODELS:
        MODELS[_model_id] = MODELS[_model_id].model_copy(update=_capabilities)

#: Fallback classifier when ``ZONE_MODEL`` is unset.
DEFAULT_SMALL_MODEL = "claude-haiku-5-5"

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
    # Anthropic input_tokens excludes cache reads and writes; all three count
    # toward the prompt-length tier. Apply the selected rate to output as well.
    prompt_tokens = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    multiplier = (
        spec.long_prompt_multiplier
        if spec.long_prompt_threshold is not None and prompt_tokens > spec.long_prompt_threshold
        else 1.0
    )
    per_token_in = spec.input_usd_per_mtok * multiplier / 1_000_000
    per_token_out = spec.output_usd_per_mtok * multiplier / 1_000_000
    total = usage.input_tokens * per_token_in
    total += usage.output_tokens * per_token_out
    total += usage.cache_read_tokens * per_token_in * spec.cache_read_multiplier
    total += usage.cache_write_tokens * per_token_in * spec.cache_write_multiplier
    return round(total, 8)


def max_output_for(model_id: str, requested: int) -> int:
    spec = MODELS.get(model_id)
    if requested == 0:
        if spec is None:
            raise LLMError(
                f"no documented output limit for '{model_id}'; set max_tokens explicitly"
            )
        return spec.max_output_tokens
    if spec is None:
        return requested
    return min(requested, spec.max_output_tokens)


def _provider_prefix(model_id: str) -> ProviderName | None:
    for prefix, provider in _PROVIDER_PREFIXES:
        if model_id.startswith(prefix):
            return provider
    return None


def _spec(model_id: str) -> ModelSpec | None:
    """Registered model, or a capability row for an id that is not priced yet."""
    spec = MODELS.get(model_id)
    if spec is not None:
        return spec
    capabilities = _CAPABILITIES.get(model_id)
    if not capabilities:
        return None
    provider = _provider_prefix(model_id)
    if provider is None:
        return None
    return ModelSpec.model_validate(
        {
            "id": model_id,
            "provider": provider,
            "input_usd_per_mtok": 0.0,
            "output_usd_per_mtok": 0.0,
            **capabilities,
        }
    )


def accepted_effort(model_id: str, effort: str | None) -> Effort | None:
    """Effort to send for ``model_id``, or ``None`` for the model's own default.

    An empty value, a value that is not an effort, and an effort the model does
    not list are all ``None``. An id with no capability row is passed through
    when the value is a real effort, matching :func:`adapt_parameters`.
    """
    if not effort or effort not in get_args(Effort):
        return None
    spec = _spec(model_id)
    if spec is not None and effort not in spec.effort_levels:
        return None
    return effort  # type: ignore[return-value]


def supports_sampling(model_id: str) -> bool:
    spec = _spec(model_id)
    return spec.supports_sampling if spec else True


def supports_structured_outputs(model_id: str) -> bool:
    spec = MODELS.get(model_id)
    return spec.supports_structured_outputs if spec else False


@dataclass
class Parameters:
    """The request's optional parameters after adapting them to the model."""

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    thinking: ThinkingMode = "default"
    thinking_budget: int | None = None
    effort: Effort | None = None
    #: Whether the model thinks on this request, whatever was sent.
    thinking_on: bool = False
    warnings: list[str] = field(default_factory=list)


_TEMPERATURE_MAX: dict[ProviderName, float] = {
    "anthropic": 1.0,
    "openai": 2.0,
    "google": 2.0,
}


def _ensure_temperature(provider: ProviderName, temperature: float | None) -> None:
    """Reject a temperature the provider would receive and then refuse."""
    if temperature is None:
        return
    limit = _TEMPERATURE_MAX[provider]
    if 0 <= temperature <= limit:
        return
    raise LLMError(
        f"temperature must be between 0 and {limit:g} for {provider} models, not {temperature}."
    )


def adapt_parameters(request: CompletionRequest) -> Parameters:
    """Keep what the model accepts, drop the rest with a warning.

    Raises :class:`LLMError` for settings that cannot be repaired by dropping
    a value without changing what was asked: thinking off at an effort the
    model only allows with thinking on, a thinking budget out of range, or a
    temperature outside the range the provider would accept on a request that
    sends one. An id with a capability row follows that row even when it is
    not in ``MODELS`` yet. Any other unknown model is passed through.
    """
    spec = _spec(request.model)
    model = request.model
    if spec is None:
        provider = _provider_prefix(model)
        if provider is not None:
            _ensure_temperature(provider, request.temperature)
        return Parameters(
            temperature=request.temperature,
            top_p=request.top_p,
            top_k=request.top_k,
            thinking=request.thinking,
            thinking_budget=request.thinking_budget if request.thinking == "budget" else None,
            effort=request.effort,
            thinking_on=request.thinking in ("adaptive", "budget"),
        )

    out = Parameters()
    warnings = out.warnings

    if request.thinking == "default" or request.thinking in spec.thinking_modes:
        out.thinking = request.thinking
    else:
        warnings.append(
            f"model '{model}' does not support thinking '{request.thinking}'; "
            "the model's default was used."
        )
    if out.thinking == "budget":
        budget = request.thinking_budget
        if budget is None or budget < spec.min_thinking_budget:
            raise LLMError(
                f"model '{model}' needs a thinking budget of at least "
                f"{spec.min_thinking_budget} tokens."
            )
        if spec.provider == "anthropic" and budget >= max_output_for(model, request.max_tokens):
            raise LLMError(
                f"the thinking budget ({budget}) must be smaller than max_tokens on '{model}'."
            )
        out.thinking_budget = budget
    out.thinking_on = out.thinking in ("adaptive", "budget") or (
        out.thinking == "default" and spec.thinking_default == "on"
    )

    if request.effort is not None:
        if request.effort in spec.effort_levels:
            out.effort = request.effort
        elif spec.effort_levels:
            warnings.append(
                f"model '{model}' does not support effort '{request.effort}'; "
                "the request was sent without one."
            )
        else:
            warnings.append(
                f"model '{model}' does not accept an effort; the request was sent without one."
            )
    if spec.provider == "openai" and out.thinking == "off" and out.effort is not None:
        # One parameter carries both: thinking off is reasoning effort "none".
        warnings.append(
            f"model '{model}' turns thinking off with reasoning effort 'none'; "
            f"effort '{out.effort}' was not sent."
        )
        out.effort = None
    if out.thinking == "off" and spec.thinking_off_max_effort is not None:
        effective = out.effort or spec.default_effort
        if effective is not None and _EFFORT_ALL.index(effective) > _EFFORT_ALL.index(
            spec.thinking_off_max_effort
        ):
            raise LLMError(
                f"model '{model}' only allows thinking off up to effort "
                f"'{spec.thinking_off_max_effort}', not '{effective}'."
            )

    sampling = {"temperature": request.temperature, "top_p": request.top_p, "top_k": request.top_k}
    requested = [name for name, value in sampling.items() if value is not None]
    if requested and not spec.supports_sampling:
        if requested == ["temperature"]:
            warnings.append(
                f"model '{model}' does not accept a temperature; the request "
                "was sent without one. Determinism comes from artifact caching instead."
            )
        else:
            warnings.append(
                f"model '{model}' does not accept sampling parameters "
                f"({', '.join(requested)}); the request was sent without them."
            )
    elif requested and out.thinking_on and not spec.sampling_with_thinking:
        warnings.append(
            f"model '{model}' is thinking on this request, which needs the default "
            f"sampling; {', '.join(requested)} was not sent."
        )
    elif requested:
        _ensure_temperature(spec.provider, request.temperature)
        out.temperature = request.temperature
        out.top_p = request.top_p
        if request.top_k is not None:
            if spec.supports_top_k:
                out.top_k = request.top_k
            else:
                warnings.append(f"model '{model}' does not accept top_k; it was not sent.")
    return out


def catalogue() -> list[dict[str, object]]:
    return [
        {
            "id": s.id,
            "provider": s.provider,
            "input_usd_per_mtok": s.input_usd_per_mtok,
            "output_usd_per_mtok": s.output_usd_per_mtok,
            "max_output_tokens": s.max_output_tokens,
            "supports_sampling": s.supports_sampling,
            "supports_top_k": s.supports_top_k,
            "sampling_with_thinking": s.sampling_with_thinking,
            "thinking_modes": list(s.thinking_modes),
            "thinking_default": s.thinking_default,
            "min_thinking_budget": s.min_thinking_budget,
            "thinking_off_max_effort": s.thinking_off_max_effort,
            "effort_levels": list(s.effort_levels),
            "default_effort": s.default_effort,
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
