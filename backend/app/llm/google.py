"""Google provider."""

from __future__ import annotations

import time
from typing import Any

from app.config import get_settings
from app.llm import registry
from app.llm.base import Completion, CompletionRequest, LLMError, Usage


class GoogleProvider:
    name = "google"

    def __init__(self) -> None:
        self._client: Any | None = None

    def available(self) -> bool:
        return bool(get_settings().google_api_key)

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover
                raise LLMError("the 'google-genai' package is not installed") from exc
            self._client = genai.Client(api_key=get_settings().google_api_key)
        return self._client

    def complete(self, request: CompletionRequest) -> Completion:
        client = self._get_client()
        warnings: list[str] = []

        config: dict[str, Any] = {
            "max_output_tokens": registry.max_output_for(request.model, request.max_tokens),
        }
        if request.system:
            config["system_instruction"] = request.system
        if request.temperature is not None:
            if registry.supports_sampling(request.model):
                config["temperature"] = request.temperature
            else:
                warnings.append(f"model '{request.model}' does not accept a temperature")
        if request.json_schema is not None:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = request.json_schema

        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in request.messages
        ]

        started = time.perf_counter()
        try:
            response = client.models.generate_content(
                model=request.model, contents=contents, config=config
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Google request failed: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        text = getattr(response, "text", "") or ""
        if not text.strip():
            raise LLMError("empty completion")

        return Completion(
            text=text,
            model_id=request.model,
            usage=_usage(response),
            latency_ms=latency_ms,
            warnings=warnings,
        )


def _usage(response: Any) -> Usage:
    raw = getattr(response, "usage_metadata", None)
    if raw is None:
        return Usage()
    cached = int(getattr(raw, "cached_content_token_count", 0) or 0)
    prompt = int(getattr(raw, "prompt_token_count", 0) or 0)
    return Usage(
        input_tokens=max(prompt - cached, 0),
        output_tokens=int(getattr(raw, "candidates_token_count", 0) or 0),
        cache_read_tokens=cached,
    )
