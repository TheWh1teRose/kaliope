"""Test doubles and generated fixtures.

The behavioural tests must run on a fresh checkout, before any corpus exists,
so they build their own PDF rather than reaching for one. It is generated, not
a real publisher's document, and no assertion depends on its wording — only on
structural facts the pipeline is supposed to produce for any document.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pymupdf as fitz

from app.llm.base import Completion, CompletionRequest, Usage, parse_json

TOPICS: list[tuple[str, str]] = [
    (
        "Regelkreise",
        "Der Hypothalamus steuert die Hypophyse, die ihrerseits zahlreiche periphere "
        "Drüsen reguliert. Diese Regelkreise beruhen auf negativer Rückkopplung, "
        "wodurch die Konzentration der Hormone im Blut konstant gehalten wird. ",
    ),
    (
        "Schilddrüse",
        "Die Schilddrüse produziert Thyroxin und Triiodthyronin. Beide Hormone steigern "
        "den Grundumsatz und beeinflussen Wachstum sowie Entwicklung des Nervensystems. ",
    ),
    (
        "Nebenniere",
        "Die Nebennierenrinde bildet Kortisol, Aldosteron und Androgene. Das Nebennierenmark "
        "schüttet Adrenalin aus und bereitet den Körper auf kurzfristige Belastungen vor. ",
    ),
    (
        "Pankreas",
        "Die Langerhansschen Inseln enthalten Alpha- und Betazellen. Insulin senkt den "
        "Blutzuckerspiegel, während Glukagon ihn anhebt und den Stoffwechsel ausgleicht. ",
    ),
    (
        "Gonaden",
        "Die Keimdrüsen bilden Sexualhormone, die Reifung und Fortpflanzung steuern. Ihre "
        "Ausschüttung unterliegt ebenfalls einem übergeordneten Regelkreis. ",
    ),
    (
        "Zusammenfassung",
        "Das Hormon- und Nervensystem arbeiten eng zusammen und ergänzen einander. "
        "Nervenimpulse wirken schnell, Hormone langsamer und dafür anhaltend. ",
    ),
]

EXERCISE_TEXT = (
    "Aufgabe: Beschriften Sie die Abbildung und ordnen Sie jedem Hormon sein Zielorgan zu."
)
CALLOUT_TEXT = (
    "Steigt die Hormonkonzentration im Blut, sinkt die Ausschüttung des übergeordneten "
    "Hormons. Dieses Prinzip heißt negative Rückkopplung."
)


def write_learning_pdf(path: Path, *, repeats: int = 6, thin: bool = False) -> Path:
    """A two-column German learning PDF with a running head, folio and a callout.

    ``thin`` produces a document with almost no narratable text, which is what
    AC-BL-2 needs to see the content budget refuse a run.
    """
    doc = fitz.open()
    for index, (title, body) in enumerate(TOPICS):
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 40), "Kursmaterial Physiologie", fontsize=8)
        page.insert_text((520, 802), str(index + 1), fontsize=8)
        page.insert_text((60, 90), f"{index + 1}. {title}", fontsize=18)

        if not thin:
            page.insert_textbox(fitz.Rect(60, 110, 285, 740), body * repeats, fontsize=10)
            page.insert_textbox(fitz.Rect(310, 110, 535, 740), body * repeats, fontsize=10)

        if index == 2 and not thin:
            page.draw_rect(fitz.Rect(60, 600, 535, 690), fill=(0.93, 0.93, 0.86))
            page.insert_textbox(fitz.Rect(66, 606, 529, 686), CALLOUT_TEXT, fontsize=11)

        if index == 4 and not thin:
            page.insert_textbox(fitz.Rect(60, 600, 535, 700), EXERCISE_TEXT, fontsize=10)

    doc.save(path)
    doc.close()
    return path


class StubProvider:
    """Deterministic stand-in that honours each node's JSON contract.

    It exists so the framework, baseline, gate and API tests exercise the real
    code paths — prompts, schemas, parsing, anchor resolution — without a
    network call or a bill.
    """

    name = "anthropic"

    def __init__(self) -> None:
        self.calls: list[CompletionRequest] = []
        #: Set to make every subsequent call raise, for AC-FW-5.
        self.fail_with: Exception | None = None

    def available(self) -> bool:
        return True

    def complete(self, request: CompletionRequest) -> Completion:
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append(request)
        payload = self._respond(request)
        body = json.dumps(payload, ensure_ascii=False)
        return Completion(
            text=body,
            model_id=request.model,
            usage=Usage(input_tokens=200, output_tokens=len(body) // 4),
            latency_ms=1,
        )

    # ------------------------------------------------------------ responses

    def _respond(self, request: CompletionRequest) -> Any:
        system = request.system or ""
        text = request.messages[-1].content

        if "You label blocks" in system:
            return self._zones(text)
        if "You write the learning objectives" in system:
            return self._objectives()
        if "serve the given listener objectives" in system:
            return self._objective_selection(text)
        if "You choose which passages" in system:
            return self._selection(text)
        if "from the listener objectives" in system:
            return self._objective_outline(text, request.model)
        if "You plan the running order" in system:
            return self._outline(text, request.model)
        if "You write one beat" in system:
            return self._beat(text)
        if "You review an outline or a script" in system:
            return self._critic(text)
        if "You revise one part of a script" in system:
            return self._revise_script(text)
        if "You revise one beat of an outline" in system:
            return self._revise_outline(text)
        return {}

    @staticmethod
    def _objectives() -> Any:
        return {
            "objectives": [
                {
                    "id": "o0",
                    "text": "Die Hörerin kann die Regelkreise in eigenen Worten erklären.",
                    "bloom_level": "understand",
                    "derivation": "Das gewünschte Ergebnis verlangt, den Stoff zu erklären.",
                },
                {
                    "id": "o1",
                    "text": "Die Hörerin kann Hormone ihren Drüsen zuordnen.",
                    "bloom_level": "remember",
                    "derivation": "Zuordnen ist die Erinnerungsleistung hinter dem Erklären.",
                },
            ],
            "rationale": "Vom gewünschten Ergebnis abgeleitet, am Dokument konkretisiert.",
        }

    def _zones(self, text: str) -> Any:
        blocks = parse_json(text.split("\n\n", 1)[1])
        labels = []
        for block in blocks:
            body = block["text"]
            if body.startswith("Aufgabe"):
                zone = "exercise"
            elif block["in_filled_rect"]:
                zone = "emphasis_callout"
            else:
                zone = "body"
            labels.append({"block_index": block["block_index"], "zone": zone, "confidence": 0.95})
        return {"labels": labels}

    @staticmethod
    def _ids(text: str) -> list[str]:
        return [line.split("]")[0][1:] for line in text.splitlines() if line.startswith("[b")]

    def _selection(self, text: str) -> Any:
        ids = self._ids(text)
        return {
            "learning_goals": [
                {"id": "g0", "text": "Die Regelkreise des Hormonsystems erklären"},
                {"id": "g1", "text": "Hormone ihren Drüsen zuordnen"},
            ],
            "selected_blocks": [
                {"block_id": block_id, "reason": "trägt Inhalt", "goal_ids": ["g0"]}
                for block_id in ids
            ],
            "rationale": "Alle inhaltstragenden Passagen ausgewählt.",
        }

    def _objective_selection(self, text: str) -> Any:
        ids = self._ids(text)
        return {
            "selected_blocks": [
                {
                    "block_id": block_id,
                    "reason": "trägt zum Hörziel bei",
                    "objective_ids": ["o0"],
                }
                for block_id in ids
            ],
            "rationale": "Passagen gewählt, die die Hörziele tragen.",
        }

    def _objective_outline(self, text: str, model: str) -> Any:
        ids = self._ids(text)
        groups = [ids[i::3] for i in range(3)]
        return {
            "beats": [
                {
                    "title": f"Beat {index + 1}",
                    "summary": f"Regelkreise und Hormone ({model})",
                    "block_ids": group,
                    "word_budget": 700,
                    "objective_id": "o0",
                }
                for index, group in enumerate(groups)
                if group
            ]
        }

    def _outline(self, text: str, model: str) -> Any:
        ids = self._ids(text)
        groups = [ids[i::3] for i in range(3)]
        # The summary carries the model id so that switching models produces a
        # different artifact, the way a real model would. Without that the
        # cache-invalidation test would be exercising a no-op change.
        return {
            "beats": [
                {
                    "title": f"Beat {index + 1}",
                    "summary": f"Regelkreise und Hormone ({model})",
                    "block_ids": group,
                    "word_budget": 700,
                    "goal_id": "g0",
                }
                for index, group in enumerate(groups)
                if group
            ]
        }

    def _beat(self, text: str) -> Any:
        """Honour the beat's word budget, the way the prompt asks a real model to."""
        body = text.split("Passages you may draw facts from:\n", 1)[1]
        block_id = body.split("]")[0][1:]
        quote = body.split("\n", 1)[1][:80].strip()

        match = re.search(r"Word budget for this beat: about (\d+) words", text)
        budget = int(match.group(1)) if match else 250
        opener = f"Womit fangen wir bei {block_id} an?"
        remaining = max(budget - len(opener.split()) - len(quote.split()), 20)

        sentence = (
            "Das Hormonsystem und das Nervensystem greifen ineinander und ergaenzen "
            "sich gegenseitig im Koerper des Menschen. "
        )
        per_sentence = len(sentence.split())
        filler = sentence * max(1, round(remaining / per_sentence))

        return {
            "segments": [
                {"speaker": "Moderator", "text": opener, "kind": "pedagogy", "citations": []},
                {
                    "speaker": "Expertin",
                    "text": f"{quote} {filler}".strip(),
                    "kind": "claim",
                    "citations": [{"block_id": block_id, "quote": quote}],
                },
            ]
        }

    def _critic(self, text: str) -> Any:
        ids = re.findall(r"\[(beat\d+|[\w-]+-s\d+)\]", text)
        beat = next((item for item in ids if item.startswith("beat")), ids[0] if ids else "beat000")
        return {
            "notes": [
                {
                    "target_kind": "beat",
                    "target_id": beat,
                    "text": "Den Einstieg kürzer und unmittelbarer machen.",
                    "criterion": "Sprache",
                }
            ]
        }

    def _revise_script(self, text: str) -> Any:
        if "You write one beat" in text:
            return self._beat(text)
        block_id = "b0"
        quote = "Das Hormonsystem"
        match = re.search(r"\[(b[^\]]+)\]", text.split("Passages", 1)[-1])
        if match:
            block_id = match.group(1)
        body = text.split("Current segments to replace:\n", 1)
        current = body[1].split("\n\nPassages", 1)[0] if len(body) > 1 else ""
        speaker = "Expertin"
        if "Moderator" in current:
            speaker = "Moderator"
        return {
            "segments": [
                {
                    "speaker": speaker,
                    "text": "Kurz und klar, wie die Anmerkung verlangt. " + quote,
                    "kind": "claim",
                    "citations": [{"block_id": block_id, "quote": quote}],
                }
            ]
        }

    def _revise_outline(self, text: str) -> Any:
        beat_id = "beat000"
        match = re.search(r"Beat id \(keep this\): (\S+)", text)
        if match:
            beat_id = match.group(1)
        ids = re.findall(r"\b(b\d[\w-]*)\b", text)
        return {
            "title": f"Überarbeitet {beat_id}",
            "summary": "Nach der Anmerkung neu gefasst.",
            "block_ids": ids[:3] or ["b0"],
            "word_budget": 700,
        }
