"""The ``audio_script`` node: the script prepared for speech synthesis.

One model call per beat adds audio tags in square brackets and writes numbers
and abbreviations the way they are spoken. A deterministic guard
(``pipeline/audio_tags.guard``) then proves that every line still says exactly
what the script says. A refused line is asked for once more; if it still fails
it is spoken as written, untagged, and marked ``fallback``. The node never fails
a run over a tag.

Beats are cached on their own, under a key of the beat's lines and the line
before them, so an edit to one line re-tags only its beat.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.llm.base import CompletionRequest, LLMError, Message
from app.pipeline.audio_tags import (
    AUDIO_SCHEMA,
    AUDIO_SYSTEM,
    DEFAULT_MAX_TAGS,
    TAG_LANGUAGES,
    ScriptLine,
    fallback_line,
    lines_from_segments,
    prompt_fields,
    read_answer,
    render_message,
    retry_message,
    speakers_text,
)
from app.pipeline.framework.artifacts import hash_payload
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.schemas.audio import AudioLine, AudioScript
from app.schemas.document import ParsedDocument
from app.schemas.pipeline import FormatSpec, Script

DEFAULT_MODEL = "claude-sonnet-5-5"


class AudioScriptInput(BaseModel):
    script: Script
    format_spec: FormatSpec
    #: Only its language is read. Optional, so a script seeded on its own still runs.
    parsed: ParsedDocument | None = None


class AudioScriptNode:
    name = "audio_script"
    title = "Audio-Skript (Tags)"
    version = "1.0"
    Input: type[BaseModel] = AudioScriptInput
    Output: type[BaseModel] = AudioScript
    produces = "audio_script"

    doc = NodeDoc(
        summary="Prepares the script for speech synthesis: audio tags in square brackets "
        "and numbers written as spoken, with every word checked against the script.",
        detail=[
            "Calls the model once per beat. The model sees the speakers with their voice "
            "notes, the line before the beat for continuity, and the beat's lines with their "
            "ids and kinds. It returns each line with audio tags such as [curious] or "
            "[short pause], and lists every spoken form it wrote, for example '1,5 %' as "
            "'eins Komma fünf Prozent'.",
            "A deterministic guard then checks every line: it removes the tags, applies the "
            "declared spoken forms to the script's line and compares the two word by word. "
            "Case and punctuation do not count. It also refuses more tags than allowed, a "
            "tag pressed against a word, and a spoken form whose original is not in the line.",
            "Refused lines are asked for once more, with the reason. A line that still "
            "fails is spoken as written, without tags, and marked as a fallback. Citations "
            "hang on the segment, not on the text, so they stay aligned either way.",
            "Each beat is cached on its own. An edit to one line re-tags only its beat; "
            "forcing the run ignores this cache.",
        ],
        inputs={
            "script": "The segments to prepare, in order, with speaker, kind and beat.",
            "format_spec": "The speakers' roles and voice notes, which shape the tags.",
            "parsed": "Only the document language. Optional.",
        },
        output="An AudioScript: per segment the original text, the spoken text, the tagged "
        "text that goes to the speech model, the spoken forms and tags, and the guard result.",
        failure_modes=[
            "The script has no segments.",
            "The model provider fails. Beats already tagged stay cached.",
        ],
        cost="One call per beat that is not cached, plus at most one retry per beat. Output "
        "is about as long as the script; on Sonnet 5.5 roughly $0.10–0.20 for a "
        "15-minute episode.",
    )

    params = [
        NodeParam(
            key="system_prompt",
            label="System prompt",
            type="prompt",
            default=AUDIO_SYSTEM,
            description=(
                "The rules for words and tags. Keep the demand to list every spoken form: "
                "the guard accepts no other change to a line."
            ),
        ),
        NodeParam(
            key="model",
            label="Model",
            type="model",
            description="Empty falls back to DEFAULT_MODEL from the environment. Placing "
            "tags is light work; the audio pipeline sets a mid-size model.",
        ),
        NodeParam(
            key="temperature",
            label="Temperature",
            type="float",
            default=0.3,
            minimum=0.0,
            maximum=2.0,
            description="Ignored by models that do not accept sampling.",
        ),
        NodeParam(
            key="tag_language",
            label="Tag language",
            type="select",
            default="en",
            options=list(TAG_LANGUAGES),
            description=(
                "ElevenLabs documents its tags in English only; whether German tags work as "
                "well in German text is open. The experiment 'Audio ausprobieren' compares both."
            ),
        ),
        NodeParam(
            key="max_tags_per_line",
            label="Max tags per line",
            type="int",
            default=DEFAULT_MAX_TAGS,
            minimum=1,
            maximum=6,
        ),
        NodeParam(
            key="retry",
            label="Retry refused lines",
            type="bool",
            default=True,
            description="Ask once more for the lines the guard refused, before falling back.",
            advanced=True,
        ),
        NodeParam(
            key="max_tokens",
            label="Max output tokens per beat",
            type="int",
            default=8000,
            minimum=1000,
            maximum=64000,
            advanced=True,
        ),
    ]

    def run(self, inp: AudioScriptInput, ctx: NodeContext) -> AudioScript:
        if not inp.script.segments:
            raise NodeError("the script has no segments to prepare")

        model = ctx.model(DEFAULT_MODEL)
        tag_language = str(ctx.get("tag_language") or "en")
        language = inp.parsed.language if inp.parsed is not None else "as written"
        speakers = speakers_text(inp.format_spec)

        beats = _beats(lines_from_segments(inp.script.segments))
        result: list[AudioLine] = []
        previous: ScriptLine | None = None
        reused = 0
        for position, lines in enumerate(beats):
            fields = prompt_fields(
                lines,
                speakers=speakers,
                language=language,
                tag_language=tag_language,
                max_tags=self._max_tags(ctx),
                previous=previous,
            )
            key = hash_payload(
                {
                    "node": self.name,
                    "version": self.version,
                    "config": ctx.config,
                    "model": model,
                    "fields": fields,
                    "segments": [[line.id, line.beat_id] for line in lines],
                }
            )
            cached = None if ctx.force else ctx.artifacts.get_step(key)
            if cached is not None:
                payload = ctx.artifacts.get_raw(cached)
                reused += 1
            else:
                ctx.progress(f"tagging beat {position + 1} of {len(beats)}")
                payload = self._tag_beat(ctx, model, fields, lines)
                cached = ctx.artifacts.put_raw("audio_beat", payload).hash
                ctx.artifacts.put_step(key, cached)
            result.extend(AudioLine.model_validate(item) for item in payload["lines"])
            previous = lines[-1]

        script = AudioScript(lines=result, model=model, tag_language=tag_language)
        if reused:
            ctx.progress(f"{reused} beat(s) reused from an earlier run")
        fallbacks = script.fallbacks()
        if fallbacks:
            ctx.progress(
                f"{len(fallbacks)} line(s) failed the word check and are spoken untagged: "
                + ", ".join(line.segment_id for line in fallbacks[:5])
            )
        return script

    def _max_tags(self, ctx: NodeContext) -> int:
        return int(ctx.get("max_tags_per_line") or DEFAULT_MAX_TAGS)

    def _tag_beat(
        self,
        ctx: NodeContext,
        model: str,
        fields: dict[str, str],
        lines: list[ScriptLine],
    ) -> dict[str, Any]:
        """Tag one beat, retry the refused lines once, fall back for the rest."""
        max_tags = self._max_tags(ctx)
        first = render_message(fields)
        answer, payload = self._ask(ctx, model, [Message(role="user", content=first)])
        tagged, problems = read_answer(payload, lines, max_tags=max_tags)

        if problems and ctx.get("retry", True):
            retried = [line for line in lines if line.id in problems]
            _text, again = self._ask(
                ctx,
                model,
                [
                    Message(role="user", content=first),
                    Message(role="assistant", content=answer or "{}"),
                    Message(role="user", content=retry_message(problems, retried)),
                ],
            )
            second, still = read_answer(again, retried, max_tags=max_tags)
            fixed = {line.segment_id: line for line in second if line.segment_id not in still}
            tagged = [fixed.get(line.segment_id, line) for line in tagged]

        if len(tagged) != len(lines):  # pragma: no cover - read_answer returns every line
            tagged = [fallback_line(line, "the answer lost a line") for line in lines]
        return {"lines": [line.model_dump(mode="json") for line in tagged]}

    def _ask(self, ctx: NodeContext, model: str, messages: list[Message]) -> tuple[str, Any]:
        """The answer text and its JSON; ``None`` JSON when the answer is not JSON."""
        completion = ctx.llm.complete(
            CompletionRequest(
                model=model,
                system=str(ctx.get("system_prompt") or AUDIO_SYSTEM),
                messages=messages,
                max_tokens=int(ctx.get("max_tokens", 8000)),
                temperature=ctx.get("temperature", 0.3),
                json_schema=AUDIO_SCHEMA,
                cache_system=True,
            )
        )
        try:
            return completion.text, completion.json_payload()
        except LLMError:
            return completion.text, None


def _beats(lines: list[ScriptLine]) -> list[list[ScriptLine]]:
    """Consecutive lines of the same beat, in script order."""
    beats: list[list[ScriptLine]] = []
    for line in lines:
        if beats and beats[-1][-1].beat_id == line.beat_id:
            beats[-1].append(line)
        else:
            beats.append([line])
    return beats


register_node(AudioScriptNode())
