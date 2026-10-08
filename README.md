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
# optional, for audio: ELEVENLABS_API_KEY

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

Then open <http://localhost:8000>. Further accounts are added in the console
under **Einstellungen → Organisation**: every member is an equal admin of one
shared workspace, and no mail is sent, so pass the credentials on yourself.
The edit-event export lives in the same place, under **Export**.

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

Zone classification uses the fallback model declared by `DEFAULT_SMALL_MODEL` in
[the model registry](backend/app/llm/registry.py). Set `ZONE_MODEL` to override it;
the ingest node's `zone_model` setting takes precedence over that environment
variable. For sampling adaptation and classification caching, see
[the classifier contract](docs/REQUIREMENTS.md#56-zone-classification--model-based-generic-taxonomy).

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
instead of producing invented filler. A plain read-through of the narratable
words is stretched so the episode has room for dialogue, and the run refuses
below three minutes, citing the numbers.

`script` writes one beat at a time. Each call sees the running order and the
text of every earlier beat, so the episode stays one conversation; facts still
come only from that beat's blocks and carry anchors. Analogies, transitions and
framing are free but introduce no new facts. The model returns a **quote** per
citation rather than character offsets — a model cannot count characters, so the
node locates the quote in the block and turns it into an anchor. Gate G1
verifies the result.

### Audio pipelines

An **audio pipeline** runs on a finished script and is chosen separately from
the pipeline that writes it, so another speech provider later is another audio
pipeline. A flow is one when it contains an audio node (`purpose: audio` in
`/api/flows`); it cannot start a run or a series. Each execution is an **audio
take** of a run (`audio_takes`), so a run can have several.

```
elevenlabs_dialog_v0:  audio_script → audio_approval (waits) → audio_render → audio_join
```

`audio_script` prepares the script for ElevenLabs Text to Dialogue (Eleven v4),
one call per beat: audio tags in square brackets (`[curious]`, `[short pause]`)
and numbers or abbreviations written as spoken, each declared as a spoken form
(`1,5 %` → `eins Komma fünf Prozent`). A deterministic guard
(`pipeline/audio_tags.py`) removes the tags, applies the declared forms to the
script's line and compares word by word; case and punctuation do not count. A
refused line is asked for once more and otherwise spoken untagged, marked
`fallback`, so a tag never fails a run. Beats are cached one by one.

`audio_approval` pauses the take with the price: lines, characters, requests and
dollars at the model's rate. Nothing is spent until someone approves in the run
view, where the voices per speaker are chosen (saved per format, never inside
the format spec, so a voice change leaves the script cache alone).
`audio_render` then speaks the approved lines in chunks of whole lines within one
beat (at most 1,800 characters, under ElevenLabs' 2,000), one dialogue input per
line, and stores each chunk's MP3 in the artifact store's `media` folder. Chunks
are cached by model, voices, settings and text, so a failed or repeated take
never pays twice; on Eleven v4 each chunk continues from the previous ones by
request id. Chunks an earlier take already spoke with the same voices are
reused and left out of the price.

**Experimentieren → ElevenLabs ausprobieren** imports a retained prepared
`audio_script` from a run's audio take, including its tags, spoken forms and
speaker structure. Choose the run and prepared take with the existing source
chooser. A raw script is not accepted or regenerated: if there is no prepared
artifact, prepare an audio take in the run view first. The experiment stores the
source take, immutable artifact hash, pipeline version and chosen configuration
with each attempt. Select available account voices per speaker, a supported
dialogue model, stability (0, 0.5 or 1) and an optional seed. Loading a source or
changing settings never synthesizes audio. **Audio generieren (Credits)** is the
explicit go-ahead to synthesize the whole prepared script without tagging again
or going through a review pipeline. Attempts reuse the existing chunk cache,
progress, error, stop/resume and authenticated playback interfaces. Saved
attempts can reload the same script and settings for another test.

A take is a **one-minute sample** (whole lines up to about 1,000 characters,
about $0.08) or the **whole episode** (about 14,000 characters for 15 minutes,
about $1.15). A take that fails part-way keeps its approval and **resumes** at
the first chunk without audio, so nothing is paid for twice.

`audio_join` joins the chunks with ffmpeg into one MP3: 0.3 s of silence between
chunks, no crossfade, loudness normalised to -16 LUFS. ffmpeg is the static
build in the `imageio-ffmpeg` wheel (about 80 MB; apt's ffmpeg adds 466 MB); the
image build checks that it runs. Each chunk's real length is measured, so the
per-line times ElevenLabs returns land on the joined file's clock: the run view
plays the take as one file, every line jumps there on click, the line being
heard is highlighted, and the MP3 can be downloaded. While a take is spoken the
run view lists every request with its state.

In a series every episode is a run with its own take. The series' plan tab
prepares takes for all finished episodes, shows their summed price, approves
them together and plays each episode; the voices are the format's default.

The speech provider sits behind `speech/base.py`; tests use a stub and never
call ElevenLabs. Without `ELEVENLABS_API_KEY` a take can still be tagged and
priced, and the console explains the setup instead of generating. In
production the key is a Secret Manager secret, bound once:

```sh
printf %s "$KEY" | gcloud secrets create elevenlabs-api-key --data-file=- --project=qlug-kalliope
gcloud secrets add-iam-policy-binding elevenlabs-api-key --project=qlug-kalliope \
  --member=serviceAccount:825911302957-compute@developer.gserviceaccount.com \
  --role=roles/secretmanager.secretAccessor
gcloud run services update kalliope --region europe-west1 --project=qlug-kalliope \
  --update-secrets ELEVENLABS_API_KEY=elevenlabs-api-key:latest
```

The experiment **Audio ausprobieren** runs the same tagging prompt and guard on a
short script typed with any speakers, or on a beat loaded from a run.

### Review links

Finished runs have a **Feedback** menu containing link creation and received
feedback; existing links show their status and revocation action on reload,
without creating a replacement. With an existing link the creation options are
collapsed initially. Completed series also have a **Review-Link** action. Choose the public
titles and a completed full recording for each episode, then create and copy the
link. Snapshot validation happens internally, without a preview step. Samples,
failed takes and unavailable mixes are excluded; a newer failed or pending take
does not hide an older ready full recording. Series sharing requires every planned episode to be finished
and includes the entire roster in plan order, without the hidden planning run.
Empty scripts and missing selected resources cannot be shared.
Sharing an episode without audio requires an explicit
acknowledgement; it remains script-only even if audio is generated later.

The link opens a separate reader without a login, for both signed-out and
signed-in visitors. It exposes the chosen titles, speaker/text segments,
creation/expiry dates, the selected audio, and only the source pages a shared
line actually cites. Those pages are frozen as images with the snapshot, and a
citation highlights that passage. Playback centers each newly active script line
using the recording's saved alignment and selects its first citation, centering
the highlighted source region if its frozen page is available. Resuming or seeking
recenters both panes even within the same line; ordinary time updates within a
line do not reset manual scrolling. While paused, source exploration stays manual,
including when a pending source image finishes loading; explicit citation clicks
and seeks still center the selected evidence. Untimed or uncited lines do not
invent alignment or evidence. Script and source panes have matching, independent
scroll viewports; the “Aktuelle Passage finden” button centers the script highlight
on request. Workspace identifiers, voice settings, edit
history and the internal review session are not included. Opening the page
does not start a review session or change a run's status. The public page
cannot edit the script.

On each line the reviewer can mark 👍 impressed, 🤢 not good, or 🤮 horrible.
The same mark again clears the reaction, without clearing its comment. Each line
also has a separate comment button usable without any emoji rating. Comments
and reactions save independently of each other and of the questionnaire. Comments save
after a short typing pause or on blur. Queued line changes coalesce to the latest
value; feedback writes are serialized and spaced at least 1.1 seconds apart.
Rate-limited writes retry once after a minute. Failed line changes remain dirty
with an explicit retry button; failed questionnaires can be submitted again.
Leaving with pending or failed line changes prompts the browser's standard
unsaved-changes warning. Forced termination cannot guarantee delivery.
The AI-slop control and reviewer-name field are no longer shown; legacy stored
data is retained. A prominent questionnaire button stays beside the player while
scrolling. The short questionnaire is always available and is offered again
when playback of any recording ends: optional stars from 0.5 to 5 in half
steps, what worked, and what did not. Playback ending is not evidence that
someone listened. The reviewer is remembered in that browser by an unguessable
key sent as a header and does not need an account. The server stores only the
key's SHA-256. Feedback is bound to that link and its frozen snapshot, addressed
by a share-local line ordinal.
Replacing the link does not carry marks onto the new text. Writes use the same
expiry and revocation check as reads. The owner summary on the share screen
lists reactions, stars, answers and lines with reactions or comments. Each link admits
at most 20 browser identities for feedback; clearing feedback does not release
a slot. Already admitted browsers can continue updating their feedback.

For internal editor controls, see [The console](#the-console).
The public page does not load third-party scripts. Comment text is not written
beside the bearer token in the application's access log; the reviewer key header is removed from that
log as well.

Each link is a frozen snapshot: when choosing audio, its original recorded
source script is captured together with the exact mix. An older recording is
labelled in the owner selection. Available recorded line timings are frozen as
well: playback highlights the actual current segment, including after seeking
or pausing. Recordings or existing links without alignment remain readable without invented
highlights. For series, the roster and order are frozen
too. Subsequent edits, new recordings or replanning do not change the link.
The snapshot references immutable private media in the artifact store rather
than making a bucket or existing authenticated audio endpoints public.

Anyone with the URL can read and listen, and can forward or save the content.
Links expire after 30 days and can be revoked manually. Revocation is checked
on every metadata/audio request, including byte ranges; it cannot recall bytes
already received. One link can be active per run/series; a run link and its
series link are independent. Replacing it requires
confirmation and atomically revokes the previous link only after the new
snapshot is validated. The full URL is returned once when created; management
lists dates and status, never the bearer token. Losing it requires replacement.
The database stores a SHA-256 token digest, not the token itself. Snapshot
artifacts and pinned media retain the existing artifact-store lifetime; expiry
or revocation removes access, not stored files.

Creation is disabled until `REVIEW_PUBLIC_ORIGIN` is configured as the reviewed
external HTTPS origin, with no credentials, path, query or fragment. The preview
API (used for internal snapshot validation), status and revocation remain
available without it. Owner preview, creation and revocation accept the configured
public origin even when a proxy makes the API report a different base address. Local development proxies also accept HTTP(S)
origins on `localhost`, `127.0.0.1` or `::1` when `Sec-Fetch-Site` is `same-origin`.
Other origins must match the API's base origin; `Sec-Fetch-Site: cross-site` is
always refused. These checks do not replace owner authentication.
This is a release decision: the configured address must reach this application's
reader, public metadata and token-scoped media endpoints without an ingress-level
login. The existing
deployment workflow assumes unauthenticated Cloud Run requests; that assumption
does not verify actual ingress/IAM or establish a canonical production URL.
Setting this variable alone cannot make a private/local service reachable. Keep
owner authentication and private storage intact; review ingress and any upstream
URL/access-log retention before enabling sharing. The app redacts bearer paths
from Uvicorn access logs and sends `no-store`, `no-referrer` and `noindex` on
sharing responses; upstream infrastructure may still record URLs. No deployment
or infrastructure access change is part of configuring the feature locally.

### Series of episodes

A run can also produce a **series**: several episodes from one document, each
sized so dialogue has room. Start one with "Serie" on the start screen.

```
series_plan_v0:  ingest → content_budget → series_plan     (once; then it waits)
each episode:    content_budget → select → outline          (all episodes first)
                 → script → gates                           (episodes in order)
```

`series_plan` decides which passages and learning goals belong to which
episode, with a title, a through-line and the key terms. Each episode's goals
are formulated by the same rule as the objectives node: a checkable change
derived from the desired outcome, limited to that episode's time, at the
lowest honest Bloom level. The number of episodes
asked for is what the content budget carries at the chosen length (two to eight)
unless the reviewer sets it. If the plan comes back with a different count, the
planner is asked once more; a count that still differs is kept and shown on the
plan screen. An episode that cannot carry three minutes is merged into its
neighbour. Passages the model left out join an episode only while it stays
inside its source-word budget; the rest stay unassigned. An assignment past
that budget is kept and named in a warning on the plan. An episode whose goals
are all unusable is kept as well, with the warning that it needs a new plan;
selection does not invent goals for it. Episodes keep the
length that was asked for. The model's split of passages is checked rather
than trusted.
The series always waits after the plan until someone approves it, and can be
planned again with another count, length or hint while it waits.

Each episode is an ordinary run (`runs.series_id`, `episode_index`), so review,
gates, the node inspector and the Markdown export work per episode unchanged.
Every episode is outlined before any script is written; then the scripts run in
order, and each one sees the plan, every other episode's running order and the
full text of the earlier episodes (for continuity only, without block ids — facts
still come only from its own passages). Two inputs keep the cache precise:
`episode_brief` (this episode's share of the plan) feeds the early nodes and
`series_context` feeds only the script. A flow without the planner never has
either, so its prompts and cache keys are unchanged (pinned by
`tests/test_series_golden.py`). Series that fail or were stopped resume where
they left off.

Known stop/resume limits: after resuming a series, its stopper label (and that of
queued episodes) can still name the previous stopper; resumed run-node accounting
can replace previously paid totals even though cached artifacts remain reusable.
Stopping the planner as an individual run can stop the series without copying the
planner's stopper label onto the series. These are accepted limits for PR #40.

The series view shows the whole pipeline on one pannable canvas — the planner as
a column, one lane per episode — compact (state, cost, tokens, duration) or with
every input and output, and follows the run live. After the last episode the S1
check reports how much of the document the series covers.

The experiment **Folgen planen** starts from the planner's system prompt and
the same user message production sends. Both stay editable, along with the
model settings, and a run can be saved and collected.

### Stopping generation

Choose **Stoppen** in the runs list or run detail, on a running series, or in
the run's audio panel, then confirm. Runs can be stopped while queued or running;
series while queued, planning, outlining or writing; audio takes while queued,
running or waiting for voice approval. A series waiting for plan approval is
already idle. Stopping a series leaves its plan and finished episodes intact
and prevents later stages from starting; audio takes are stopped separately.

An accepted stop marks the item **stopped** immediately. A provider request
already in progress can still finish and incur a charge; no subsequent paid
request is started. Its cost is recorded, its returned LLM text is discarded,
and a returned speech chunk is cached for reuse. Completed artifacts stay.
The open views refresh while the worker drains so final costs can appear after
the stopped status. Repeating a stop, or stopping an item that has already
ended, changes nothing.

Use **Erneut starten** for a standalone run or **Fortsetzen** for a stopped
series. A stopped audio take cannot be resumed directly: prepare a new take
with the same text, voices and settings to reuse its cached chunks. The accepted
series attribution and run accounting limits are described under
[Series of episodes](#series-of-episodes).

### Gates

| ID | Rule | On violation |
|---|---|---|
| G0 | ingestion confidence is not `low`; anchor integrity ≥ 0.99 | warn (fail below 0.95) |
| G1 | every anchor resolves to real characters in a real block | fail |
| G2 | every `claim` segment has at least one anchor | fail |
| G3 | no segment shares a 6-gram with non-narratable text | fail |
| G4 | total words within ±15% of `target_minutes × wpm(language)` | fail |
| G5 | every stated objective is served by a beat (in a series: the episode's objectives) | warn, skipped when none |
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

Per segment: 👍 impressed, 🤢 not good, or 🤮 horrible, optional slop tag and
marked-line comment, plus edit inline, general comment, and **undo**.
These internal controls are distinct from the public [Review links](#review-links).
These reactions replace the accept, flag and tag controls; older events remain
in the event stream. Reactions are separate from the edit undo stack.
**Saving an edit requires a reason code** — the eleven codes
are one shared definition consumed by both backend and frontend, and `OTHER`
additionally requires a note. Selection, outline and **block zone labels** are
all reviewable; a zone correction is stored as an `EditEvent` and is how the
system adapts to an unfamiliar document family without anyone writing template
code. Comments and saved reactions are shown against their segment, so a
reviewer coming back to a script sees what was already said about it.

**Undo deletes nothing.** Segment state is a fold over the event stream rather
than a stored flag, so an undo is itself an event that pops the last accept,
edit or flag — one step at a time, and the whole sequence still reaches the
evaluation export. An edit that was taken back does not reach the stored script
either.

Documents are filed in **folders**: an ordinary tree, drag a card onto a folder
to file it. Folders carry no permissions and no effect on parsing or runs, and
deleting one never deletes a document — the contents move up to the parent.

Collected experiment outputs get the same kind of tree in the **Sammlung**, a
tab of the Experimente page: every collected output of every experiment in one
list, filed into folders and subfolders by dragging a card onto a folder, by
"In Ordner …", or many at once. Each experiment page has a "Sammeln in" folder
that every "Sammeln" files into. The two trees are separate; both follow the
rules in `app/folder_tree.py`.

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

The script node's **App-Längenlimit ausschalten** switch saves `max_tokens: 0`
to remove the app's output cap per beat. OpenAI requests omit the optional cap;
providers requiring a cap use the model's catalogue limit. Model and context
limits still apply, including reasoning tokens; output limits come from the
[model catalogue](backend/app/llm/registry.py).
A length stop emits a progress warning that output may be incomplete; usable
JSON is parsed as before. There is no automatic continuation. Existing numeric
settings and saved revisions stay intact.

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
GET /api/series/{id}/export?format=zip  # one Markdown file per finished episode, plus the plan
GET /api/exports/edit-events.jsonl      # every edit event, one JSON object per line
```

Other endpoints worth knowing:

```
GET /api/gates                          # what every gate checks, and how
GET /api/runs/{id}/gates/detail         # that, joined with what it measured here
GET /api/flows/{id}/graph               # the flow's nodes, ports and wiring
GET /api/runs/{id}/graph                # the same, as it actually ran
GET /api/runs/{id}/nodes/{node}/io      # one node's real inputs and output
GET /api/documents/{id}/budget          # how many minutes a document supports
POST /api/series                        # start a series; approve, replan, stop and resume below it
GET /api/series/{id}/graph              # the planner run and every episode run
GET /api/series/{id}/events             # one live stream for the whole series
GET /api/folders                        # the folder tree, with rolled-up counts
GET /api/experiment-folders             # the Sammlung's folder tree, same rules
GET /api/experiments/outputs            # every collected output; folder, experiment, q filters
POST /api/experiments/outputs/move      # file one or many outputs into a folder
```

The JSONL file is what the evaluation work consumes. Each line carries the
reviewer, the reason code, and the text before and after.

## Layout

```
backend/app/
├─ main.py config.py db.py security.py accounts.py errors.py events.py migrations.py worker.py cli.py
├─ models/       SQLAlchemy ORM
├─ schemas/      pydantic domain + API models, zone taxonomy, reason codes
├─ api/          auth · folders · documents · runs · series · review · audio · sharing
├─ llm/          provider protocol, pricing and capability registry, three providers
├─ speech/       speech provider protocol, client (retries, cost), ElevenLabs
├─ lang/         detection, per-language resources, readability formulas
├─ experiments/  prompt experiments (script, outline, selection, series planner, audio tags) · verbalized sampling
├─ ingestion/    runs · extract · layout · repetition · normalize · blocks ·
│                anchors · structure · zones · tables · report · pipeline
└─ pipeline/
   ├─ framework/ node · registry · artifacts · keys · runner
   ├─ nodes/     ingest · content_budget · select · outline · script · series_plan ·
   │             audio_script · audio_approval · audio_render
   ├─ gates/     G0–G8
   └─ flows/     baseline_v0.yaml · series_plan_v0.yaml · elevenlabs_dialog_v0.yaml
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

The fact-check repair loop; AI persona feedback; pairwise evaluation, judges or
rubrics; vision-based extraction of diagram content; multi-tenancy or
customer-facing upload; OCR of scanned pages. None of these are partially
implemented.
# kaliope

## Deploy

Pushes to `main` deploy automatically to the Cloud Run service `kalliope`
(`qlug-kalliope`, `europe-west1`): GitHub Actions builds the image, pushes it to
Artifact Registry (tagged with the commit SHA and `latest`), and updates only the
image of the service, so its env, secrets, bucket volume and scaling stay as
configured. The job fails unless `/api/health` reports `"status":"ok"`.
Authentication uses Workload Identity Federation, with no stored keys.

The container image defaults to `SQLITE_JOURNAL_MODE=DELETE` for the Cloud
Storage bucket mounted at `/data`. WAL does not work on that network mount
(logins fail with `stale file handle` on `kalliope.db-wal`). Local development
stays on WAL. Accepted values are `WAL`, `DELETE`, `TRUNCATE` or `PERSIST`; a
leftover `-wal` is checkpointed into the database on the next start.

Setup (service account, Workload Identity pool, repo variables), rollback and
manual deploy are described in [AGENTS.md](AGENTS.md#deploy).
