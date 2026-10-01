"""Wire types for the model catalogue (§ registry capabilities)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.llm.base import Effort, ThinkingMode
from app.llm.registry import ProviderName


class ModelOut(BaseModel):
    """One registered model and the request parameters it accepts."""

    id: str
    provider: ProviderName
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    context_window: int
    max_output_tokens: int
    small: bool
    supports_sampling: bool
    supports_top_k: bool
    sampling_with_thinking: bool
    supports_structured_outputs: bool
    supports_prompt_caching: bool
    thinking_modes: list[ThinkingMode]
    thinking_default: Literal["on", "off"]
    min_thinking_budget: int
    thinking_off_max_effort: Effort | None
    effort_levels: list[Effort]
    default_effort: Effort | None


class ProviderOut(BaseModel):
    name: ProviderName
    #: ``False`` when its API key is not set.
    available: bool


class ModelCatalogueOut(BaseModel):
    models: list[ModelOut]
    providers: list[ProviderOut]
