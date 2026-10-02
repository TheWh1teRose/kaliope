"""Experiment 1, "Direkter Stil": tune the script step's prompt toward a direct style.

It starts from exactly what production sends for one beat: the script node's
system prompt and response schema, and its user message expressed as a
template over named fields. Everything is editable, fields can be added (for
example a hand-written note placed as ``{{anmerkung}}``), and one run is one
model call for one beat.
"""

from __future__ import annotations

import copy

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.experiments.base import (
    FieldSpec,
    ModelSettings,
    PromptExperiment,
    PromptSetup,
    SourceIn,
    SourceOut,
)
from app.experiments.registry import register_experiment
from app.experiments.sources import BEAT_TEMPLATE, SAMPLE_BEAT, load_beat_source
from app.pipeline.framework.artifacts import ArtifactStore
from app.pipeline.nodes.script import _SCHEMA as SCRIPT_SCHEMA
from app.pipeline.nodes.script import _SYSTEM as SCRIPT_SYSTEM

#: The script node pins this model in both shipped flows.
PRODUCTION_MODEL = "claude-opus-5"


class DirectStyle(PromptExperiment):
    key = "direct_style"
    title = "Direkter Stil"
    summary = (
        "System-Prompt und Nutzer-Nachricht des Skript-Schritts ändern, einen Abschnitt "
        "schreiben lassen und die Ausgaben sammeln."
    )
    target = "Skript-Schritt"
    version = "1"

    def defaults(self) -> BaseModel:
        return PromptSetup(
            system_prompt=SCRIPT_SYSTEM,
            user_template=BEAT_TEMPLATE,
            fields=dict(SAMPLE_BEAT),
            settings=ModelSettings(model=PRODUCTION_MODEL, max_tokens=8000),
            json_schema=copy.deepcopy(SCRIPT_SCHEMA),
        )

    def field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec(key="beat_title", label="Abschnitt"),
            FieldSpec(key="word_budget", label="Wortbudget"),
            FieldSpec(key="speakers", label="Sprecher", multiline=True),
            FieldSpec(key="passages", label="Belegstellen", multiline=True),
            FieldSpec(key="beat_position", label="Position"),
            FieldSpec(key="beat_total", label="Abschnitte gesamt"),
            FieldSpec(key="beat_summary_line", label="Zusammenfassung (Zeile)"),
            FieldSpec(key="register", label="Register"),
            FieldSpec(key="language", label="Sprache"),
            FieldSpec(key="audience", label="Zielgruppe", multiline=True),
            FieldSpec(
                key="opening_closing",
                label="Einstieg/Schluss",
                multiline=True,
                hint="nur im ersten und letzten Abschnitt",
            ),
            FieldSpec(
                key="running_order",
                label="Ablaufplan",
                multiline=True,
                hint="alle Abschnitte der Folge, wie das Modell sie sieht",
            ),
            FieldSpec(
                key="written_so_far",
                label="Bisher geschrieben",
                multiline=True,
                hint="Text der früheren Abschnitte; leer im ersten Abschnitt",
            ),
            FieldSpec(
                key="transition",
                label="Übergang",
                multiline=True,
                hint="wie der vorige Abschnitt endete und was als Nächstes kommt",
            ),
        ]

    def load_source(self, session: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
        return load_beat_source(session, store, source)


register_experiment(DirectStyle())
