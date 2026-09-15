# Kalliope

Turns long-form learning documents into podcast **scripts** with sentence-level
provenance back to the source page, runs them through automatic quality gates,
and lets a small team review and edit the result while capturing structured data
about every edit.

That edit data is the primary input for the evaluation system that comes later.
**Producing it is a first-class goal of this build, not a side effect.**

The whole system is one container with no external services: SQLite for
relational data, content-addressed files on disk for artifacts, an in-process
worker for flow execution, and FastAPI serving both the JSON API and the built
console. Outbound HTTPS to an LLM provider is the only network dependency.

---

## Run it

```sh
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into APP_SECRET_KEY
# add at least one provider key: ANTHROPIC_API_KEY, OPENAI_API_KEY or GOOGLE_API_KEY

docker build -t kalliope .
docker run -p 8000:8000 -v kalliope-data:/data --env-file .env kalliope
```

That is the complete instruction. One image, one volume at `/data`, port 8000.

There is no self-registration — create the first account against the same
volume:

```sh
docker run --rm -v kalliope-data:/data --env-file .env kalliope \
  python -m app.cli create-user --email you@example.com --role admin
```

Then open <http://localhost:8000>.

## Develop

```sh
make install          # backend venv + console dependencies
make user EMAIL=you@example.com ROLE=admin
make dev              # API on :8000
make dev-ui           # console on :5173, proxying /api to :8000
```

`make help` lists everything. `make doctor` reports whether the environment is
usable and says what is missing.

```sh
make check            # ruff, mypy, backend suite, console typecheck
make test             # backend suite (holdout corpus stays closed)
make test-holdout     # final acceptance pass, opens the holdout corpus
make corpus           # corpus coverage summary
```

---

## How it works

### Ingestion — one pipeline, no document knowledge

```
extract → layout/reading order → repetition analysis → normalize
        → block assembly → structure inference → zone classification
        → table extraction → ingestion report
```

**No component contains knowledge of a specific document, publisher, course or
layout.** There are no templates, no profiles, no per-publisher branches, and no
literal heading strings used to detect meaning. Everything is derived from the
document at runtime by statistical, geometric or model-based means.
Language-level resources — a stopword list, a readability formula, a speaking
rate — are permitted and live in `app/lang/`.

Two design decisions carry the rest:

**The `TextRun` primitive.** Normalization is never string manipulation over
concatenated page text. Every stage maps `list[TextRun] → list[TextRun]`, and
each run carries the x-boundary of every character it still contains. Edits go
through one function that keeps that geometry aligned, so an anchor that starts
mid-sentence resolves to a rectangle that starts mid-sentence — after
dehyphenation, ligature expansion and whitespace collapse. Round-trip is tested
as a universal invariant.

**The ingestion report.** Every parse measures itself: structure source and
confidence, anchor integrity, zone uncertainty, text density, reading-order
confidence, a character-conservation ledger. `ingestion_confidence` is derived
from those, and a `low` result always explains which measurement caused it. That
replaces "unrecognised template" with a number that means something on a first
encounter with any PDF.

### Flow framework

A node is a typed step: pydantic input model → pydantic output model. Flows are
YAML files listing nodes and configuration, editable in the console and
versioned there; adding a flow over existing nodes needs no Python change.
Artifacts are content-addressed JSON at
`/data/artifacts/<sha[:2]>/<sha>.json`, never mutated and never deleted.

```
node_key = sha256(name | version | canonical_json(config) | model_id |
                  sorted(input_artifact_hashes))
```

Because inputs are identified by content hash, an identical re-run is all cache
hits at zero cost, and changing one node's config invalidates that node plus
whatever its changed output reaches — and nothing else.

The run manifest records, per node: version, resolved config, cache key, hit or
miss, timings, model id, token counts and **cost in USD**. Pricing lives in
`app/llm/registry.py` alongside each model's capabilities, so a model that
rejects sampling parameters is adapted to rather than crashed on.

### Baseline flow v0

```
ingest → content_budget → select → outline → script → gates
```

Deliberately simple: no best-of-N, no revision loops, no critique passes. It is
the control condition every later flow is compared against.

`content_budget` is what makes a thin or image-dominant document fail honestly
instead of producing invented filler. It computes what the narratable word count
can actually support and refuses below three minutes, citing the numbers.

`script` writes one beat at a time. Factual assertions come only from the
provided blocks and carry anchors; analogies, transitions and framing are free
but introduce no new facts. The model returns a **quote** per citation rather
than character offsets — a model cannot count characters, so the node locates
the quote in the block and turns it into an anchor. Gate G1 verifies the result.

### Gates

| ID | Rule | On violation |
|---|---|---|
| G0 | ingestion confidence is not `low`; anchor integrity ≥ 0.99 | warn (fail below 0.95) |
| G1 | every anchor resolves to real characters in a real block | fail |
| G2 | every `claim` segment has at least one anchor | fail |
| G3 | no segment shares a 6-gram with non-narratable text | fail |
| G4 | total words within ±15% of `target_minutes × wpm(language)` | fail |
| G5 | every stated objective is served by a beat | warn, skipped when none |
| G6 | no segment reproduces removed page furniture | fail |
| G7 | language-appropriate readability inside a configurable band | warn, skipped without a formula |
| G8 | declared speakers, no empty segments, no duplicates, every beat covered | fail |

Deliberately **not** in this build: any model-based check of whether a cited
span actually supports its claim. That is the fact-checker, and it brings a
calibration problem that does not belong in a control condition.

Every gate describes itself — rule, method, thresholds, the node outputs it
inspects — and reports the **numbers it computed** even when it passes. A gate
that reports only `pass` asks to be trusted; one that shows its measurement can
be checked. `GET /api/gates` is that catalogue, and the console has a view built
on it.

**Gates inform; they do not block review.**

### The console

The review workspace is the eval-harvesting instrument, so its logging
requirements matter more than its aesthetics. Two panes: the script on the left,
the rendered source page on the right. Clicking a citation puts them in
register — the page scrolls to the anchored rectangle, a registration crosshair
parks on it, and a tie line is drawn across the gutter.

Per segment: accept in one click, edit inline, flag without editing, comment,
tag, and **undo**. **Saving an edit requires a reason code** — the eleven codes
are one shared definition consumed by both backend and frontend, and `OTHER`
additionally requires a note. Selection, outline and **block zone labels** are
all reviewable; a zone correction is stored as an `EditEvent` and is how the
system adapts to an unfamiliar document family without anyone writing template
code. Comments and tags are shown in the editor against the segment they are
about, so a reviewer coming back to a script sees what was already said about it.

**Undo deletes nothing.** Segment state is a fold over the event stream rather
than a stored flag, so an undo is itself an event that pops the last accept,
edit or flag — one step at a time, and the whole sequence still reaches the
evaluation export. An edit that was taken back does not reach the stored script
either.

Documents are filed in **folders**: an ordinary tree, drag a card onto a folder
to file it. Folders carry no permissions and no effect on parsing or runs, and
deleting one never deletes a document — the contents move up to the parent.

A run is also shown as its **flow graph**: every node with the values it
consumes and the value it publishes, its status, cost, model and timing, and the
gates that report on its output. A failed run marks the node that broke and
draws everything after it as never reached. Clicking a node shows the inputs it
actually received — each resolved to the upstream node and artifact hash that
produced it — next to what it produced, which is what turns "this output is
wrong" into "because this input was".

Completing a review marks the run `reviewed` and stores the edited script as a
**new** artifact. The original is never overwritten.

### Editing pipelines and formats

Pipelines are edited in the console: the ordered node list, every node's
parameters, and the **system prompt of each generation node**. Format
specifications — speakers, register, target length, opening and closing guidance
— are edited the same way.

Every save appends a **revision**; nothing is ever overwritten, and restoring an
old revision writes a new one carrying the old content. A run records the flow
revision it used, so a script from last month stays explainable. Archiving takes
a pipeline out of circulation without deleting it, because runs point at their
flow by id.

Each node **documents itself** — what it does in order, what it uses each input
for, and the ways it fails — from the node class, so the description in the
editor cannot drift from the code. Node parameters are declared with their type
and default, which is what lets the editor render a form instead of a JSON blob.
A parameter left at its default is not written into the saved definition, so
saving does not needlessly invalidate the artifact cache.

Wiring is by value name: a node consumes whatever an earlier node published under
the name it asks for. The editor shows the resulting connections on every node
and validates the draft on each change — a node placed before its producer, a
duplicate node, an unknown quality check, or a pipeline that never publishes a
script is refused before it can be saved.

The YAML files on disk stay the shipped defaults. A flow nobody has edited
tracks the file across deploys; once it has been edited in the console, the
database wins and a changed file is only logged.

```
GET  /api/nodes                                   # node catalogue, docs and parameters
GET  /api/pipelines                               # list, with revision and run count
POST /api/pipelines                               # create
POST /api/pipelines/validate                      # check a draft without saving
PUT  /api/pipelines/{id}                          # save a revision
GET  /api/pipelines/{id}/versions                 # history
POST /api/pipelines/{id}/versions/{n}/restore     # restore as a new revision
GET  /api/format-specs                            # same shape, for format specs
```

---

## Exports

```
GET /api/runs/{id}/export?format=md     # script as Markdown, citations as footnotes
GET /api/exports/edit-events.jsonl      # every edit event, one JSON object per line
```

Other endpoints worth knowing:

```
GET /api/gates                          # what every gate checks, and how
GET /api/runs/{id}/gates/detail         # that, joined with what it measured here
GET /api/flows/{id}/graph               # the flow's nodes, ports and wiring
GET /api/runs/{id}/graph                # the same, as it actually ran
GET /api/runs/{id}/nodes/{node}/io      # one node's real inputs and output
GET /api/folders                        # the folder tree, with rolled-up counts
```

The JSONL file is what the evaluation work consumes. Each line carries the
reviewer, the reason code, and the text before and after.

## Layout

```
backend/app/
├─ main.py config.py db.py security.py errors.py events.py migrations.py worker.py cli.py
├─ models/       SQLAlchemy ORM
├─ schemas/      pydantic domain + API models, zone taxonomy, reason codes
├─ api/          auth · folders · documents · runs · review
├─ llm/          provider protocol, pricing and capability registry, three providers
├─ lang/         detection, per-language resources, readability formulas
├─ ingestion/    runs · extract · layout · repetition · normalize · blocks ·
│                anchors · structure · zones · tables · report · pipeline
└─ pipeline/
   ├─ framework/ node · registry · artifacts · keys · runner
   ├─ nodes/     ingest · content_budget · select · outline · script
   ├─ gates/     G0–G8
   └─ flows/     baseline_v0.yaml
frontend/src/    views · components · api · stores · i18n
```

## Testing

The suite is parameterised over a corpus directory. **No test names a file** —
adding a PDF to `backend/tests/corpus/` extends coverage with zero test-code
changes, and a document that breaks an invariant turns the suite red without
anyone writing a new test. See `backend/tests/corpus/README.md`.

`tests/corpus/holdout/` is never opened during development. It runs only in the
final acceptance pass (`make test-holdout`), and it is the only mechanism the
suite has for detecting overfitting. A holdout failure is a design finding, not
a test to adjust: fix the generic pipeline, never special-case the document.

Ten universal invariants cover parsing, hyphenation, character conservation,
anchor round-trip, zone totality, normalization idempotence, determinism, block
and section integrity, report consistency and degradation. Five capability
thresholds are aggregate over the corpus, never per document.

## Not in this build

Text-to-speech and audio handling; the fact-check repair loop; AI persona
feedback; pairwise evaluation, judges or rubrics; vision-based extraction of
diagram content; multi-tenancy or customer-facing upload; OCR of scanned pages.
None of these are partially implemented.
# kaliope
