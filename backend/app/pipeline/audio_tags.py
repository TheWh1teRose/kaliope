"""Audio tags for speech synthesis, and the guard that keeps the words intact.

Shared by the ``audio_script`` node and the "Audio ausprobieren" experiment, so
an experiment sends exactly what production sends and reads the answer the same
way.

A model places the tags (``[curious]``, ``[short pause]``) and writes numbers and
abbreviations the way they are spoken. It may not change anything else: the
script was checked against its sources, and a changed word can change a fact.
The guard proves that deterministically. It removes the tags, applies the
spoken forms the model declared to the original line, and compares the two word
by word. Case and punctuation do not count, because both shape delivery
(capitals for emphasis, an ellipsis for a hesitation) without changing what is
said.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.schemas.audio import AudioLine, SpokenForm
from app.schemas.pipeline import FormatSpec, Segment

#: One audio tag: a short instruction in square brackets, on one line.
TAG = re.compile(r"\[([^\[\]\n]{1,60})\]")
#: A tag pressed against a letter or digit, on either side.
_GLUED = re.compile(r"\w\[[^\[\]\n]{1,60}\]|\[[^\[\]\n]{1,60}\]\w")
#: Everything that is neither a word character nor whitespace.
_PUNCTUATION = re.compile(r"[^\w\s]|_")

#: How many tags one line may carry unless the node is configured otherwise.
DEFAULT_MAX_TAGS = 3

TAG_LANGUAGES: dict[str, str] = {"en": "English", "de": "German"}

AUDIO_SYSTEM = """\
You prepare one beat of a podcast script for speech synthesis with ElevenLabs
(Eleven v4, Text to Dialogue). You add audio tags that direct the delivery, and
you write numbers and abbreviations the way a speaker says them. You never change
what is said.

Words — this is the part that matters:
- Keep every word of every line, in order. Do not add, drop, reorder or rephrase
  words, and do not correct grammar or style. The script was checked against its
  sources; a changed word can change a fact.
- The one allowed change is a spoken form: a number, unit, percentage, year, date,
  symbol or abbreviation written out the way the speaker says it, in the language
  of the line ("1,5 %" -> "eins Komma fünf Prozent", "CO₂" -> "C O zwei",
  "z. B." -> "zum Beispiel"). List every such change in spoken_forms, with the
  exact original text and its spoken form. A change that is not listed is an error.
- Punctuation and capitals may change for pacing and emphasis: an ellipsis (…) for
  a hesitation or a trailing thought, a dash (–) for a cut-off, capitals for a
  stressed word.

Audio tags:
- A tag is a short delivery instruction in square brackets, placed directly before
  the words it shapes, with a space on both sides: [curious], [thoughtful],
  [warmly], [surprised], [excited], [serious], [chuckles], [sighs],
  [short pause], [jumping in].
- Use them sparingly: about one tag every two or three lines, never one on every
  line, and no more per line than the limit you are given.
- A tag must suit the speaker's role and voice note.
- No sound effects ([applause], [music]) and no stage directions ([standing],
  [pacing]).
- Lines of kind "claim" state facts: give them calm delivery tags only, never
  laughter.

Return JSON only: one entry per input line, with the same id, in the same order.
"""

#: The node's user message for one beat, as a template over named fields.
AUDIO_TEMPLATE = (
    "Speakers:\n{{speakers}}\n\n"
    "Language of the lines: {{language}}\n"
    "Write the audio tags in {{tag_language}}. At most {{max_tags}} tags in one line.\n\n"
    "{{context}}"
    "Lines, one per entry as [id] speaker (kind): text:\n{{lines}}"
)

AUDIO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "tagged": {"type": "string"},
                    "spoken_forms": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "original": {"type": "string"},
                                "spoken": {"type": "string"},
                            },
                            "required": ["original", "spoken"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "tagged", "spoken_forms"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lines"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------- lines


@dataclass(frozen=True)
class ScriptLine:
    """One line as the tagging prompt sees it."""

    id: str
    speaker: str
    text: str
    kind: str | None = None
    beat_id: str | None = None

    def formatted(self) -> str:
        kind = f" ({self.kind})" if self.kind else ""
        return f"[{self.id}] {self.speaker}{kind}: {self.text}"


def lines_from_segments(segments: Iterable[Segment]) -> list[ScriptLine]:
    return [
        ScriptLine(id=s.id, speaker=s.speaker, text=s.text, kind=s.kind, beat_id=s.beat_id)
        for s in segments
    ]


#: ``[id] Speaker (kind): text``; id and kind are optional.
_LINE = re.compile(
    r"^\s*(?:\[(?P<id>[^\]\s]+)\]\s*)?"
    r"(?P<speaker>[^:\[\]()]{1,60}?)\s*"
    r"(?:\((?P<kind>claim|pedagogy)\))?\s*:\s*(?P<text>\S.*?)\s*$"
)


def parse_lines(text: str) -> tuple[list[ScriptLine], list[str]]:
    """Read typed lines (``Moderator: …`` or ``[id] Moderator (claim): …``).

    Blank lines are skipped. A line without an id gets ``l001``, ``l002`` … by
    position. Returns the lines and a problem per line that has no speaker.
    """
    lines: list[ScriptLine] = []
    problems: list[str] = []
    used: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        match = _LINE.match(raw)
        if match is None:
            problems.append(f"line {number} has no 'Speaker: text' form")
            continue
        line_id = match.group("id") or f"l{len(lines) + 1:03d}"
        if line_id in used:
            problems.append(f"line {number} repeats the id '{line_id}'")
            continue
        used.add(line_id)
        lines.append(
            ScriptLine(
                id=line_id,
                speaker=match.group("speaker").strip(),
                text=match.group("text"),
                kind=match.group("kind"),
            )
        )
    return lines, problems


def speakers_text(format_spec: FormatSpec) -> str:
    """The speakers as the script node describes them: name, role, voice note."""
    rows = []
    for speaker in format_spec.speakers:
        note = f": {speaker.voice_note}" if speaker.voice_note else ""
        rows.append(f"- {speaker.name} ({speaker.role}){note}")
    return "\n".join(rows)


def prompt_fields(
    lines: Sequence[ScriptLine],
    *,
    speakers: str,
    language: str,
    tag_language: str,
    max_tags: int,
    previous: ScriptLine | None = None,
) -> dict[str, str]:
    """The named values ``AUDIO_TEMPLATE`` is rendered from."""
    context = ""
    if previous is not None:
        context = (
            "The line just before these, for continuity only (do not return it):\n"
            f"{previous.speaker}: {previous.text}\n\n"
        )
    return {
        "speakers": speakers,
        "language": language,
        "tag_language": TAG_LANGUAGES.get(tag_language, tag_language),
        "max_tags": str(max_tags),
        "context": context,
        "lines": "\n".join(line.formatted() for line in lines),
    }


def render_message(fields: dict[str, str]) -> str:
    text = AUDIO_TEMPLATE
    for name, value in fields.items():
        text = text.replace("{{" + name + "}}", value)
    return text


# --------------------------------------------------------------------- guard


def strip_tags(text: str) -> str:
    return re.sub(r"\s{2,}", " ", TAG.sub(" ", text)).strip()


def tags_of(text: str) -> list[str]:
    return [f"[{match}]" for match in TAG.findall(text)]


def words(text: str) -> list[str]:
    """The spoken words: tags, punctuation and case removed."""
    return _PUNCTUATION.sub(" ", strip_tags(text)).lower().split()


def apply_spoken_forms(text: str, forms: Sequence[SpokenForm]) -> str:
    """Replace every occurrence of each original, longest original first."""
    for form in sorted(forms, key=lambda f: len(f.original), reverse=True):
        text = text.replace(form.original, form.spoken)
    return text


@dataclass
class GuardResult:
    ok: bool
    #: ``text`` with the spoken forms applied.
    spoken: str
    tags: list[str] = field(default_factory=list)
    problem: str | None = None


def guard(
    text: str,
    tagged: str,
    forms: Sequence[SpokenForm],
    *,
    max_tags: int = DEFAULT_MAX_TAGS,
) -> GuardResult:
    """Check that ``tagged`` says exactly ``text``, up to the declared spoken forms."""
    spoken = apply_spoken_forms(text, forms)
    tags = tags_of(tagged)

    def refuse(problem: str) -> GuardResult:
        return GuardResult(ok=False, spoken=spoken, tags=tags, problem=problem)

    for form in forms:
        if not form.original.strip():
            return refuse("a spoken form has an empty original")
        if not form.spoken.strip():
            return refuse(f"the spoken form of '{form.original}' is empty")
        if form.original not in text:
            return refuse(f"the spoken form '{form.original}' is not in the line")
    if len(tags) > max_tags:
        return refuse(f"{len(tags)} tags, at most {max_tags} are allowed")
    if _GLUED.search(tagged):
        return refuse("a tag touches a word; it needs a space on both sides")
    if _brackets(strip_tags(tagged)) > _brackets(strip_tags(text)):
        return refuse("an unclosed or empty bracket")

    expected, actual = words(spoken), words(tagged)
    if expected != actual:
        return refuse(_difference(expected, actual))
    return GuardResult(ok=True, spoken=spoken, tags=tags)


def _brackets(text: str) -> int:
    return text.count("[") + text.count("]")


def _difference(expected: list[str], actual: list[str]) -> str:
    """The first stretch where the words differ, in plain words."""
    matcher = difflib.SequenceMatcher(a=expected, b=actual, autojunk=False)
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op == "equal":
            continue
        removed = " ".join(expected[a1:a2])
        added = " ".join(actual[b1:b2])
        if op == "delete":
            return f"words missing: '{removed}'"
        if op == "insert":
            return f"words added: '{added}'"
        return f"words changed: '{removed}' became '{added}'"
    return "the words differ"  # pragma: no cover - unequal lists always have an opcode


# ------------------------------------------------------------------- answers


def read_answer(
    payload: Any,
    lines: Sequence[ScriptLine],
    *,
    max_tags: int = DEFAULT_MAX_TAGS,
) -> tuple[list[AudioLine], dict[str, str]]:
    """Turn a model answer into audio lines, guarded.

    Returns every input line in order, and ``{line id: problem}`` for the lines
    that failed. A failed line is the plain original with ``guard="fallback"``,
    so the result is always usable.
    """
    answers: dict[str, dict[str, Any]] = {}
    entries = payload.get("lines") if isinstance(payload, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            answers.setdefault(entry["id"], entry)

    result: list[AudioLine] = []
    problems: dict[str, str] = {}
    for line in lines:
        entry = answers.get(line.id)
        if entry is None:
            problem = "no answer for this line"
        else:
            forms = _forms(entry.get("spoken_forms"))
            tagged = " ".join(str(entry.get("tagged") or "").split())
            checked = guard(line.text, tagged, forms, max_tags=max_tags)
            if checked.ok:
                result.append(
                    AudioLine(
                        segment_id=line.id,
                        beat_id=line.beat_id,
                        speaker=line.speaker,
                        kind=line.kind,
                        text=line.text,
                        spoken=checked.spoken,
                        tagged=tagged,
                        spoken_forms=forms,
                        tags=checked.tags,
                    )
                )
                continue
            problem = checked.problem or "the guard refused the line"
        problems[line.id] = problem
        result.append(fallback_line(line, problem))
    return result, problems


def fallback_line(line: ScriptLine, problem: str) -> AudioLine:
    """The original line, untagged: what is used when the guard refuses."""
    return AudioLine(
        segment_id=line.id,
        beat_id=line.beat_id,
        speaker=line.speaker,
        kind=line.kind,
        text=line.text,
        spoken=line.text,
        tagged=line.text,
        guard="fallback",
        problem=problem,
    )


def retry_message(problems: dict[str, str], lines: Sequence[ScriptLine]) -> str:
    """The follow-up that asks once more for the lines the guard refused."""
    by_id = {line.id: line for line in lines}
    rows = [
        f"{by_id[line_id].formatted()}\n  Problem: {problem}"
        for line_id, problem in problems.items()
        if line_id in by_id
    ]
    return (
        "These lines failed the word check. Keep every word; change only spoken forms "
        "and list each one. Return JSON for these lines only, same ids:\n\n" + "\n".join(rows)
    )


def _forms(value: Any) -> list[SpokenForm]:
    forms: list[SpokenForm] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            forms.append(
                SpokenForm(
                    original=str(item.get("original", "")), spoken=str(item.get("spoken", ""))
                )
            )
    return forms
