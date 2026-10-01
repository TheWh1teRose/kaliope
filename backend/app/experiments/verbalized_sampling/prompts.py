"""System prompts for the VS call and the plain baseline call.

Both calls share one base prompt, which defaults to the production ``script``
node prompt. The VS call appends the VS instruction; the baseline call appends
only the script node's own closing line. Editing the base therefore changes
both calls the same way, and the comparison stays fair.

The VS instruction has three placeholders, ``{{k}}``, ``{{threshold_clause}}``
and ``{{probability_definition}}``. Only those three are filled here; anything
else in ``{{…}}`` is left as typed, so a user's own text survives untouched.
"""

from __future__ import annotations

import re

from app.experiments.verbalized_sampling.options import ProbabilityFormat, VSOptions
from app.pipeline.nodes.script import _SYSTEM as SCRIPT_SYSTEM

#: The script node's last line. The baseline keeps it; the VS block replaces it
#: with its own output instructions.
BASELINE_SUFFIX = "Return JSON only."


def _strip_suffix(prompt: str) -> str:
    text = prompt.rstrip()
    if text.endswith(BASELINE_SUFFIX):
        text = text[: -len(BASELINE_SUFFIX)].rstrip()
    return text


#: Default shared base: the production script prompt without its closing line.
DEFAULT_BASE_PROMPT = _strip_suffix(SCRIPT_SYSTEM)

#: Kalliope's default. Close to the paper's VS-Standard wording (§G.3), adapted
#: to a beat, plus one sentence of ours that steers the variety into framing,
#: because the grounding rules forbid varying the facts.
DEFAULT_VS_INSTRUCTION = """\
Verbalized sampling:
Generate {{k}} different versions of this beat. Every version must follow all rules
above, use the same passages and speakers, and stay close to the word budget.
The versions should differ in how they explain: framing, opening question,
analogies, order and the interplay of the speakers. Do not vary the facts.
{{threshold_clause}}
Return the versions in JSON format with the key "responses" (a list of objects).
Each object must include:
- segments: the beat as speaker segments, exactly as for a single beat.
- probability: {{probability_definition}}
Give ONLY the JSON object, with no explanations or extra text."""

#: The paper's VS-Standard prompt (§G.3), word for word, with two changes: each
#: response carries ``segments`` instead of ``text`` (the structured output
#: schema requires the script shape), and the target-length sentence is left
#: out because the beat's word budget is already in the user message.
PAPER_VS_INSTRUCTION = """\
Generate {{k}} responses to the input prompt.
{{threshold_clause}}
Return the responses in JSON format with the key: "responses" (list of dicts).
Each dictionary must include:
- segments: the response as speaker segments (no explanation or extra text).
- probability: {{probability_definition}}
Give ONLY the JSON object, with no explanations or extra text."""

#: Paper §F.4, verbatim.
PROBABILITY_DEFINITIONS: dict[ProbabilityFormat, str] = {
    "explicit": (
        "the estimated probability from 0.0 to 1.0 of this response given the input prompt "
        "(relative to the full distribution)."
    ),
    "implicit": "how likely this response would be (from 0.0 to 1.0).",
    "relative": (
        "the probability between 0.0 and 1.0, reflecting the relative likelihood of this "
        "response given the input."
    ),
    "percentage": (
        "the probability of this response relative to the full distribution, expressed as a "
        "percentage from 0% to 100%."
    ),
    "confidence": (
        "the normalized likelihood score between 0.0 and 1.0 that indicates how representative "
        "or typical this response is compared to the full distribution."
    ),
}

#: Paper §G.3 (VS-CoT), added in front of the output instructions.
COT_CLAUSE = (
    'First, provide a single "reasoning" field as a string, detailing your step-by-step '
    "thought process. Then return the versions as described below."
)

_PLACEHOLDER = re.compile(r"\{\{\s*(k|threshold_clause|probability_definition)\s*\}\}")
_K_PLACEHOLDER = re.compile(r"\{\{\s*k\s*\}\}")
_PROBABILITY_LINE = re.compile(r"^.*\{\{\s*probability_definition\s*\}\}.*$\n?", re.MULTILINE)


def threshold_clause(options: VSOptions) -> str:
    """Paper §G.3 wording, kept verbatim so results compare to the paper."""
    if options.threshold_mode == "full":
        return "Randomly sample the responses from the full distribution."
    if options.threshold_mode == "below":
        return (
            "Randomly sample the responses from the distribution, with the probability of each "
            f"response must be below {options.threshold:g}."
        )
    return ""


def render_vs_instruction(instruction: str, options: VSOptions) -> tuple[str, list[str]]:
    """Fill the three VS placeholders. Returns the text and warnings for the user."""
    warnings: list[str] = []
    if not _K_PLACEHOLDER.search(instruction):
        warnings.append(
            "Die VS-Anweisung enthält kein {{k}}: die Zahl der Fassungen steht nicht im Prompt "
            f"und wird nur beim Prüfen der Antwort verwendet (erwartet {options.k})."
        )

    text = instruction
    if not options.verbalizes_probability:
        # The "list" control asks for the same versions without probabilities.
        text = _PROBABILITY_LINE.sub("", text)

    values = {
        "k": str(options.k),
        "threshold_clause": threshold_clause(options),
        "probability_definition": PROBABILITY_DEFINITIONS[options.probability_format],
    }
    lines: list[str] = []
    for line in text.split("\n"):
        filled = _PLACEHOLDER.sub(lambda m: values[m.group(1)], line)
        # A placeholder alone on its line that renders empty leaves no blank line.
        if not filled.strip() and _PLACEHOLDER.search(line):
            continue
        lines.append(filled)
    rendered = "\n".join(lines).strip()

    if options.variant == "cot":
        rendered = f"{rendered}\n{COT_CLAUSE}"
    return rendered, warnings


def vs_system_prompt(base: str, rendered_instruction: str) -> str:
    return f"{base.rstrip()}\n\n{rendered_instruction.strip()}"


def baseline_system_prompt(base: str) -> str:
    return f"{_strip_suffix(base)}\n\n{BASELINE_SUFFIX}"


def follow_up_message(options: VSOptions) -> str:
    """VS-Multi, later turns (paper §G.3)."""
    return f"Generate {options.k} alternative versions of this beat."
