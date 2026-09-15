"""Provider protocol and the run-scoped LLM client (§7.1).

Every model call goes through :class:`LLMClient`, which records usage and cost
against the run. That is what makes AC-FW-3 — manifest cost equals the sum of
``llm_calls`` — true by construction rather than by discipline.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Role = Literal["user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class CompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    system: str | None = None
    max_tokens: int = 8192
    #: Requested sampling temperature. Dropped, with a warning, for models whose
    #: API rejects sampling parameters (§ registry ``supports_sampling``).
    temperature: float | None = None
    #: JSON Schema for a structured response. Providers that support native
    #: structured outputs use it; the rest fall back to prompt instruction plus
    #: tolerant parsing.
    json_schema: dict[str, Any] | None = None
    #: Marks the leading part of the prompt as cacheable.
    cache_system: bool = False


class Completion(BaseModel):
    text: str
    model_id: str
    usage: Usage
    latency_ms: int
    stop_reason: str | None = None
    #: Warnings raised while adapting the request to the model's capabilities.
    warnings: list[str] = Field(default_factory=list)

    def json_payload(self) -> Any:
        """Parse the completion as JSON, tolerating code fences and preamble."""
        try:
            return parse_json(self.text)
        except LLMError:
            if self.stop_reason == "max_tokens":
                raise LLMError(
                    "model response was cut off (hit max_tokens) and was not "
                    f"valid JSON: {self.text[:400]!r}"
                ) from None
            raise


class LLMError(RuntimeError):
    """Raised when a provider call fails in a way the node cannot recover from."""


class Provider(Protocol):
    name: str

    def complete(self, request: CompletionRequest) -> Completion: ...

    def available(self) -> bool: ...


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json(text: str) -> Any:
    """Best-effort JSON extraction from a model response.

    Structured outputs make this unnecessary on capable models, but the parser
    stays so that a cheaper model without the feature is still usable. A
    response cut off at max_tokens is repaired when the tail can be closed
    without inventing values.
    """
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    match = _FENCE.search(text)
    if match:
        candidates.insert(0, match.group(1).strip())

    # Fall back to the outermost brace/bracket pair.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if 0 <= start < end:
            candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            repaired = _repair_truncated_json(candidate)
            if repaired is not None:
                return repaired
    raise LLMError(f"model response was not valid JSON: {text[:400]!r}")


def _scan_json(text: str) -> tuple[bool, list[str]]:
    """Return (in_string, closers) after scanning ``text``."""
    in_string = False
    escape = False
    stack: list[str] = []
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]" and stack and stack[-1] == char:
            stack.pop()
    return in_string, stack


def _loads_closed(prefix: str) -> Any | None:
    in_string, stack = _scan_json(prefix)
    if in_string:
        return None
    trimmed = prefix.rstrip()
    if not trimmed or trimmed[-1] in ",:[{":
        return None
    try:
        return json.loads(trimmed + "".join(reversed(stack)))
    except json.JSONDecodeError:
        return None


def _repair_truncated_json(text: str) -> Any | None:
    """Close a cut-off object or array. Drops an unfinished last value."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None

    in_string, _stack = _scan_json(stripped)
    if in_string:
        repaired = _loads_closed(stripped + '"')
        if repaired is not None:
            return repaired

    for end in range(len(stripped), 0, -1):
        if stripped[end - 1] not in ",}]":
            continue
        prefix = stripped[:end].rstrip().rstrip(",")
        repaired = _loads_closed(prefix)
        if repaired is not None:
            return repaired
    return None


class UsageRecorder(Protocol):
    def __call__(
        self,
        *,
        model_id: str,
        usage: Usage,
        cost_usd: float,
        latency_ms: int,
        node_name: str | None,
    ) -> None: ...


class LLMClient:
    """Run-scoped facade over the provider registry.

    ``node_name`` is set by the runner before each node executes, so every call
    is attributable without the node having to pass it.
    """

    def __init__(
        self,
        resolve: Callable[[str], Provider],
        cost_of: Callable[[str, Usage], float],
        recorder: UsageRecorder | None = None,
    ) -> None:
        self._resolve = resolve
        self._cost_of = cost_of
        self._recorder = recorder
        self.node_name: str | None = None
        self.total_cost_usd: float = 0.0
        self.total_usage = Usage()
        self.calls: int = 0
        #: Every request this client sent, in order. The bench reads this so a
        #: developer can see the prompt, not only the parsed node output.
        self.traces: list[dict[str, Any]] = []

    def complete(self, request: CompletionRequest) -> Completion:
        provider = self._resolve(request.model)
        started = time.perf_counter()
        completion = provider.complete(request)
        if not completion.latency_ms:
            completion.latency_ms = int((time.perf_counter() - started) * 1000)

        cost = self._cost_of(completion.model_id, completion.usage)
        self.total_cost_usd += cost
        self.total_usage = self.total_usage + completion.usage
        self.calls += 1
        self.traces.append(
            {
                "node_name": self.node_name,
                "model": request.model,
                "system": request.system,
                "messages": [message.model_dump(mode="json") for message in request.messages],
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
                "response_text": completion.text,
                "latency_ms": completion.latency_ms,
                "tokens_in": completion.usage.input_tokens + completion.usage.cache_read_tokens,
                "tokens_out": completion.usage.output_tokens,
                "cost_usd": round(cost, 8),
                "warnings": list(completion.warnings),
            }
        )
        if self._recorder is not None:
            self._recorder(
                model_id=completion.model_id,
                usage=completion.usage,
                cost_usd=cost,
                latency_ms=completion.latency_ms,
                node_name=self.node_name,
            )
        return completion

    def complete_json(self, request: CompletionRequest) -> Any:
        return self.complete(request).json_payload()
