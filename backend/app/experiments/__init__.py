"""Experiments (Experimentieren). Importing this package registers every experiment."""

from app.experiments import (  # noqa: F401
    audio_generation,
    audio_tags,
    direct_style,
    outline_plan,
    selection,
    series_plan,
)
from app.experiments.verbalized_sampling import experiment  # noqa: F401
