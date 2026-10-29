<script setup lang="ts">
/**
 * Experiment "ElevenLabs ausprobieren": speak a prepared audio script again.
 *
 * Same page as the other experiments: left the setup in three tabs (the
 * script segment by segment, the voices with a sample each, the model), right
 * the current audio and the earlier attempts. The script comes from a take
 * that was already tagged; segments can be chosen and their text edited, the
 * stored artifact never changes. Only "Audio generieren" spends credits.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

import { audioApi, takeActive } from '@/api/audio'
import { ApiError } from '@/api/client'
import type {
  AudioTakeOut,
  ExperimentDetail,
  ExperimentRun,
  ExperimentSource,
  ExperimentSourceMeta,
  SpeechVoice,
} from '@/api/types'
import LoadingState from '@/components/LoadingState.vue'
import ExperimentFrame from '@/components/experiments/ExperimentFrame.vue'
import SourceLoader from '@/components/experiments/SourceLoader.vue'
import { getExperiment, isFinished, listRuns, loadSource, startRun, waitForRun } from '@/experiments/api'
import { fill, t } from '@/i18n'

import ScriptSegments from './ScriptSegments.vue'
import TakeResult from './TakeResult.vue'
import VoiceCasting from './VoiceCasting.vue'
import {
  DEFAULT_CHARACTERS_PER_SECOND,
  chosenLines,
  clock,
  emptySetup,
  estimate,
  missingVoices,
  money,
  normalise,
  payload,
  textOf,
  type Line,
  type Setup,
} from './synthesis'

const KEY = 'audio_generation'
const DRAFT_KEY = `kalliope-exp:${KEY}`
const POLL_MS = 1500
const labels = t.experiments.audioGeneration

type Tab = 'script' | 'voices' | 'model'
interface Draft {
  setup: Setup
  source: ExperimentSourceMeta | null
}

const detail = ref<ExperimentDetail<Setup> | null>(null)
const source = ref<ExperimentSourceMeta | null>(null)
const setup = ref<Setup>(emptySetup())
const lines = ref<Line[]>([])
const voices = ref<SpeechVoice[]>([])
const voicesLoading = ref(false)
const configured = ref(true)
const setupMessage = ref('')
const rates = ref<Record<string, number>>({})
const runs = ref<ExperimentRun<Setup>[]>([])
const takes = ref<Record<string, AudioTakeOut>>({})
const shown = ref<string | null>(null)
const tab = ref<Tab>('script')
const loading = ref(true)
const starting = ref(false)
const error = ref('')
const notice = ref('')
let gone = false
let refreshNeeded = false
let refreshVersion = 0
let timer: ReturnType<typeof setTimeout> | undefined

const extras = computed(() => detail.value?.extras ?? {})
const models = computed(() => (extras.value.models as string[] | undefined) ?? ['eleven_v4', 'eleven_v3'])
const rate = computed(() => rates.value[setup.value.voice_cast.model_id] ?? 0)
const pace = computed(
  () => (extras.value.characters_per_second as number | undefined) ?? DEFAULT_CHARACTERS_PER_SECOND,
)
const sampleCharacters = computed(() => (extras.value.sample_characters as number | undefined) ?? 64)
const speakers = computed(() => [...new Set(lines.value.map((line) => line.speaker))])
const needed = computed(() => [...new Set(chosenLines(lines.value, setup.value).map((l) => l.speaker))])
const totals = computed(() => estimate(lines.value, setup.value, rate.value, pace.value))
const missing = computed(() => missingVoices(lines.value, setup.value))
const blank = computed(() =>
  lines.value
    .map((line, index) => ({ line, n: index + 1 }))
    .filter(({ line }) => chosenLines(lines.value, setup.value).includes(line) && !textOf(line, setup.value).trim())
    .map(({ n }) => n),
)
const problems = computed(() => {
  if (!lines.value.length) return []
  const found: string[] = []
  if (!totals.value.segments) found.push(labels.nothingSelected)
  if (blank.value.length) found.push(fill(labels.emptySelected, { list: blank.value.join(', ') }))
  if (missing.value.length) found.push(fill(labels.missingVoices, { names: missing.value.join(', ') }))
  return found
})
const canGenerate = computed(
  () => configured.value && Boolean(source.value) && lines.value.length > 0 && !problems.value.length && !starting.value,
)
const stabilityLabel = computed(() => {
  const value = setup.value.voice_cast.stability
  return value === 0 ? labels.creative : value === 1 ? labels.robust : labels.natural
})
const current = computed(() => runs.value.find((run) => run.id === shown.value) ?? runs.value[0] ?? null)
const earlier = computed(() => runs.value.filter((run) => run.id !== current.value?.id))
const busy = computed(
  () =>
    starting.value ||
    runs.value.some((run) => !isFinished(run)) ||
    Object.values(takes.value).some((take) => takeActive(take.status)),
)

function fail(exc: unknown): void {
  error.value = exc instanceof ApiError ? exc.detail : exc instanceof Error ? exc.message : t.errors.generic
}

function flash(message: string): void {
  notice.value = message
  window.setTimeout(() => {
    if (notice.value === message) notice.value = ''
  }, 3500)
}

function takeOf(run: ExperimentRun<Setup>): AudioTakeOut | null {
  const id = run.output?.take_id
  return typeof id === 'string' ? (takes.value[id] ?? null) : null
}

// ----------------------------------------------------------------- source

function linesOf(value: ExperimentSource): Line[] {
  return (JSON.parse(value.fields.audio_script ?? '{}') as { lines?: Line[] }).lines ?? []
}

/** Keep the voices already chosen; add an empty choice for every new speaker. */
function castFor(names: string[]): void {
  const known = setup.value.voice_cast.voices
  setup.value.voice_cast.voices = names.map(
    (speaker) => known.find((v) => v.speaker === speaker) ?? { speaker, voice_id: '' },
  )
}

function loaded(value: ExperimentSource): void {
  const changed = value.fields.artifact_hash !== setup.value.artifact_hash
  source.value = value.source
  lines.value = linesOf(value)
  setup.value.artifact_hash = value.fields.artifact_hash ?? ''
  if (changed) {
    setup.value.segment_ids = null
    setup.value.edits = {}
  }
  castFor(speakers.value)
  error.value = ''
  tab.value = 'script'
}

function clearSource(): void {
  source.value = null
  lines.value = []
  setup.value = { ...emptySetup(), voice_cast: setup.value.voice_cast }
}

async function reload(from: ExperimentSourceMeta | null): Promise<void> {
  if (!from) return
  const value = await loadSource(KEY, from.run_id, from.beat_id ?? null)
  source.value = value.source
  lines.value = linesOf(value)
  castFor(speakers.value)
  if (value.fields.artifact_hash !== setup.value.artifact_hash) {
    // The prepared take changed since this setup was made; its edits no longer apply.
    setup.value = { ...setup.value, artifact_hash: value.fields.artifact_hash ?? '', segment_ids: null, edits: {} }
  }
}

// ------------------------------------------------------------------ setup

function select(ids: string[] | null): void {
  setup.value.segment_ids = ids
}

function edit(id: string, text: string | null): void {
  const { [id]: _old, ...rest } = setup.value.edits
  setup.value.edits = text === null ? rest : { ...rest, [id]: text }
}

function assign(speaker: string, voiceId: string): void {
  const voice = voices.value.find((v) => v.voice_id === voiceId)
  setup.value.voice_cast.voices = setup.value.voice_cast.voices.map((choice) =>
    choice.speaker === speaker ? { speaker, voice_id: voiceId, voice_name: voice?.name ?? null } : choice,
  )
}

function setSeed(raw: string): void {
  setup.value.voice_cast.seed = raw === '' ? null : Number(raw)
}

async function adopt(run: ExperimentRun<Setup>): Promise<void> {
  try {
    setup.value = normalise(JSON.parse(JSON.stringify(run.setup)) as Setup)
    await reload(run.source)
    flash(labels.adopted)
  } catch (exc) {
    fail(exc)
  }
}

// ------------------------------------------------------------------- runs

async function refresh(): Promise<void> {
  const version = ++refreshVersion
  try {
    const nextRuns = (await listRuns<Setup>(KEY)).map((run) => ({ ...run, setup: normalise(run.setup) }))
    const runIds = [...new Set(nextRuns.map((run) => run.output?.run_id).filter((id): id is string => typeof id === 'string'))]
    const wanted = new Set(nextRuns.map((run) => run.output?.take_id))
    const results = await Promise.all(runIds.map((id) => audioApi.takes(id)))
    const nextTakes = Object.fromEntries(
      results.flatMap((result) => result.takes).filter((take) => wanted.has(take.id)).map((take) => [take.id, take]),
    )
    if (gone || version !== refreshVersion) return
    runs.value = nextRuns
    takes.value = nextTakes
    refreshNeeded = false
  } catch (exc) {
    if (gone || version !== refreshVersion) return
    refreshNeeded = true
    throw exc
  }
}

function schedule(): void {
  clearTimeout(timer)
  if (!gone && (busy.value || refreshNeeded)) timer = setTimeout(poll, POLL_MS)
}

async function poll(): Promise<void> {
  try {
    await refresh()
  } catch (exc) {
    fail(exc)
  }
  schedule()
}

async function generate(): Promise<void> {
  if (!canGenerate.value) return
  starting.value = true
  error.value = ''
  try {
    const run = await startRun<Setup>(KEY, payload(lines.value, setup.value), source.value)
    refreshVersion++
    refreshNeeded = true
    runs.value = [{ ...run, setup: normalise(run.setup) }, ...runs.value]
    shown.value = run.id
    await waitForRun(run.id, () => gone)
    await refresh()
    void refreshStats()
  } catch (exc) {
    fail(exc)
  } finally {
    starting.value = false
    schedule()
  }
}

async function act(call: () => Promise<unknown>): Promise<void> {
  try {
    await call()
    await refresh()
  } catch (exc) {
    fail(exc)
  }
  schedule()
}

async function refreshStats(): Promise<void> {
  try {
    const next = await getExperiment<Setup>(KEY)
    if (detail.value) detail.value = { ...detail.value, stats: next.stats }
  } catch {
    /* stats are informational */
  }
}

// ------------------------------------------------------------------- load

watch(
  [setup, source],
  () => {
    if (loading.value) return
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ setup: setup.value, source: source.value }))
    } catch {
      /* draft storage is optional */
    }
  },
  { deep: true },
)

async function loadVoices(): Promise<void> {
  voicesLoading.value = true
  try {
    voices.value = await audioApi.voices()
  } catch (exc) {
    fail(exc)
  } finally {
    voicesLoading.value = false
  }
}

onMounted(async () => {
  try {
    const [loadedDetail, status] = await Promise.all([getExperiment<Setup>(KEY), audioApi.status()])
    detail.value = loadedDetail
    rates.value = status.usd_per_1k_characters ?? {}
    configured.value = status.configured
    setupMessage.value = status.message ?? ''
    if (status.configured) void loadVoices()
    await refresh()
    let draft: Draft | null = null
    try {
      draft = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? 'null') as Draft | null
    } catch {
      /* ignore malformed drafts */
    }
    const start = draft?.setup?.artifact_hash && draft.source ? draft : runs.value[0]
    if (start) {
      setup.value = normalise(JSON.parse(JSON.stringify(start.setup)) as Setup)
      await reload(start.source)
    }
  } catch (exc) {
    fail(exc)
  } finally {
    loading.value = false
    schedule()
  }
})

onUnmounted(() => {
  gone = true
  clearTimeout(timer)
})

onBeforeRouteLeave(() => {
  if (starting.value) return window.confirm(t.experiments.leaveConfirm)
  return true
})
</script>

<template>
  <ExperimentFrame
    experiment-key="audio_generation"
    :title="detail?.title ?? ''"
    :lead="detail?.summary"
    :spent-usd="detail?.stats.spent_usd"
    :run-count="detail?.stats.run_count"
  >
    <template #actions>
      <button class="btn btn--primary" :disabled="!canGenerate" data-generate @click="generate">
        <template v-if="starting">{{ labels.starting }}</template>
        <template v-else-if="lines.length">{{ fill(labels.generateCost, { cost: money(totals.costUsd) }) }}</template>
        <template v-else>{{ labels.generate }}</template>
      </button>
    </template>

    <template #banner>
      <p v-if="!configured" class="banner banner--warn" role="status">
        <b>{{ labels.notConfigured }}</b> {{ setupMessage }}
      </p>
      <p v-if="error" class="banner banner--fail" role="alert">
        {{ error }}
        <button class="btn btn--ghost btn--sm" @click="error = ''">{{ t.common.close }}</button>
      </p>
      <p v-if="notice" class="banner" role="status">{{ notice }}</p>
    </template>

    <template #setup>
      <LoadingState v-if="loading" />
      <template v-else>
        <div class="spread">
          <p class="eyebrow">{{ t.experiments.setup }}</p>
        </div>
        <div class="tabs" role="tablist">
          <button
            v-for="name in ['script', 'voices', 'model'] as const"
            :key="name"
            class="tab"
            role="tab"
            :aria-selected="tab === name"
            :data-tab="name"
            @click="tab = name"
          >
            {{ labels.tabs[name] }}
            <span v-if="name === 'voices' && missing.length" class="dot" aria-hidden="true" />
          </button>
        </div>
        <div class="summary">
          <span class="badge badge--idle">{{ setup.voice_cast.model_id }}</span>
          <span class="badge badge--idle">{{ labels.stability }} {{ stabilityLabel }}</span>
        </div>

        <div v-if="lines.length" class="estimate" data-estimate>
          <div class="estimate__nums">
            <span><b class="num">{{ fill(labels.selected, { n: totals.segments, total: lines.length }) }}</b></span>
            <span class="num">{{ fill(labels.characters, { n: totals.characters.toLocaleString('de-DE') }) }}</span>
            <span class="num">{{ fill(labels.duration, { duration: clock(totals.seconds) }) }}</span>
            <span class="num estimate__cost">{{ fill(labels.cost, { cost: money(totals.costUsd) }) }}</span>
          </div>
          <p class="meta">{{ fill(labels.estimateHint, { rate: money(rate) }) }}</p>
          <p v-for="problem in problems" :key="problem" class="estimate__problem">{{ problem }}</p>
        </div>

        <!-- script -->
        <div v-if="tab === 'script'" class="stack">
          <SourceLoader
            :experiment-key="KEY"
            :source="source"
            :lead="labels.sourceLead"
            :selection-label="labels.selection"
            :reset-label="labels.reset"
            :apply-label="labels.apply"
            hide-word-budget
            :sample-label="labels.noSource"
            @loaded="loaded"
            @error="fail"
            @sample="clearSource"
          />
          <div v-if="!lines.length" class="empty">
            <p class="eyebrow">{{ labels.emptyTitle }}</p>
            <p class="muted small">{{ labels.emptyLead }}</p>
          </div>
          <ScriptSegments v-else :lines="lines" :setup="setup" @select="select" @edit="edit" />
        </div>

        <!-- voices -->
        <div v-else-if="tab === 'voices'" class="stack">
          <p v-if="!lines.length" class="muted small">{{ labels.emptyLead }}</p>
          <VoiceCasting
            :speakers="speakers"
            :needed="needed"
            :voices="voices"
            :cast="setup.voice_cast"
            :loading="voicesLoading"
            :rate="rate"
            :sample-characters="sampleCharacters"
            @assign="assign"
            @error="fail"
          />
        </div>

        <!-- model -->
        <div v-else class="stack">
          <div class="param">
            <span class="param__label">{{ labels.model }}</span>
            <p class="hint">{{ labels.modelHint }}</p>
            <span class="seg" role="group" :aria-label="labels.model">
              <button
                v-for="model in models"
                :key="model"
                :aria-pressed="setup.voice_cast.model_id === model"
                @click="setup.voice_cast.model_id = model"
              >
                {{ model }}
              </button>
            </span>
          </div>
          <div class="param">
            <span class="param__label">{{ labels.stability }}</span>
            <p class="hint">{{ labels.stabilityHint }}</p>
            <span class="seg" role="group" :aria-label="labels.stability">
              <button
                v-for="choice in [
                  { value: 0, text: labels.creative },
                  { value: 0.5, text: labels.natural },
                  { value: 1, text: labels.robust },
                ]"
                :key="choice.value"
                :aria-pressed="setup.voice_cast.stability === choice.value"
                @click="setup.voice_cast.stability = choice.value"
              >
                {{ choice.text }} · {{ choice.value }}
              </button>
            </span>
          </div>
          <label class="param">
            <span class="param__label">{{ labels.seed }}</span>
            <p class="hint">{{ labels.seedHint }}</p>
            <input
              class="input seed"
              type="number"
              min="0"
              max="4294967295"
              :value="setup.voice_cast.seed ?? ''"
              @input="setSeed(($event.target as HTMLInputElement).value)"
            />
          </label>
        </div>
      </template>
    </template>

    <template #outputs>
      <div class="spread">
        <p class="eyebrow">{{ labels.result }}</p>
        <span class="meta">{{ labels.resultHint }}</span>
      </div>
      <LoadingState v-if="loading" />
      <LoadingState v-else-if="starting && !current" :label="labels.starting" />
      <div v-else-if="!current" class="empty empty--result">
        <p class="muted small">{{ labels.noResult }}</p>
      </div>
      <TakeResult
        v-else
        :run="current"
        :take="takeOf(current)"
        current
        @stop="(take) => act(() => audioApi.stop(take.id))"
        @resume="(take) => act(() => audioApi.resume(take.id))"
      >
        <template #foot>
          <button class="btn btn--ghost btn--sm" @click="adopt(current)">{{ labels.adopt }}</button>
        </template>
      </TakeResult>

      <template v-if="!loading">
        <p class="eyebrow attempts">{{ labels.attempts }}</p>
        <p v-if="!earlier.length" class="muted small">{{ labels.attemptsEmpty }}</p>
        <div class="stack">
          <TakeResult
            v-for="run in earlier"
            :key="run.id"
            :run="run"
            :take="takeOf(run)"
            @stop="(take) => act(() => audioApi.stop(take.id))"
            @resume="(take) => act(() => audioApi.resume(take.id))"
          >
            <template #foot>
              <button class="btn btn--ghost btn--sm" @click="shown = run.id">{{ labels.listen }}</button>
              <button class="btn btn--ghost btn--sm" @click="adopt(run)">{{ labels.adopt }}</button>
            </template>
          </TakeResult>
        </div>
      </template>
    </template>
  </ExperimentFrame>
</template>

<style scoped>
.banner {
  margin: var(--s3) var(--s6) 0;
  padding: var(--s3) var(--s4);
  border-radius: var(--r-md);
  background: var(--mark-soft);
  color: var(--mark-deep);
  font-size: var(--t-sm);
}

.banner--warn {
  background: var(--warn-soft);
  color: var(--warn);
}

.banner--fail {
  display: flex;
  align-items: center;
  gap: var(--s3);
  background: var(--fail-soft);
  color: var(--fail);
}

.tabs {
  display: flex;
  gap: 2px;
  flex-wrap: wrap;
  margin: var(--s2) 0 var(--s3);
  padding: 3px;
  background: var(--sunk);
  border-radius: var(--r-md);
}

.tab {
  position: relative;
  flex: 1;
  padding: 6px 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--ink-2);
  font-size: var(--t-sm);
  cursor: pointer;
}

.tab[aria-selected='true'] {
  background: var(--card);
  color: var(--ink);
  font-weight: 560;
  box-shadow: var(--shadow-sm);
}

.dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-left: 4px;
  border-radius: 50%;
  background: var(--warn);
  vertical-align: middle;
}

.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: var(--s3);
}

.estimate {
  position: sticky;
  top: calc(-1 * var(--s4));
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: var(--s3);
  padding: var(--s3) var(--s4);
  background: var(--card);
  border: 1px solid var(--rule);
  border-left: 3px solid var(--mark);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-sm);
}

.estimate__nums {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s4);
  font-size: var(--t-sm);
}

.estimate__cost {
  margin-left: auto;
  font-weight: 650;
  color: var(--mark-deep);
}

.estimate__problem {
  margin: 2px 0 0;
  padding: 3px 8px;
  border-radius: var(--r-md);
  background: var(--warn-soft);
  color: var(--warn);
  font-size: var(--t-xs);
}

.empty {
  padding: var(--s5) var(--s4);
  border: 1px dashed var(--rule-strong);
  border-radius: var(--r-lg);
  text-align: center;
}

.empty--result {
  margin-top: var(--s3);
}

.small {
  font-size: var(--t-sm);
}

.param {
  display: block;
  padding: var(--s3) var(--s4);
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
}

.param__label {
  font-weight: 600;
  font-size: var(--t-sm);
}

.hint {
  margin: 4px 0 var(--s2);
  font-size: var(--t-sm);
  color: var(--ink-3);
}

.seed {
  width: 12rem;
  font-family: var(--mono);
}

.seg {
  display: inline-flex;
  border: 1px solid var(--rule-strong);
  border-radius: var(--r-md);
  overflow: hidden;
}

.seg button {
  border: 0;
  background: var(--card);
  padding: 5px 12px;
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.04em;
  color: var(--ink-2);
  cursor: pointer;
}

.seg button + button {
  border-left: 1px solid var(--rule-strong);
}

.seg button[aria-pressed='true'] {
  background: var(--ink);
  color: var(--chrome);
}

.attempts {
  margin: var(--s5) 0 var(--s2);
}
</style>
