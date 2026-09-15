"""OpenAI provider."""

from __future__ import annotations

import time
from typing import Any

from app.config import get_settings
from app.llm import registry
from app.llm.base import Completion, CompletionRequest, LLMError, Usage


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        self._client: Any | None = None

    def available(self) -> bool:
        return bool(get_settings().openai_api_key)

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise LLMError("the 'openai' package is not installed") from exc
            self._client = OpenAI(api_key=get_settings().openai_api_key)
        return self._client

    def complete(self, request: CompletionRequest) -> Completion:
        client = self._get_client()
        warnings: list[str] = []

        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend({"role": m.role, "content": m.content} for m in request.messages)

        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_completion_tokens": registry.max_output_for(request.model, request.max_tokens),
        }
        if request.temperature is not None:
            if registry.supports_sampling(request.model):
                kwargs["temperature"] = request.temperature
            else:
                warnings.append(f"model '{request.model}' does not accept a temperature")
        if request.json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": False,
                    "schema": request.json_schema,
                },
            }

        started = time.perf_counter()
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"OpenAI request failed: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        choice = response.choices[0]
        text = choice.message.content or ""
        if not text.strip():
            raise LLMError(f"empty completion (finish_reason={choice.finish_reason})")

        return Completion(
            text=text,
            model_id=getattr(response, "model", request.model),
            usage=_usage(response),
            latency_ms=latency_ms,
            stop_reason=choice.finish_reason,
            warnings=warnings,
        )


def _usage(response: Any) -> Usage:
    raw = getattr(response, "usage", None)
    if raw is None:
        return Usage()
    details = getattr(raw, "prompt_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
    prompt = int(getattr(raw, "prompt_tokens", 0) or 0)
    return Usage(
        input_tokens=max(prompt - cached, 0),
        output_tokens=int(getattr(raw, "completion_tokens", 0) or 0),
        cache_read_tokens=cached,
    )
