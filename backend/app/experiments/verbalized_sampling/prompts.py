"""System prompts for the VS call and the plain baseline call.

Both calls share one base prompt, which defaults to the production ``script``
node prompt. The VS call appends the VS instruction; the baseline call appends
only the script node's own closing line. Editing the base therefore changes
both calls the same way, and the comparison stays fair.

The VS instruction has two placeholders, ``{{k}}`` and
``{{probability_definition}}``. Only those are filled here; anything else in
``{{…}}`` is left as typed, so a user's own text survives untouched.
"""

from __future__ import annotations

import re

from app.experiments.verbalized_sampling.options import VSOptions
from app.pipeline.nodes.script import _SYSTEM as SCRIPT_SYSTEM

#: The script node's last line. Both calls strip it, then the baseline puts it
#: back and the VS block replaces it with its own output instructions.
BASELINE_SUFFIX = "Return JSON only."

#: One 0–1 definition, the paper's explicit wording (§F.4), used by both the
#: Kalliope and the paper VS-Standard texts.
PROBABILITY_DEFINITION = (
    "the estimated probability from 0.0 to 1.0 of this response given the input prompt "
    "(relative to the full distribution)."
)


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
Return the responses in JSON format with the key: "responses" (list of dicts).
Each dictionary must include:
- segments: the response as speaker segments (no explanation or extra text).
- probability: {{probability_definition}}
Give ONLY the JSON object, with no explanations or extra text."""

_PLACEHOLDER = re.compile(r"\{\{\s*(k|probability_definition)\s*\}\}")
_K_PLACEHOLDER = re.compile(r"\{\{\s*k\s*\}\}")


def render_vs_instruction(instruction: str, options: VSOptions) -> tuple[str, list[str]]:
    """Fill the VS placeholders. Returns the text and warnings for the user."""
    warnings: list[str] = []
    if not _K_PLACEHOLDER.search(instruction):
        warnings.append(
            "Die VS-Anweisung enthält kein {{k}}: die Zahl der Fassungen steht nicht im Prompt "
            f"und wird nur beim Prüfen der Antwort verwendet (erwartet {options.k})."
        )
    values = {
        "k": str(options.k),
        "probability_definition": PROBABILITY_DEFINITION,
    }
    rendered = _PLACEHOLDER.sub(lambda m: values[m.group(1)], instruction).strip()
    return rendered, warnings


def vs_system_prompt(base: str, rendered_instruction: str) -> str:
    return f"{_strip_suffix(base)}\n\n{rendered_instruction.strip()}"


def baseline_system_prompt(base: str) -> str:
    return f"{_strip_suffix(base)}\n\n{BASELINE_SUFFIX}"
