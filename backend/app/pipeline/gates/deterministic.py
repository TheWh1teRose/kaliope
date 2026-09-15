"""Gates G0–G6 and G8 (§8).

All deterministic; none makes a model call. Deliberately absent is any
model-based check of whether a cited span actually supports its claim — that is
the fact-checker, and it brings a calibration problem that does not belong in a
control condition.

Each gate reports the numbers it computed even when it passes, so the console
can show the measurement rather than only the verdict.
"""

from __future__ import annotations

from app.ingestion.anchors import anchor_is_valid
from app.ingestion.normalize import normalize_text
from app.lang.resources import stopwords_for, words_per_minute
from app.pipeline.gates.base import (
    GateContext,
    GateReport,
    GateSpec,
    failed,
    ngrams,
    passed,
    skipped,
    warned,
    words,
)
from app.schemas.gates import Violation

#: G0 thresholds (§8).
ANCHOR_WARN_BELOW = 0.99
ANCHOR_FAIL_BELOW = 0.95
#: G3 shingle size.
APPARATUS_NGRAM = 6
#: G4 tolerance around the target word count.
LENGTH_TOLERANCE = 0.15
#: G5 share of an objective's content words that must appear in the outline.
OBJECTIVE_COVERAGE = 0.5
#: G6 ignores boilerplate shorter than this; a very short string collides with
#: ordinary prose and would fail every run for no reason.
MIN_BOILERPLATE_CHARS = 12


class G0IngestionConfidence:
    id = "G0"
    name = "ingestion_confidence"

    def spec(self) -> GateSpec:
        return GateSpec(
            id=self.id,
            name=self.name,
            severity="warn",
            inspects=["ingest"],
            rule=(
                "The parse has to be trustworthy enough to cite: anchor integrity at or "
                f"above {ANCHOR_WARN_BELOW:.0%}, and ingestion confidence not 'low'."
            ),
            method=(
                "Reads the ingestion report the parse produced for itself. Anchor "
                f"integrity below {ANCHOR_FAIL_BELOW:.0%} is the one case this gate fails "
                "rather than warns, because below that floor no citation in the run can be "
                "trusted. A 'low' confidence always carries the measurements that caused it."
            ),
            thresholds={
                "anchor_warn_below": ANCHOR_WARN_BELOW,
                "anchor_fail_below": ANCHOR_FAIL_BELOW,
            },
        )

    def check(self, ctx: GateContext) -> GateReport:
        report = ctx.parsed.report
        measurements = {
            "anchor_integrity": report.anchor_integrity,
            "ingestion_confidence": report.ingestion_confidence,
            "structure_source": report.structure_source,
            "structure_confidence": report.structure_confidence,
            "reading_order_confidence": report.reading_order_confidence,
            "zone_uncertain_ratio": report.zone_uncertain_ratio,
            "confidence_reasons": report.confidence_reasons,
        }
        violations: list[Violation] = []

        if report.anchor_integrity < ANCHOR_FAIL_BELOW:
            return failed(
                self,
                [
                    Violation(
                        message=(
                            f"Anchor integrity is {report.anchor_integrity:.1%}, below the "
                            f"{ANCHOR_FAIL_BELOW:.0%} floor. Citations cannot be trusted."
                        ),
                        detail={"anchor_integrity": report.anchor_integrity},
                    )
                ],
                measurements,
            )

        if report.anchor_integrity < ANCHOR_WARN_BELOW:
            violations.append(
                Violation(
                    message=(
                        f"Anchor integrity is {report.anchor_integrity:.1%}, below the "
                        f"{ANCHOR_WARN_BELOW:.0%} target."
                    ),
                    detail={"anchor_integrity": report.anchor_integrity},
                )
            )

        if report.ingestion_confidence == "low":
            violations.append(
                Violation(
                    message="Ingestion confidence is low: " + " ".join(report.confidence_reasons),
                    detail={"reasons": report.confidence_reasons},
                )
            )

        return warned(self, violations, measurements) if violations else passed(self, measurements)


class G1AnchorsResolve:
    id = "G1"
    name = "anchors_resolve"

    def spec(self) -> GateSpec:
        return GateSpec(
            id=self.id,
            name=self.name,
            severity="fail",
            inspects=["script", "ingest"],
            rule="Every anchor resolves to real characters inside a real block of the parse.",
            method=(
                "For each anchor, looks the block up in the parse and checks that the "
                "character range exists and is non-empty. This is what makes the citation "
                "on screen a fact about the document rather than a claim by the model — "
                "the script node locates a quote and turns it into an offset, and this "
                "gate verifies the result."
            ),
            thresholds={"tolerated_broken_anchors": 0},
        )

    def check(self, ctx: GateContext) -> GateReport:
        violations: list[Violation] = []
        total = 0
        for segment in ctx.script.segments:
            for anchor in segment.anchors:
                total += 1
                ok, reason = anchor_is_valid(ctx.parsed, anchor)
                if not ok:
                    violations.append(
                        Violation(
                            target_id=segment.id,
                            message=f"Anchor does not resolve: {reason}.",
                            detail={
                                "block_id": anchor.block_id,
                                "char_start": anchor.char_start,
                                "char_end": anchor.char_end,
                            },
                        )
                    )
        measurements = {
            "anchors_checked": total,
            "anchors_broken": len(violations),
            "resolve_rate": round(1 - (len(violations) / total), 4) if total else None,
        }
        return failed(self, violations, measurements) if violations else passed(self, measurements)


class G2ClaimsCited:
    id = "G2"
    name = "claims_cited"

    def spec(self) -> GateSpec:
        return GateSpec(
            id=self.id,
            name=self.name,
            severity="fail",
            inspects=["script"],
            rule="Every segment of kind 'claim' carries at least one anchor.",
            method=(
                "Counts segments the script node labelled 'claim' that have an empty anchor "
                "list. Segments of kind 'pedagogy' — analogies, transitions, framing — are "
                "exempt by design: they introduce no new facts, so there is nothing to cite."
            ),
            thresholds={"min_anchors_per_claim": 1},
        )

    def check(self, ctx: GateContext) -> GateReport:
        claims = [s for s in ctx.script.segments if s.kind == "claim"]
        violations = [
            Violation(
                target_id=segment.id,
                message="Segment is marked as a claim but carries no citation.",
                detail={"text": segment.text[:200]},
            )
            for segment in claims
            if not segment.anchors
        ]
        measurements = {
            "segments": len(ctx.script.segments),
            "claim_segments": len(claims),
            "pedagogy_segments": len(ctx.script.segments) - len(claims),
            "uncited_claims": len(violations),
        }
        return failed(self, violations, measurements) if violations else passed(self, measurements)


class G3NoApparatusLeak:
    id = "G3"
    name = "no_apparatus_leak"

    def spec(self) -> GateSpec:
        return GateSpec(
            id=self.id,
            name=self.name,
            severity="fail",
            inspects=["script", "ingest"],
            rule=(
                f"No segment shares a {APPARATUS_NGRAM}-word sequence with text from a "
                "non-narratable zone."
            ),
            method=(
                "Normalises every non-narratable block — exercises, forms, reference lists, "
                f"front matter — into {APPARATUS_NGRAM}-gram shingles, does the same to each "
                "segment, and intersects the two sets. A shared shingle means the script is "
                f"reciting course apparatus instead of teaching. {APPARATUS_NGRAM} words is "
                "long enough that ordinary German prose does not collide by chance."
            ),
            thresholds={"ngram_size": APPARATUS_NGRAM},
        )

    def check(self, ctx: GateContext) -> GateReport:
        forbidden: set[tuple[str, ...]] = set()
        for text in ctx.parsed.non_narratable_text:
            forbidden |= ngrams(words(normalize_text(text, ctx.parsed.language)), APPARATUS_NGRAM)
        if not forbidden:
            return passed(self, {"forbidden_ngrams": 0, "segments_checked": 0})

        violations: list[Violation] = []
        for segment in ctx.script.segments:
            shared = (
                ngrams(words(normalize_text(segment.text, ctx.parsed.language)), APPARATUS_NGRAM)
                & forbidden
            )
            if shared:
                sample = " ".join(sorted(shared)[0])
                violations.append(
                    Violation(
                        target_id=segment.id,
                        message=(
                            "Segment reproduces text from a non-narratable zone "
                            f"(exercise, form, reference or front matter): “{sample}”."
                        ),
                        detail={"shared_ngrams": len(shared)},
                    )
                )
        measurements = {
            "forbidden_ngrams": len(forbidden),
            "segments_checked": len(ctx.script.segments),
            "segments_leaking": len(violations),
        }
        return failed(self, violations, measurements) if violations else passed(self, measurements)


class G4LengthBand:
    id = "G4"
    name = "length_band"

    def spec(self) -> GateSpec:
        return GateSpec(
            id=self.id,
            name=self.name,
            severity="fail",
            inspects=["script", "content_budget"],
            rule=(
                f"The script's word count lies within ±{LENGTH_TOLERANCE:.0%} of "
                "target_minutes × words-per-minute for the document's language."
            ),
            method=(
                "Takes the target minutes the content budget settled on — not the minutes "
                "that were requested, which the budget may have clamped — multiplies by the "
                "speaking rate for the detected language, and compares the band against the "
                "script's actual word count."
            ),
            thresholds={"tolerance": LENGTH_TOLERANCE},
        )

    def check(self, ctx: GateContext) -> GateReport:
        wpm = words_per_minute(ctx.parsed.language)
        target = ctx.budget.target_minutes * wpm
        actual = ctx.script.word_count()
        tolerance = float(ctx.get("tolerance", LENGTH_TOLERANCE))
        low, high = target * (1 - tolerance), target * (1 + tolerance)
        measurements = {
            "actual_words": actual,
            "target_words": round(target),
            "band_low": round(low),
            "band_high": round(high),
            "words_per_minute": wpm,
            "target_minutes": ctx.budget.target_minutes,
            "tolerance": tolerance,
        }

        if low <= actual <= high:
            return passed(self, measurements)
        return failed(
            self,
            [
                Violation(
                    message=(
                        f"The script is {actual} words; the band for "
                        f"{ctx.budget.target_minutes:.1f} minutes at {wpm} wpm is "
                        f"{low:.0f}–{high:.0f} words."
                    ),
                    detail={"actual": actual, "target": round(target), "tolerance": tolerance},
                )
            ],
            measurements,
        )


class G5ObjectiveCoverage:
    id = "G5"
    name = "objective_coverage"

    def spec(self) -> GateSpec:
        return GateSpec(
            id=self.id,
            name=self.name,
            severity="warn",
            inspects=["ingest", "outline", "objective_outline", "select", "objective_select"],
            rule=(
                "Every learning objective the document states is served by at least one beat "
                f"of the outline, at {OBJECTIVE_COVERAGE:.0%} content-word overlap or better."
            ),
            method=(
                "Collects the words of every beat title and summary plus the selected "
                "learning goals, then for each objective drops stopwords and words of four "
                "characters or fewer and measures what share of the remainder appears in that "
                "pool. A lexical proxy for coverage, not a semantic one — which is why it "
                "warns instead of failing. It skips entirely when the document states no "
                "objectives of its own, rather than inventing some to score against."
            ),
            thresholds={
                "min_coverage": OBJECTIVE_COVERAGE,
                "min_content_word_length": 4,
            },
            skip_condition="the document states no objectives of its own",
        )

    def check(self, ctx: GateContext) -> GateReport:
        objectives = ctx.parsed.objectives
        if not objectives:
            return skipped(self, "the document states no objectives of its own", {"objectives": 0})

        stopwords = stopwords_for(ctx.parsed.language)
        covered_tokens: set[str] = set()
        for beat in ctx.outline.beats:
            covered_tokens |= set(words(beat.title))
            covered_tokens |= set(words(beat.summary or ""))
        for goal in ctx.selection.learning_goals:
            covered_tokens |= set(words(goal.text))

        violations: list[Violation] = []
        scores: list[float] = []
        for index, objective in enumerate(objectives):
            content = [w for w in words(objective) if w not in stopwords and len(w) > 3]
            if not content:
                continue
            hits = sum(1 for w in content if w in covered_tokens)
            coverage = hits / len(content)
            scores.append(coverage)
            if coverage < OBJECTIVE_COVERAGE:
                violations.append(
                    Violation(
                        target_id=f"objective-{index}",
                        message=f"No beat appears to serve this objective: “{objective[:160]}”.",
                        detail={"coverage": round(coverage, 2)},
                    )
                )
        measurements = {
            "objectives": len(objectives),
            "objectives_scored": len(scores),
            "objectives_uncovered": len(violations),
            "mean_coverage": round(sum(scores) / len(scores), 3) if scores else None,
            "beats": len(ctx.outline.beats),
        }
        return warned(self, violations, measurements) if violations else passed(self, measurements)


class G6NoBoilerplate:
    id = "G6"
    name = "no_boilerplate"

    def spec(self) -> GateSpec:
        return GateSpec(
            id=self.id,
            name=self.name,
            severity="fail",
            inspects=["script", "ingest"],
            rule="No segment reproduces page furniture the repetition analysis removed.",
            method=(
                "The parse records every running header, footer and watermark it stripped. "
                "Each is normalised and searched for as a substring of each normalised "
                "segment. Strings shorter than "
                f"{MIN_BOILERPLATE_CHARS} characters are ignored, because a short one "
                "collides with ordinary prose and would fail every run for no reason."
            ),
            thresholds={"min_boilerplate_chars": MIN_BOILERPLATE_CHARS},
        )

    def check(self, ctx: GateContext) -> GateReport:
        candidates = [
            normalize_text(b, ctx.parsed.language).casefold()
            for b in ctx.parsed.boilerplate
            if len(b.strip()) >= MIN_BOILERPLATE_CHARS
        ]
        if not candidates:
            return passed(
                self,
                {"boilerplate_strings": len(ctx.parsed.boilerplate), "candidates_checked": 0},
            )

        violations: list[Violation] = []
        for segment in ctx.script.segments:
            haystack = normalize_text(segment.text, ctx.parsed.language).casefold()
            for needle in candidates:
                if needle and needle in haystack:
                    violations.append(
                        Violation(
                            target_id=segment.id,
                            message=f"Segment contains removed page furniture: “{needle[:80]}”.",
                        )
                    )
                    break
        measurements = {
            "boilerplate_strings": len(ctx.parsed.boilerplate),
            "candidates_checked": len(candidates),
            "segments_affected": len(violations),
        }
        return failed(self, violations, measurements) if violations else passed(self, measurements)


class G8Structure:
    id = "G8"
    name = "structure"

    def spec(self) -> GateSpec:
        return GateSpec(
            id=self.id,
            name=self.name,
            severity="fail",
            inspects=["script", "outline"],
            rule=(
                "Only declared speakers appear, no segment is empty, no segment repeats "
                "another verbatim, and every beat of the outline produced at least one "
                "segment."
            ),
            method=(
                "Four independent structural checks over the script. Duplicates are compared "
                "on the word sequence rather than the raw string, so a difference in "
                "punctuation or whitespace does not disguise a repeat. Beat coverage is the "
                "one check that looks backwards: a beat with no segments means the script "
                "node silently dropped part of the plan."
            ),
            thresholds={"duplicate_comparison": "casefolded word sequence"},
        )

    def check(self, ctx: GateContext) -> GateReport:
        violations: list[Violation] = []
        declared = set(ctx.format_spec.speaker_names())

        undeclared = 0
        empty = 0
        duplicates = 0
        seen: dict[str, str] = {}
        for segment in ctx.script.segments:
            if segment.speaker not in declared:
                undeclared += 1
                violations.append(
                    Violation(
                        target_id=segment.id,
                        message=(
                            f"Speaker '{segment.speaker}' is not declared in the format "
                            f"spec ({', '.join(sorted(declared))})."
                        ),
                    )
                )
            if not segment.text.strip():
                empty += 1
                violations.append(Violation(target_id=segment.id, message="Segment text is empty."))
            key = " ".join(words(segment.text))
            if key and key in seen:
                duplicates += 1
                violations.append(
                    Violation(
                        target_id=segment.id,
                        message=f"Segment duplicates {seen[key]} verbatim.",
                    )
                )
            elif key:
                seen[key] = segment.id

        covered = {s.beat_id for s in ctx.script.segments}
        uncovered = 0
        for beat in ctx.outline.beats:
            if beat.id not in covered:
                uncovered += 1
                violations.append(
                    Violation(
                        target_id=beat.id,
                        message=f"Beat “{beat.title}” produced no segments.",
                    )
                )

        measurements = {
            "segments": len(ctx.script.segments),
            "declared_speakers": sorted(declared),
            "undeclared_speaker_segments": undeclared,
            "empty_segments": empty,
            "duplicate_segments": duplicates,
            "beats": len(ctx.outline.beats),
            "beats_without_segments": uncovered,
        }
        return failed(self, violations, measurements) if violations else passed(self, measurements)
