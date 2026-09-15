# Requirements — Podcast Pipeline, Build 1 (App + Baseline)

**Version:** 2.0 · **Date:** 2026-08-11 · **Status:** ready for implementation
**Audience:** Claude Code (implementer) and the development team.

This document is the implementation contract for the first build. It is written
to be implemented directly. Where it says MUST, the acceptance criteria in §13
test it.

> **Changed in 2.0** — v1.0 was built around a parser for one publisher's
> template, which made both the code and the test suite overfit to two therapy
> documents from one customer. This version removes all document-specific
> knowledge. There are no templates, no profiles and no per-publisher branches.
> Ingestion is a single generic pipeline that reports its own confidence, and the
> test suite is parameterised over a corpus directory so that adding a new PDF
> strengthens the suite without changing a line of test code.

---

## 1. Purpose and scope

Build an internal tool that turns long-form learning documents into podcast
**scripts** with sentence-level provenance back to the source document, runs them
through automatic quality gates, and lets a small team review and edit the result
while capturing structured data about every edit.

The captured edit data is the primary input for the evaluation system that will
be built later. **Producing that data is a first-class goal of this build, not a
side effect.**

### 1.1 In scope

1. Repository, tooling, single-container Docker build
2. Pipeline flow framework (typed nodes, content-addressed artifacts, caching, cost accounting)
3. Generic document ingestion with stable anchors and a self-assessed confidence report
4. Baseline flow v0 (ingest → content budget → select → outline → script)
5. Tier-1 quality gates
6. Vue review console with per-edit reason-code logging
7. User accounts

### 1.2 Explicit non-goals

Do not build, and do not leave partial implementations of: text-to-speech or any
audio handling; the fact-check repair loop; AI persona feedback; pairwise
evaluation, judges or rubrics; vision-based extraction of diagram content;
multi-tenancy or customer-facing upload; OCR of scanned pages.

### 1.3 The generality rule

**No component may contain knowledge of a specific document, publisher, course or
layout.** Concretely, the following are forbidden anywhere in the codebase:

- Publisher names, course names, cover strings or document titles
- Literal heading strings used to detect meaning (`"AUFGABE ZUM KAPITEL"`,
  `"Merke"`, `"Lernziele"`, `"Literatur"` …)
- Page geometry constants tied to a known document
- Conditional branches of the form "if this looks like document family X"
- Test assertions naming a specific fixture file

Language-level resources (a stopword list per language, a readability formula per
language) are permitted and are **not** document-specific. Everything else must
be derived from the document at runtime by statistical, geometric or model-based
means.

### 1.4 Language

Document language MUST be **detected**, not assumed. The generated script
language is a per-run setting defaulting to the detected document language.
German is the current target but nothing in the code may assume it. UI strings
live in `frontend/src/i18n/`, currently `de.ts`, and MUST NOT be inlined in
components. Code, comments, identifiers and logs are English.

---

## 2. Deployment constraint

The entire application MUST run as **one Docker container** with no external
services.

- SQLite for relational data
- Content-addressed files on disk for artifacts
- In-process background worker (thread pool) for flow execution
- FastAPI serves both the JSON API and the built Vue static assets
- One mounted volume at `/data` holds everything stateful

Outbound HTTPS to LLM providers is the only network dependency.

---

## 3. Repository layout

```
podcast/
├─ backend/
│  ├─ app/
│  │  ├─ main.py                  # FastAPI app factory, static mount, SSE
│  │  ├─ config.py                # pydantic-settings
│  │  ├─ db.py                    # SQLAlchemy engine/session, SQLite PRAGMAs
│  │  ├─ security.py              # argon2 hashing, session cookies
│  │  ├─ models/                  # SQLAlchemy ORM (§10)
│  │  ├─ schemas/                 # pydantic API request/response models
│  │  ├─ api/
│  │  │  └─ auth.py  documents.py  runs.py  review.py  flows.py
│  │  ├─ worker.py                # background run executor
│  │  ├─ llm/
│  │  │  ├─ base.py               # Provider protocol, Completion, Usage
│  │  │  ├─ anthropic.py  openai.py  google.py
│  │  │  └─ registry.py           # model id → provider, pricing table
│  │  ├─ lang/
│  │  │  ├─ detect.py             # language detection
│  │  │  ├─ resources.py          # per-language stopwords, conjunctions
│  │  │  └─ readability.py        # per-language formulas
│  │  ├─ ingestion/
│  │  │  ├─ runs.py               # TextRun primitive (§5.2)
│  │  │  ├─ extract.py            # PyMuPDF → TextRun list
│  │  │  ├─ layout.py             # column & reading-order inference
│  │  │  ├─ repetition.py         # statistical boilerplate detection
│  │  │  ├─ normalize.py          # §5.4 transformations
│  │  │  ├─ structure.py          # §5.5 section inference
│  │  │  ├─ zones.py              # §5.6 LLM zone classification
│  │  │  ├─ tables.py             # §5.7
│  │  │  ├─ blocks.py             # block assembly + char_map
│  │  │  └─ report.py             # §5.8 ingestion confidence report
│  │  ├─ pipeline/
│  │  │  ├─ framework/
│  │  │  │  └─ node.py  registry.py  artifacts.py  runner.py  keys.py
│  │  │  ├─ nodes/
│  │  │  │  └─ ingest.py  content_budget.py  select.py  outline.py  script.py
│  │  │  ├─ gates/
│  │  │  │  └─ base.py  g0_ingestion.py … g8_structure.py  runner.py
│  │  │  └─ flows/
│  │  │     └─ baseline_v0.yaml
│  │  └─ cli.py                   # typer: create-user, run-flow, reparse
│  ├─ tests/
│  │  ├─ corpus/                  # §13.1 — PDFs, discovered at test time
│  │  │  └─ holdout/              # never opened during development
│  │  └─ …
│  └─ pyproject.toml
├─ frontend/                      # Vue 3 + TypeScript + Vite + Pinia
│  └─ src/{views,components,api,stores,i18n}
├─ docker/entrypoint.sh
├─ Dockerfile
├─ .env.example
├─ Makefile
└─ README.md
```

**Backend:** Python 3.12, `uv`, FastAPI, SQLAlchemy 2.x, pydantic v2,
pydantic-settings, PyMuPDF, typer, pytest, ruff, mypy (strict on
`app/ingestion` and `app/pipeline`).

**Frontend:** Vue 3 `<script setup>`, TypeScript strict, Vite, Pinia, Vue Router,
vitest. No component framework mandated; keep dependencies minimal.

---

## 4. Core domain concepts

| Concept | Definition |
|---|---|
| **Document** | An uploaded PDF plus its parsed structure. Immutable once parsed; re-parsing creates a new `parse_version`. |
| **TextRun** | A provenance-carrying span of extracted text (§5.2). The unit all normalization operates on. |
| **Block** | The atomic unit of source text: a paragraph, list item, heading, table, caption or box. Stable ID, page/bbox provenance, one zone label. |
| **Anchor** | `{document_id, parse_version, block_id, char_start, char_end}`. MUST always resolve to exact source characters and to page rectangles. |
| **Zone** | A generic semantic label from the fixed taxonomy in §5.6. |
| **Node** | A typed pipeline step: pydantic input model → pydantic output model. |
| **Flow** | A named, versioned YAML file listing nodes and configuration. |
| **Run** | One execution of one flow over one document with one config. |
| **Artifact** | A content-addressed JSON blob produced by a node. |
| **Segment** | One unit of generated script: speaker turn, text, anchors, claim/pedagogy label. |
| **Gate** | A pass/fail/warn check producing a report; never mutates anything. |
| **EditEvent** | A recorded reviewer action with a reason code. |

---

## 5. Ingestion

One pipeline. No branches on document identity. Every stage either succeeds,
degrades explicitly, or records reduced confidence — it never assumes.

```
extract → layout/reading order → repetition analysis → normalize
        → block assembly → structure inference → zone classification
        → table extraction → ingestion report
```

### 5.1 Degradation contract

Every structural feature is **optional** in the output model, and every consumer
MUST define its behaviour when it is absent. There is no failure mode in which
missing structure produces silently wrong output; it produces lower confidence
and an explicit warning. §5.8 makes that visible, and gate G0 makes it
actionable.

### 5.2 The TextRun primitive — mandatory design

Normalization MUST NOT be implemented as string operations over concatenated
page text. Doing so destroys the mapping back to the PDF and makes every anchor
unverifiable.

```python
class TextRun(BaseModel):
    page: int                        # 0-based
    bbox: tuple[float, float, float, float]
    text: str
    font_size: float
    font_name: str
    bold: bool
    in_filled_rect: bool             # inside a drawn box / shaded area
    raw_index: int                   # position in original extraction order
    sources: list[int] = []          # raw_index values merged into this run
```

Every stage transforms `list[TextRun] → list[TextRun]`, preserving page and bbox
provenance for every surviving character. `Block.text` is assembled from runs and
stores `char_map: list[RunSpan]`, so any `(char_start, char_end)` resolves back
to one or more `(page, rect)` pairs for UI highlighting. **Round-trip is tested as
a universal invariant (§13.2, INV-4).**

### 5.3 Layout and reading order

Column count is inferred per page by clustering run x-positions; reading order is
then per-column top-to-bottom, left-to-right. Multi-column, single-column and
free-form slide layouts are all handled by the same routine. Reading order
confidence is recorded — measured on real material, presentation exports
frequently emit headings after the content they introduce, and the layout stage
is what must repair that.

### 5.4 Normalization — all steps statistical or structural

1. **Repetition analysis → boilerplate removal.** Cluster runs by
   `(normalized_text, quantised_bbox)`. Any cluster occurring on ≥30% of pages
   (minimum 3 pages) is boilerplate. Separately, runs in the top or bottom 10%
   page band whose text is an integer that correlates with page index are page
   numbers. Removed runs are retained in `Document.boilerplate` for gate G6.
   No literal strings; entirely derived from the document.
2. **Letterspacing collapse.** A run where ≥60% of whitespace-separated tokens
   are single characters and token count ≥4 is letterspaced; collapse it.
   Language- and publisher-agnostic.
3. **Dehyphenation.** Two schemes, both always handled: remove every U+00AD SOFT
   HYPHEN; join `<letter>-\n<letter>` sequences. **Suspended-compound exception:**
   do not join when the following token is a coordinating conjunction in the
   detected language (from `lang/resources.py`) — otherwise German constructions
   like `Hormon-\nund Nervensystem` are corrupted into a nonexistent word. Both
   schemes occur in real material at comparable frequency; handling only one
   silently corrupts tokens and therefore every anchor built on them.
4. **Unicode normalization.** NFC, ligature expansion, quote and dash
   normalization, whitespace collapse.

Normalization MUST be idempotent (INV-6).

### 5.5 Structure inference

Attempted in order, recording `structure_source` and `structure_confidence`:

1. **Embedded PDF outline** — used when present and when its targets are
   internally consistent. `confidence=high`.
2. **Typographic clustering** — cluster runs by (font size, weight); the modal
   cluster is body text. Runs materially larger or bold, short, and followed by
   body text are heading candidates; cluster sizes give heading levels.
   `confidence=medium`.
3. **Flat fallback** — a single implicit section, or page-range chunks for long
   documents. `confidence=low`.

Every block is assigned to at most one section. Nothing downstream may require a
section hierarchy to exist.

### 5.6 Zone classification — model-based, generic taxonomy

This is the component that previously encoded publisher knowledge. It is now a
model call over a fixed, publisher-neutral, language-neutral taxonomy.

| Zone | Meaning | Narratable | Salience |
|---|---|---|---|
| `body` | Running prose, the substance of the document | yes | 1.0 |
| `heading` | Section or subsection title | context only | — |
| `emphasis_callout` | Highlighted key-point box the author marked as important | yes | 2.0 |
| `objective` | Stated learning objectives or goals | yes, as goals | — |
| `exercise` | Tasks, questions or assignments addressed to the learner | **no** | — |
| `answer_space` | Blank forms or tables for the learner to fill in | **no** | — |
| `reference` | Bibliography, citations, further reading | **no** | — |
| `navigation` | Table of contents, index, page furniture | **no** | — |
| `caption` | Figure or table caption | yes | 1.0 |
| `data_table` | Content-bearing table | yes | 1.0 |
| `metadata` | Cover, imprint, legal notices | **no** | — |
| `other` | Unclassifiable | **no** | — |

**Input to the classifier**, per block: index, truncated text, font size relative
to the page's body median, bold flag, `in_filled_rect`, bbox, page position, and
the enclosing section title if known. Batched at ~8 pages per call using a cheap
model (`ZONE_MODEL`, default a small fast model, temperature 0).

**Output**: `{block_index, zone, confidence}` for every block. Classification MUST
be total — an unlabelled block is a bug, not a state.

**Uncertainty handling.** Blocks below a confidence threshold are labelled `body`
(the conservative choice for coverage — losing real content is worse than a
reviewer deleting a stray line) and flagged `zone_uncertain=True`. Uncertain
blocks are surfaced in the ingestion report and highlighted in the console.

**Zone correction is a first-class reviewer action.** The console lets a reviewer
relabel a block; the correction is stored as an `EditEvent` with
`target_type="block_zone"`. These corrections are training and evaluation data
for improving the classifier later, and are the mechanism by which the system
adapts to a new document family without anyone writing a template.

**Caching.** The classifier's input payload is content-hashed; identical input
returns the cached labelling. Parsing the same PDF twice therefore costs one
classification and is deterministic (INV-7).

### 5.7 Tables

Extracted via `page.find_tables()` and stored as
`TableBlock{rows, header, n_cols}` — never flattened into a text stream, because
a flattened table loses column association and any anchor into it mis-attributes
cells. The LLM-facing rendering of a table is a Markdown table.

### 5.8 Ingestion report — the generic replacement for template detection

Every parse produces an `IngestionReport`, stored with the document and shown in
the console:

```python
class IngestionReport(BaseModel):
    language: str                     # detected, with confidence
    page_count: int
    extractable_words: int
    narratable_words: int             # words in narratable zones
    visual_content_ratio: float       # image area / page area, mean
    text_density: float               # words per page
    structure_source: Literal["outline", "typographic", "flat"]
    structure_confidence: Literal["high", "medium", "low"]
    section_count: int
    zone_distribution: dict[str, int]
    zone_uncertain_ratio: float
    table_count: int
    boilerplate_lines_removed: int
    anchor_integrity: float           # sampled round-trip success rate
    reading_order_confidence: float
    warnings: list[str]
    ingestion_confidence: Literal["high", "medium", "low"]
```

`ingestion_confidence` is derived from structure confidence, zone uncertainty,
anchor integrity and text density. A document that is mostly diagrams, or has no
recoverable structure, or whose anchors do not round-trip, reports `low` and
explains why — for **any** document, including families nobody anticipated. This
replaces "unrecognised template" with a measurement that is meaningful on a first
encounter with any PDF.

### 5.9 Parsed document artifact

```python
class ParsedDocument(BaseModel):
    document_id: str
    parse_version: int
    language: str
    title: str | None
    page_count: int
    sections: list[Section] | None    # None when structure is flat
    blocks: list[Block]               # text, zone, salience, page, char_map
    objectives: list[str]             # from objective zones; may be empty
    non_narratable_text: list[str]    # for gate G3
    boilerplate: list[str]            # for gate G6
    report: IngestionReport
```

Note there is no field naming a template, publisher or document family.

---

## 6. Baseline flow v0

Deliberately simple: no best-of-N, no revision loops, no critique passes. It is
the control condition every later flow is compared against.

```
ingest → content_budget → select → outline → script → gates
```

### 6.1 `content_budget`

Input: `ParsedDocument`, requested target minutes.
Output: `ContentBudget{narratable_words, max_supportable_minutes, target_minutes,
compression_ratio, verdict, explanation}`

- Speaking rate is a per-language constant in `lang/resources.py` (German: 135
  wpm). Never hardcoded in the node.
- `max_supportable_minutes = narratable_words / wpm / MIN_COMPRESSION`, default
  `MIN_COMPRESSION = 2.5`. Rationale: a grounded episode must *select* from
  meaningfully more material than it emits.
- `target_minutes > max_supportable_minutes` → clamp, `verdict="clamped"`.
- `max_supportable_minutes < 3` → fail the run, `verdict="insufficient"`, with an
  explanation citing narratable word count and `visual_content_ratio`.

This is what makes an image-dominant document fail honestly instead of producing
invented filler, and it works from measured properties, not document identity.

### 6.2 `select`

Input: `ParsedDocument`, `ContentBudget`, `AudienceSpec`.
Output: `Selection{learning_goals, selected_blocks, rationale}`

- If `objectives` is non-empty, learning goals MUST be derived from them and
  recorded as `source="document"`. Otherwise they are generated and recorded as
  `source="generated"`. Both paths are first-class; neither is a fallback hack.
- Block salience from §5.6 is passed to the prompt as a weight. No zone is named
  in prose in the prompt; the model receives generic labels and weights.
- The `Selection` artifact is reviewable in its own right (§9.3).

### 6.3 `outline`

Input: `Selection`, `FormatSpec`, `ContentBudget`.
Output: `Outline{beats}` — each beat has a title, covered block refs, an allocated
word budget and the learning goal it serves. `sum(beat.word_budget)` MUST be
within ±10% of the target word count.

### 6.4 `script`

Input: `Outline`, `FormatSpec`, and the text of referenced blocks only.
Output: `Script{segments}`

```python
class Segment(BaseModel):
    id: str
    speaker: str
    text: str
    kind: Literal["claim", "pedagogy"]
    anchors: list[Anchor]              # non-empty when kind == "claim"
    beat_id: str
```

The prompt states the groundedness policy: factual assertions come only from the
provided blocks and carry anchors; analogies, transitions, questions and framing
are free but introduce no new facts. The model receives block IDs alongside block
text and is instructed to cite them. The node does not verify anchors — gate G1
does.

### 6.5 FormatSpec

Ships with exactly one instance (two-host dialogue, host asks / expert explains,
formal register, 15 minutes default) but MUST be data, not prompt text:

```python
class FormatSpec(BaseModel):
    id: str
    name: str
    speakers: list[SpeakerSpec]
    register: str
    target_minutes: int
    opening: str | None
    closing: str | None
    beats_hint: str | None
```

### 6.6 AudienceSpec

One free-text `description` plus optional structured fields `prior_knowledge`,
`role`, `listening_context`, `desired_outcome`.

---

## 7. Flow framework

### 7.1 Node contract

```python
class Node(Protocol):
    name: str
    version: str            # bump on any behaviour change
    Input: type[BaseModel]
    Output: type[BaseModel]
    def run(self, inp: BaseModel, ctx: NodeContext) -> BaseModel: ...
```

`NodeContext` provides the LLM client (recording usage against the run), the
artifact store, the run logger and the node's resolved config.

### 7.2 Cache key

```
node_key = sha256(name | version | canonical_json(config) | model_id |
                  sorted(input_artifact_hashes))
```

Existing artifact for a key → node skipped and artifact reused, unless
`force=True`. Cache hits are recorded in the manifest.

### 7.3 Artifact store

Content-addressed at `/data/artifacts/<sha256[:2]>/<sha256>.json`. Never mutated,
never deleted by the app. The DB stores metadata and the
`(run_id, node_name) → hash` mapping.

### 7.4 Run manifest

Per node: name, version, resolved config, cache key, hit/miss, timestamps, wall
time, model id, prompt and completion tokens, **cost in USD**. Run total is shown
in the console. Pricing lives in `llm/registry.py`.

### 7.5 Flow definition

```yaml
id: baseline_v0
version: "1.0"
description: Control condition. No quality mechanisms.
nodes:
  - node: ingest
  - node: content_budget
    config: {min_compression: 2.5}
  - node: select
    config: {model: claude-opus-4-6}
  - node: outline
    config: {model: claude-opus-4-6}
  - node: script
    config: {model: claude-opus-4-6, temperature: 0.7}
gates: [G0, G1, G2, G3, G4, G5, G6, G7, G8]
```

Adding a flow MUST require only a new YAML file when its nodes already exist.

### 7.6 Execution

Bounded `ThreadPoolExecutor` (default 2 concurrent runs) started in the FastAPI
lifespan. States: `queued → running → (completed | failed) → in_review →
reviewed`. Progress is pushed over SSE at `GET /api/runs/{id}/events`. A node
failure fails the run, preserves completed artifacts and stores the traceback.
Re-runs resume via cache.

---

## 8. Tier-1 gates

Gates run after the script node, never mutate anything, and produce a
`GateReport` artifact of
`{id, status: pass|warn|fail, violations: [{target_id?, message, detail}]}`.

| ID | Name | Rule | On violation |
|---|---|---|---|
| **G0** | `ingestion_confidence` | `IngestionReport.ingestion_confidence` is not `low`; `anchor_integrity ≥ 0.99`. | warn (fail if anchor integrity < 0.95) |
| **G1** | `anchors_resolve` | Every anchor's `block_id` exists, `char_start < char_end`, range inside the block. | fail |
| **G2** | `claims_cited` | Every `kind == "claim"` segment has ≥1 anchor. | fail |
| **G3** | `no_apparatus_leak` | No segment shares a 6-gram with `non_narratable_text`. | fail |
| **G4** | `length_band` | Total words within ±15% of `target_minutes × wpm(language)`. | fail |
| **G5** | `objective_coverage` | If `objectives` is non-empty, every objective is served by ≥1 beat. Skipped when empty. | warn |
| **G6** | `no_boilerplate` | No segment contains a string from `Document.boilerplate`. | fail |
| **G7** | `readability` | Language-appropriate formula from `lang/readability.py` within a configurable band. Skipped when no formula exists for the detected language. | warn |
| **G8** | `structure` | Speakers declared in the FormatSpec; no empty segments; no verbatim duplicates; every beat covered. | fail |

G1–G6 and G8 are deterministic and require no model call. **G7 is a heuristic
designed for written text** — directional only, never a target to optimise.

Deliberately **not** in this build: any model-based check of whether a cited span
actually supports its claim. That is the fact-checker, and it brings a
calibration problem that does not belong in a control condition.

Gates inform; they do not block review.

---

## 9. Review console

The console is the eval-harvesting instrument. Its logging requirements matter
more than its aesthetics.

### 9.1 Views

Login · Documents (list, upload, parse status, **ingestion report**) · Document
detail (section tree, zone overlays on page previews, uncertain blocks
highlighted) · New run · Run detail (live progress, cost, artifacts, gate report)
· Review workspace · Runs list.

### 9.2 Review workspace

Two panes. Left: script segments with speaker, text, claim/pedagogy badge,
citation markers, gate violations. Right: the rendered source page with the
anchored rectangle highlighted. Clicking a citation MUST scroll to and highlight
the exact rectangle — this is what §5.2's `char_map` exists for.

Per segment: **Accept** (one click), **Edit** (inline; saving REQUIRES a reason
code), **Flag without editing**, **Comment**.

### 9.3 What is reviewable

`Selection`, `Outline`, `Script` segments, and **block zone labels**. Structural
mistakes are far cheaper to catch in the selection than in finished prose, and
zone corrections are how the system learns an unfamiliar document family without
anyone writing template code.

### 9.4 Reason codes

Fixed enum, German labels, single shared definition used by backend and frontend:

| Code | Label (de) |
|---|---|
| `FACT_WRONG` | Fachlich falsch |
| `NOT_IN_DOC` | Nicht im Dokument belegt |
| `UNINTUITIVE_EXAMPLE` | Beispiel unpassend oder unverständlich |
| `WRONG_LEVEL` | Falsches Niveau für die Zielgruppe |
| `CLUMSY_LANGUAGE` | Sprachlich unsauber |
| `PACING` | Tempo / Länge unpassend |
| `MISSING_CONTENT` | Wichtiger Inhalt fehlt |
| `STRUCTURE` | Aufbau / Reihenfolge |
| `CITATION_WRONG` | Beleg passt nicht zur Aussage |
| `ZONE_WRONG` | Textbereich falsch klassifiziert |
| `OTHER` | Sonstiges (Freitext erforderlich) |

`OTHER` requires a non-empty note. The enum MUST be trivially extensible — expect
it to grow once real reviews start; no `switch` statements over its values.

### 9.5 Completion and export

Completing a review marks the run `reviewed`, stores the edited script as a new
artifact (the original is never overwritten), and records reviewer, duration and
per-reason-code counts.

`GET /api/runs/{id}/export?format=md` — script as Markdown with citations as
footnotes.
`GET /api/exports/edit-events.jsonl` — every edit event across all runs, one JSON
object per line. This is the file the evaluation work will consume.

---

## 10. Data model (SQLite)

```
users(id, email, name, password_hash, role[admin|reviewer], created_at, active)
sessions(id, user_id, expires_at)
documents(id, filename, sha256, uploaded_by, uploaded_at, page_count, language,
          parse_version, parse_status, parse_error, report_json)
flows(id, version, path, description)              -- synced from YAML on boot
runs(id, document_id, flow_id, flow_version, config_json, format_spec_json,
     audience_spec_json, status, created_by, created_at, started_at,
     finished_at, error, total_cost_usd, manifest_json)
run_nodes(id, run_id, node_name, node_version, cache_key, cache_hit,
          artifact_hash, started_at, finished_at, cost_usd, tokens_in,
          tokens_out, model_id, error)
artifacts(hash, kind, size_bytes, created_at)
llm_calls(id, run_id, node_name, model_id, tokens_in, tokens_out, cost_usd,
          latency_ms, created_at)
gate_results(id, run_id, gate_id, status, violations_json)
segments(id, run_id, ordinal, speaker, text, kind, anchors_json, beat_id)
edit_events(id, run_id, target_type[segment|selection|outline|block_zone],
            target_id, user_id, action[accept|edit|flag|comment|relabel],
            reason_code, note, text_before, text_after, created_at)
review_sessions(id, run_id, user_id, started_at, finished_at, summary_json)
```

`PRAGMA journal_mode=WAL`, `foreign_keys=ON`. Alembic migrations.

---

## 11. API

All under `/api`, JSON, session-cookie authenticated except login.

```
POST   /api/auth/login  ·  POST /api/auth/logout  ·  GET /api/auth/me

GET    /api/documents
POST   /api/documents                        multipart PDF upload, parses async
GET    /api/documents/{id}                   includes IngestionReport
GET    /api/documents/{id}/structure         sections + blocks + zones
GET    /api/documents/{id}/pages/{n}/image   PNG render, cached on disk
POST   /api/documents/{id}/reparse
PATCH  /api/documents/{id}/blocks/{bid}/zone  reviewer relabel → EditEvent

GET    /api/flows  ·  GET /api/formats

POST   /api/runs        {document_id, flow_id, format_id, target_minutes,
                         audience_spec, language?, force?}
GET    /api/runs  ·  GET /api/runs/{id}
GET    /api/runs/{id}/events                 SSE progress
GET    /api/runs/{id}/artifacts/{node_name}
GET    /api/runs/{id}/script  ·  /gates  ·  /export

POST   /api/runs/{id}/review/start
POST   /api/runs/{id}/review/events
POST   /api/runs/{id}/review/complete
GET    /api/exports/edit-events.jsonl
```

Errors use RFC 9457 problem+json. Uploads limited to 50 MB, validated as PDF by
magic bytes.

---

## 12. Configuration, security, Docker

### 12.1 Environment

```
APP_SECRET_KEY=            # required
DATA_DIR=/data
ANTHROPIC_API_KEY=  OPENAI_API_KEY=  GOOGLE_API_KEY=
DEFAULT_MODEL=claude-opus-4-6
ZONE_MODEL=                # cheap fast model for zone classification
MAX_CONCURRENT_RUNS=2
LOG_LEVEL=INFO
```

Missing required keys MUST fail fast at startup with a clear message.
`.env.example` lists every variable.

### 12.2 Users

No self-registration. `python -m app.cli create-user --email … --role …`, argon2id
hashing, httpOnly SameSite=Lax session cookies, 30-day expiry. Reviewer identity
is attached to every edit event — the reason real accounts exist.

### 12.3 Dockerfile

Multi-stage: `node:22-alpine` builds the Vue app; `python:3.12-slim` installs the
backend via `uv` and copies `frontend/dist`. Runtime: non-root user,
`EXPOSE 8000`, `VOLUME /data`, entrypoint runs Alembic then uvicorn,
`HEALTHCHECK GET /api/health`.

`docker run -p 8000:8000 -v podcast-data:/data --env-file .env <image>` MUST be
the complete instruction to run the system.

---

## 13. Testing and acceptance

The v1.0 suite asserted document-specific numbers, which meant a green suite
proved only that two therapy PDFs still parsed. This section replaces that.

### 13.1 Corpus-parameterised tests

- Test PDFs live in `backend/tests/corpus/`. Tests **discover** them at runtime.
- **No test may reference a filename.** Adding a PDF to the directory
  automatically extends coverage with zero test-code changes; if a new document
  breaks an invariant, the suite goes red without anyone writing a new test.
- An optional sibling `<name>.meta.yaml` may declare only coarse facts —
  `language`, `family` (a free-text label used for diversity reporting only),
  `has_outline: true|false|unknown`. It MUST NOT contain expected counts.
- **Holdout:** `tests/corpus/holdout/` holds documents that MUST NOT be opened,
  inspected or debugged against during implementation. They run only in the final
  acceptance pass (§13.5). This is the mechanism that detects overfitting; the
  suite has no other way to catch it.
- CI prints a corpus coverage summary: document count, distinct families, page
  and layout distribution.

### 13.2 Universal invariants — must hold for every document, including unseen ones

| ID | Invariant |
|---|---|
| **INV-1** | Parsing completes without exception and produces ≥1 block. |
| **INV-2** | No block text contains U+00AD, and no `<letter>-\n<letter>` sequence. |
| **INV-3** | Character conservation: extracted characters equal block characters plus recorded removals (boilerplate, hyphens, whitespace) within 2%; nothing vanishes unexplained. |
| **INV-4** | Anchor round-trip: for 200 sampled `(block, start, end)` triples, resolving to `(page, rect)` and re-extracting returns the same string modulo whitespace, ≥99.5% of the time. |
| **INV-5** | Zone labelling is total; every label is in the §5.6 taxonomy; every block has a salience value. |
| **INV-6** | Normalization is idempotent: `normalize(normalize(x)) == normalize(x)`. |
| **INV-7** | Determinism: two parses of the same file produce identical `ParsedDocument` hashes (zone classification cached, temperature 0). |
| **INV-8** | Blocks are strictly ordered, non-overlapping, non-empty; every section's block IDs exist; every block belongs to ≤1 section. |
| **INV-9** | `IngestionReport` is fully populated, and `ingestion_confidence` is consistent with its inputs. |
| **INV-10** | No unhandled degradation: a document with no outline, no tables, no objectives and no detectable boilerplate still parses and yields a usable `ParsedDocument`. |

### 13.3 Capability thresholds — aggregate over the corpus, never per document

| ID | Threshold |
|---|---|
| **CAP-1** | ≥90% of corpus documents achieve `structure_confidence` ≥ medium. |
| **CAP-2** | For any document containing a line repeated on ≥50% of pages, ≥90% of such lines are removed as boilerplate. |
| **CAP-3** | Zone classifier macro-F1 ≥ 0.80 against a hand-labelled set of ≥100 blocks spread across ≥4 documents from ≥3 families. This is the only manual labelling in the suite and it MUST be spread across families, not concentrated in one. |
| **CAP-4** | ≥80% of tables detected by visual inspection are preserved as `TableBlock` with correct column count. |
| **CAP-5** | Mean `anchor_integrity` across the corpus ≥ 0.99, with no document below 0.95. |

### 13.4 Behavioural acceptance criteria

**Framework** — **AC-FW-1** identical re-run is all cache hits, zero LLM calls,
zero cost · **AC-FW-2** changing one node's config invalidates that node and its
descendants only · **AC-FW-3** manifest cost equals the sum of `llm_calls` ·
**AC-FW-4** a new flow YAML over existing nodes runs with no Python change ·
**AC-FW-5** a mid-flow exception leaves earlier artifacts intact and the run
`failed` with a stored traceback.

**Baseline** — **AC-BL-1** for any corpus document with sufficient narratable
content, a 15-minute run produces a script within the G4 band in the document's
detected language · **AC-BL-2** a document whose narratable content cannot
support 3 minutes fails at `content_budget` with `verdict="insufficient"` and an
explanation · **AC-BL-3** learning goals are marked `source="document"` when the
document declares objectives and `source="generated"` otherwise, with both paths
exercised by the corpus.

**Gates** — **AC-GATE-1** all nine gates execute and produce a `GateReport` ·
**AC-GATE-2** synthetic scripts trigger each deterministic gate: dangling
`block_id` → G1, uncited claim → G2, quoted exercise text → G3, out-of-band
length → G4, boilerplate string → G6, undeclared speaker → G8 · **AC-GATE-3**
gate failures do not prevent review · **AC-GATE-4** G5 and G7 skip cleanly when
objectives or a language formula are absent.

**Console** — **AC-UI-1** a reviewer opens a completed run and sees every segment
with citations and gate violations · **AC-UI-2** clicking a citation highlights
the correct rectangle on the correct page · **AC-UI-3** saving an edit without a
reason code is impossible · **AC-UI-4** the JSONL export returns one line per
event with reviewer, reason code, before and after text · **AC-UI-5** selection,
outline and **block zone labels** are all reviewable, and a zone relabel appears
in the export.

**Deployment** — **AC-DEP-1** `docker build` succeeds and one `docker run` with
one volume starts the system · **AC-DEP-2** empty `/data` creates the schema;
existing `/data` preserves all runs and artifacts · **AC-DEP-3** a missing
required key fails at boot with a clear message.

### 13.5 Final acceptance

The build is done when INV-*, CAP-*, and AC-* all pass **including on the holdout
documents, run for the first time in that pass**. A holdout failure is a design
finding, not a test to be adjusted: fix the generic pipeline, never special-case
the document.

---

## 14. Implementation order

1. Repo, config, DB, auth, CLI user creation, Dockerfile — a container that boots
2. Extraction → layout → repetition analysis → normalization → block assembly
   with `char_map` (**INV-1…4, INV-6**). Hardest and most load-bearing part;
   complete it before anything downstream.
3. Structure inference with all three tiers and confidence reporting (**INV-8**)
4. LLM abstraction with cost accounting
5. Zone classification and the ingestion report (**INV-5, INV-9, CAP-1…3**)
6. Flow framework: node protocol, artifact store, cache keys, runner, manifest
   (**AC-FW-1…5**), verified with a no-op flow
7. Baseline nodes: content_budget → select → outline → script (**AC-BL-1…3**)
8. Gates (**AC-GATE-1…4**)
9. API and SSE
10. Vue console: documents → run detail → review workspace with zone overlays
    (**AC-UI-1…5**)
11. Export endpoints, README, `.env.example`
12. Final acceptance pass against the holdout set (§13.5)

---

## 15. Notes for the implementer

- **The generality rule (§1.3) is the point of this revision.** If you find
  yourself writing a document-specific string, a publisher name, or an
  "if it looks like X" branch, the design is wrong — make the pipeline measure
  the property instead, and surface the measurement in the ingestion report.
- The anchor mechanism is the spine of the product. If §5.2 is implemented as
  string manipulation, everything above it becomes unverifiable and the eventual
  fact-checker cannot be built. Do not compromise here.
- Prefer explicit degradation over clever inference. A document that reports
  `ingestion_confidence: low` with clear reasons is a good outcome; one that
  silently produces plausible-looking wrong structure is the failure this
  architecture exists to prevent.
- The reason-code enum and the zone taxonomy will both change once real reviews
  start. Make them cheap to extend: one shared definition each, no switch
  statements over their values.
- Prefer boring, explicit code over abstraction. This codebase will be read and
  modified by people evaluating pipeline variants, not by framework authors.
