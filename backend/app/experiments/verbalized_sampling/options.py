"""The Verbalized Sampling knobs a run is configured with.

Kept apart from the shared model settings (model, temperature, max tokens …):
those belong to the experiments framework and are the same for every
experiment. These are the ones only VS has.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

#: Smallest and largest k. Beats are long; the paper reports quality dropping
#: as k grows (§F.2), so k stays well below its largest setting of 20.
MIN_K = 2
MAX_K = 8


class VSOptions(BaseModel):
    k: int = Field(5, ge=MIN_K, le=MAX_K)
