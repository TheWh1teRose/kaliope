"""Anthropic provider.

Requests are adapted to the model's declared capabilities before they are sent:
current frontier models reject sampling parameters outright, so ``temperature``
is dropped with a recorded warning rather than raising a 400 in the middle of a
run. Structured outputs are used where available, which is what makes the JSON
contracts in §5.6 and §6 reliable.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import get_settings
from app.llm import registry
from app.llm.base import Completion, CompletionRequest, LLMError, Usage

logger = logging.getLogger(__name__)


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
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
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

        if request.temperature is not None:
            if registry.supports_sampling(request.model):
                kwargs["temperature"] = request.temperature
            else:
                warnings.append(
                    f"model '{request.model}' does not accept a temperature; the request "
                    "was sent without one. Determinism comes from artifact caching instead."
                )

        if request.json_schema is not None:
            if registry.supports_structured_outputs(request.model):
                kwargs["output_config"] = {
                    "format": {"type": "json_schema", "schema": request.json_schema}
                }
            else:
                warnings.append(
                    f"model '{request.model}' has no native structured outputs; "
                    "falling back to prompt-instructed JSON."
                )

        started = time.perf_counter()
        try:
            response = client.messages.create(**kwargs)
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


def _usage(response: Any) -> Usage:
    raw = getattr(response, "usage", None)
    if raw is None:
        return Usage()
    return Usage(
        input_tokens=int(getattr(raw, "input_tokens", 0) or 0),
        output_tokens=int(getattr(raw, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(raw, "cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(getattr(raw, "cache_creation_input_tokens", 0) or 0),
    )
