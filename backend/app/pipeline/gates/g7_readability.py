"""Gate G7 — readability (§8).

A heuristic designed for written text: directional only, never a target to
optimise. It skips cleanly when the detected language has no formula
(AC-GATE-4), rather than applying an English formula to non-English prose.
"""

from __future__ import annotations

from app.lang import readability
from app.pipeline.gates.base import (
    GateContext,
    GateReport,
    GateSpec,
    passed,
    skipped,
    warned,
)
from app.schemas.gates import Violation


class G7Readability:
    id = "G7"
    name = "readability"

    def spec(self) -> GateSpec:
        return GateSpec(
            id=self.id,
            name=self.name,
            severity="warn",
            inspects=["script"],
            rule=(
                "The whole script scores inside the readability band configured for its language."
            ),
            method=(
                "Joins every segment into one text and scores it with the formula registered "
                "for the detected language, then compares against that language's band. The "
                "formula was designed for written prose, not speech, so the result is a "
                "direction and never a target to optimise — which is why this gate warns and "
                "cannot fail. A language with no registered formula is skipped rather than "
                "scored with someone else's formula."
            ),
            thresholds={
                "formulas": sorted(readability.FORMULAS),
                "bands": {
                    language: [band.min_score, band.max_score]
                    for language, band in sorted(readability.DEFAULT_BANDS.items())
                },
            },
            skip_condition="the detected language has no registered readability formula",
        )

    def check(self, ctx: GateContext) -> GateReport:
        language = ctx.parsed.language
        if not readability.has_formula(language):
            return skipped(
                self,
                f"no readability formula is defined for language '{language}'",
                {"language": language},
            )

        text = " ".join(s.text for s in ctx.script.segments)
        score = readability.score(language, text)
        if score is None or score.words == 0:  # pragma: no cover - guarded by has_formula
            return skipped(self, "the script contains no scorable text", {"language": language})

        band = readability.band_for(language)
        min_score = float(ctx.get("min_score", band.min_score if band else 0.0))
        max_score = float(ctx.get("max_score", band.max_score if band else 100.0))
        measurements = {
            "language": language,
            "formula": score.formula,
            "score": score.score,
            "label": score.label,
            "band_min": min_score,
            "band_max": max_score,
            "words": score.words,
            "sentences": score.sentences,
        }

        if min_score <= score.score <= max_score:
            return passed(self, measurements)

        direction = "harder" if score.score < min_score else "easier"
        return warned(
            self,
            [
                Violation(
                    message=(
                        f"{score.formula} scores {score.score} ({score.label}), outside the "
                        f"configured band {min_score}–{max_score}. The script reads "
                        f"{direction} than intended. This is a written-text heuristic; "
                        "treat it as a direction, not a target."
                    ),
                    detail={
                        "score": score.score,
                        "formula": score.formula,
                        "words": score.words,
                        "sentences": score.sentences,
                    },
                )
            ],
            measurements,
        )
