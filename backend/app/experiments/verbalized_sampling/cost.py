"""Cost before a run (estimate) and after it (from the LLM client's traces).

Prices come from the model registry, the same source the rest of the app uses.
The after-run numbers include the honest comparison: one VS call costs about k
plain calls of output, which is only "cheap" per draft.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.experiments.verbalized_sampling.options import VSOptions
from app.llm import registry
from app.llm.base import Usage

#: ``node_name`` set on the LLM client for each call, so traces and
#: ``llm_calls`` rows say which side of the comparison a call was.
VS_NODE = "experiment:verbalized_sampling"
BASELINE_NODE = "experiment:verbalized_sampling:baseline"

#: Rough characters per input token.
CHARS_PER_TOKEN = 4
#: Output tokens per spoken word, including JSON keys and citation quotes.
#: German runs long; the estimate is meant to be rough and is labelled so.
TOKENS_PER_WORD = 2.0


class CostEstimate(BaseModel):
    vs_usd: float
    baseline_usd: float
    vs_output_tokens: int
    #: The VS answer is expected not to fit into ``max_tokens``.
    exceeds_max_tokens: bool


class RunCost(BaseModel):
    vs_usd: float = 0.0
    baseline_usd: float = 0.0
    vs_calls: int = 0
    baseline_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    #: VS cost divided by the number of drafts it returned.
    vs_per_draft_usd: float | None = None
    #: VS cost against one plain call; ``None`` without a plain call.
    ratio_to_single_baseline: float | None = None


def estimate(
    model: str,
    *,
    input_chars: int,
    word_budget: int,
    options: VSOptions,
    max_tokens: int,
) -> CostEstimate:
    tokens_in = max(1, input_chars // CHARS_PER_TOKEN)
    per_draft = int(word_budget * TOKENS_PER_WORD)
    vs_out = per_draft * options.expected_candidates()
    vs_calls = options.turns if options.variant == "multi" else 1
    vs = registry.cost_usd(model, Usage(input_tokens=tokens_in * vs_calls, output_tokens=vs_out))
    single = registry.cost_usd(model, Usage(input_tokens=tokens_in, output_tokens=per_draft))
    return CostEstimate(
        vs_usd=round(vs, 6),
        baseline_usd=round(single * options.baseline_calls(), 6),
        vs_output_tokens=vs_out,
        exceeds_max_tokens=vs_out // vs_calls > registry.max_output_for(model, max_tokens),
    )


def summarize(traces: list[dict[str, Any]], drafts: int) -> RunCost:
    """Sum ``LLMClient.traces`` per side of the comparison."""
    cost = RunCost()
    for trace in traces:
        usd = float(trace.get("cost_usd") or 0.0)
        if trace.get("node_name") == BASELINE_NODE:
            cost.baseline_usd += usd
            cost.baseline_calls += 1
        elif trace.get("node_name") == VS_NODE:
            cost.vs_usd += usd
            cost.vs_calls += 1
        else:
            continue
        cost.tokens_in += int(trace.get("tokens_in") or 0)
        cost.tokens_out += int(trace.get("tokens_out") or 0)
        cost.latency_ms += int(trace.get("latency_ms") or 0)

    cost.vs_usd = round(cost.vs_usd, 8)
    cost.baseline_usd = round(cost.baseline_usd, 8)
    if drafts and cost.vs_calls:
        cost.vs_per_draft_usd = round(cost.vs_usd / drafts, 8)
    if cost.baseline_calls and cost.baseline_usd > 0:
        single = cost.baseline_usd / cost.baseline_calls
        cost.ratio_to_single_baseline = round(cost.vs_usd / single, 2)
    return cost
