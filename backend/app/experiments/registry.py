"""Experiment registry: every experiment registers itself on import."""

from __future__ import annotations

from app.experiments.base import FIELD_NAME, Experiment

_EXPERIMENTS: dict[str, Experiment] = {}

#: Path segments the workbench, the compare page and the experiments API already use.
_RESERVED = frozenset({"bench", "runs", "outputs", "compare"})


def register_experiment(experiment: Experiment) -> Experiment:
    key = experiment.key
    if not FIELD_NAME.match(key) or key in _RESERVED:
        raise ValueError(f"experiment key '{key}' is not allowed")
    if key in _EXPERIMENTS and _EXPERIMENTS[key] is not experiment:
        raise ValueError(f"experiment '{key}' is already registered")
    _EXPERIMENTS[key] = experiment
    return experiment


def get_experiment(key: str) -> Experiment:
    try:
        return _EXPERIMENTS[key]
    except KeyError as exc:
        raise KeyError(f"unknown experiment '{key}'") from exc


def experiments() -> list[Experiment]:
    return [_EXPERIMENTS[key] for key in sorted(_EXPERIMENTS)]
