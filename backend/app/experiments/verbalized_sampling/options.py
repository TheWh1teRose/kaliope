"""The Verbalized Sampling knobs a run is configured with.

Kept apart from the shared model settings (model, temperature, max tokens …):
those belong to the experiments framework and are the same for every
experiment. These are the ones only VS has.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: VS-Standard, VS-CoT and VS-Multi from the paper (Table 1), plus "list": the
#: same request without probabilities (the paper's "Sequence" baseline), as a
#: control for whether verbalizing the probabilities matters at all.
Variant = Literal["standard", "cot", "multi", "list"]

#: How the probability field is defined in the prompt (paper §F.4).
ProbabilityFormat = Literal["explicit", "implicit", "relative", "percentage", "confidence"]

#: "off" adds nothing; "full" asks to sample from the full distribution;
#: "below" asks for every version to stay below ``threshold`` (paper §F.5).
ThresholdMode = Literal["off", "full", "below"]

#: How many plain calls (no VS block) run next to the VS call.
BaselineMode = Literal["none", "single", "k"]

#: Smallest and largest k. Beats are long; the paper reports quality dropping
#: as k grows (§F.2), so k stays well below its largest setting of 20.
MIN_K = 2
MAX_K = 8


class VSOptions(BaseModel):
    k: int = Field(5, ge=MIN_K, le=MAX_K)
    variant: Variant = "standard"
    probability_format: ProbabilityFormat = "explicit"
    threshold_mode: ThresholdMode = "off"
    threshold: float = Field(0.1, gt=0.0, lt=1.0)
    baseline: BaselineMode = "single"
    #: Only for ``variant == "multi"``: total turns, k versions per turn.
    turns: int = Field(2, ge=2, le=4)

    def expected_candidates(self) -> int:
        return self.k * self.turns if self.variant == "multi" else self.k

    def baseline_calls(self) -> int:
        return {"none": 0, "single": 1, "k": self.k}[self.baseline]

    @property
    def verbalizes_probability(self) -> bool:
        return self.variant != "list"
