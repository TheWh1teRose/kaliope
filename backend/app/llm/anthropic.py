"""Anthropic provider.

Requests are adapted to the model's declared capabilities before they are sent:
current frontier models reject sampling parameters outright, so ``temperature``
is dropped with a recorded warning rather than raising a 400 in the middle of a
run. Sampling parameters (``temperature``, ``top_p``, ``top_k``) go through
``extra_body``, because SDK 1.x no longer accepts them as ``messages.create``
keyword arguments; thinking and ``output_config.effort`` are sent per model as
the registry allows. A ``max_tokens`` above the SDK's non-streaming budget is
sent with ``messages.stream`` and read back via ``get_final_message`` so the
completion, usage, and cost path stay the same. Structured outputs are used
where available, which is what makes the JSON contracts in §5.6 and §6 reliable.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import get_settings
from app.llm import registry
from app.llm.base import Completion, CompletionRequest, LLMError, Message, Usage

logger = logging.getLogger(__name__)

# Anthropic._calculate_nonstreaming_timeout refuses a non-streaming call when
# ``3600 * max_tokens / 128_000`` exceeds the 10-minute default. 21333 still
# passes; 21334 is the first value that raises.
_NONSTREAMING_MAX_TOKENS = 128_000 * 600 // 3600


class AnthropicProvider:
    name = "anthropic"

    def __init__(self) -> None:
        self._client: Any | None = None

    def available(self) -> bool:
        return bool(get_settings().anthropic_api_key)

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise LLMError("the 'anthropic' package is not installed") from exc
            self._client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)
        return self._client

    def complete(self, request: CompletionRequest) -> Completion:
        client = self._get_client()
        warnings: list[str] = []

        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": registry.max_output_for(request.model, request.max_tokens),
            "messages": [
                _message(m, cache=registry.spec_for(request.model) is not None)
                for m in request.messages
            ],
        }

        if request.system:
            if request.cache_system and registry.spec_for(request.model) is not None:
                kwargs["system"] = [
                    {
                        "type": "text",
                        "text": request.system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            else:
                kwargs["system"] = request.system

        params = registry.adapt_parameters(request)
        warnings.extend(params.warnings)
        # SDK 1.x has no temperature/top_p/top_k keywords; the API still reads
        # them from the JSON body.
        sampling = {
            name: value
            for name, value in (
                ("temperature", params.temperature),
                ("top_p", params.top_p),
                ("top_k", params.top_k),
            )
            if value is not None
        }
        if sampling:
            kwargs["extra_body"] = sampling
        if params.thinking == "adaptive":
            kwargs["thinking"] = {"type": "adaptive"}
        elif params.thinking == "off":
            kwargs["thinking"] = {"type": "disabled"}
        elif params.thinking == "budget":
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": params.thinking_budget}

        output_config: dict[str, Any] = {}
        if params.effort is not None:
            output_config["effort"] = params.effort
        if request.json_schema is not None:
            if registry.supports_structured_outputs(request.model):
                output_config["format"] = {"type": "json_schema", "schema": request.json_schema}
            else:
                warnings.append(
                    f"model '{request.model}' has no native structured outputs; "
                    "falling back to prompt-instructed JSON."
                )
        if output_config:
            kwargs["output_config"] = output_config

        started = time.perf_counter()
        try:
            response = _send(client, kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced to the run as a node failure
            raise LLMError(f"Anthropic request failed: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            raise LLMError(
                "the model declined this request "
                f"({getattr(getattr(response, 'stop_details', None), 'category', 'unspecified')})"
            )

        text = "".join(
            block.text
            for block in getattr(response, "content", [])
            if getattr(block, "type", None) == "text"
        )
        if not text.strip():
            raise LLMError(f"empty completion (stop_reason={stop_reason})")

        return Completion(
            text=text,
            model_id=getattr(response, "model", request.model),
            usage=_usage(response),
            latency_ms=latency_ms,
            stop_reason=stop_reason,
            warnings=warnings,
        )


def _message(message: Message, *, cache: bool) -> dict[str, Any]:
    """One message, split into text blocks at its cache breaks.

    Only the block that ends at the last break carries ``cache_control``. The
    earlier block boundaries still matter: the API looks back over them for a
    prefix an earlier call cached, so a prompt that grows call by call reads
    everything up to the previous call's breakpoint from the cache.
    """
    breaks = sorted({b for b in message.cache_breaks if 0 < b <= len(message.content)})
    if not cache or not breaks:
        return {"role": message.role, "content": message.content}
    blocks: list[dict[str, Any]] = []
    start = 0
    for end in [*breaks, len(message.content)]:
        if end > start:
            blocks.append({"type": "text", "text": message.content[start:end]})
            if end == breaks[-1]:
                blocks[-1]["cache_control"] = {"type": "ephemeral"}
        start = end
    return {"role": message.role, "content": blocks}


def _send(client: Any, kwargs: dict[str, Any]) -> Any:
    """Call Messages, streaming only when a non-streaming request would be refused."""
    if int(kwargs["max_tokens"]) > _NONSTREAMING_MAX_TOKENS:
        with client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()
    return client.messages.create(**kwargs)


def _usage(response: Any) -> Usage:
    raw = getattr(response, "usage", None)
    if raw is None:
        return Usage()
    creation = getattr(raw, "cache_creation", None)
    return Usage(
        input_tokens=int(getattr(raw, "input_tokens", 0) or 0),
        output_tokens=int(getattr(raw, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(raw, "cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(getattr(raw, "cache_creation_input_tokens", 0) or 0),
        cache_write_1h_tokens=int(getattr(creation, "ephemeral_1h_input_tokens", 0) or 0),
    )
