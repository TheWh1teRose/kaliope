<script setup lang="ts">
/**
 * Experiment "Verbalized Sampling": k versions of one beat in one call, next
 * to one plain call, compared blind.
 *
 * Left: the setup (shared base prompt, VS instruction, user template, the
 * input fields, pasted or loaded from a run, model settings, k). Right: the current run with its
 * drafts, blind until revealed, and the collected drafts with a tally by
 * method. Nothing here creates a run, a note or a pipeline node.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

import { ApiError } from '@/api/client'
import type {
  ExperimentDetail,
  ExperimentOutput,
  ExperimentRun,
  ExperimentSource,
  ExperimentSourceMeta,
  ModelCatalogue,
  ModelSettings as SharedSettings,
  VSSetup,
} from '@/api/types'
import CollectFolder from '@/components/experiments/CollectFolder.vue'
import CollectionList from '@/components/experiments/CollectionList.vue'
import ExperimentFrame from '@/components/experiments/ExperimentFrame.vue'
import FieldCard, { type FieldOrigin } from '@/components/experiments/FieldCard.vue'
import ModelSettings from '@/components/experiments/ModelSettings.vue'
import OutputCard from '@/components/experiments/OutputCard.vue'
import SourceLoader from '@/components/experiments/SourceLoader.vue'
import {
  collect,
  deleteOutput,
  getExperiment,
  isFinished,
  listOutputs,
  listRuns,
  startRun,
  waitForRun,
} from '@/experiments/api'
import { loadModelCatalogue, settingsState } from '@/experiments/modelSettings'
import { type Badge, runBadges } from '@/experiments/outputMeta'
import { FIELD_NAME, originsFor, placeholders, refill } from '@/experiments/promptSetup'
import {
  type VSDraft,
  asOutput,
  blindOrder,
  citationBadge,
  costFacts,
  draftBadges,
  drafts,
  letter,
  probabilityText,
  readRevealed,
  readSetupDraft,
  tally,
  writeRevealed,
  writeSetupDraft,
} from '@/experiments/verbalized_sampling/vs'
import {
  collectedBadges as collectedBadgesOf,
  collectedPayload,
  collectedTitle as collectedTitleOf,
} from '@/experiments/verbalized_sampling/collected'
import { t } from '@/i18n'

const KEY = 'verbalized_sampling'
const labels = t.experiments
const vsLabels = t.experiments.vs

type Tab = 'prompt' | 'input' | 'model' | 'vs'
const TABS: Tab[] = ['prompt', 'input', 'model', 'vs']

const detail = ref<ExperimentDetail<VSSetup> | null>(null)
const catalogue = ref<ModelCatalogue | null>(null)
const setup = ref<VSSetup | null>(null)
const origins = ref<Record<string, FieldOrigin>>({})
const source = ref<ExperimentSourceMeta | null>(null)
const current = ref<ExperimentRun<VSSetup> | null>(null)
const outputs = ref<ExperimentOutput<VSSetup>[]>([])
const revealed = ref<Set<string>>(readRevealed())
const tab = ref<Tab>('prompt')
const loading = ref(true)
const error = ref('')
/** The "Sammeln in" folder; `null` files under "Ohne Ordner". */
const collectFolder = ref<string | null>(null)
const notice = ref('')
const running = ref(false)
let leaving = false

const defaults = computed(() => detail.value?.defaults ?? null)
const instructions = computed(
  () => (detail.value?.extras.vs_instructions ?? {}) as Record<'kalliope' | 'paper', string>,
)
const vsSchema = computed(() => JSON.stringify(detail.value?.extras.vs_schema ?? {}, null, 2))
const fieldSpecs = computed(
  () => new Map((detail.value?.fields ?? []).map((spec) => [spec.key, spec] as const)),
)
const fieldNames = computed(() => {
  const fields = setup.value?.fields ?? {}
  const known = [...fieldSpecs.value.keys()].filter((name) => name in fields)
  const extra = Object.keys(fields).filter((name) => !fieldSpecs.value.has(name))
  return [...known, ...extra]
})
const chips = computed(() =>
  setup.value ? placeholders(setup.value.user_template, setup.value.fields) : [],
)
const missing = computed(() => chips.value.filter((chip) => chip.state === 'missing'))
const model = computed(() =>
  catalogue.value?.models.find((m) => m.id === setup.value?.settings.model),
)
const kValid = computed(() => {
  const k = setup.value?.k
  return typeof k === 'number' && Number.isInteger(k) && k >= 2 && k <= 8
})
const settingsValid = computed(() =>
  model.value && setup.value ? settingsState(model.value, setup.value.settings).valid : false,
)
const canRun = computed(
  () =>
    Boolean(setup.value) &&
    !running.value &&
    !missing.value.length &&
    kValid.value &&
    settingsValid.value,
)

function changed(field: 'base_prompt' | 'vs_instruction' | 'user_template'): boolean {
  return !!setup.value && !!defaults.value && setup.value[field] !== defaults.value[field]
}

const wording = computed(() => {
  const text = setup.value?.vs_instruction
  if (text === instructions.value.kalliope) return 'kalliope'
  if (text === instructions.value.paper) return 'paper'
  return null
})
const pasted = computed(() =>
  Object.values(origins.value).some((origin) => origin === 'edited' || origin === 'custom'),
)
const summaryChips = computed(() => {
  const s = setup.value?.settings
  if (!s || !setup.value) return []
  return [
    s.model,
    s.temperature !== null ? `T ${s.temperature}` : 'T —',
    `max ${s.max_tokens.toLocaleString('de-DE')}`,
    `k ${setup.value.k} · ${vsLabels.variantStandard}`,
    vsLabels.comparisonValue,
    pasted.value
      ? labels.edited
      : source.value?.beat_title
        ? `${labels.sourceBeat} ${source.value.beat_position}/${source.value.beat_total}`
        : labels.sample,
  ]
})

// ------------------------------------------------------------- current run

const output = computed(() => asOutput(current.value?.output))
const blind = computed(() => !!current.value && !revealed.value.has(current.value.id))
const currentDrafts = computed<VSDraft[]>(() => {
  const all = drafts(output.value)
  if (!current.value || !blind.value) return all
  const order = blindOrder(
    current.value.id,
    all.map((d) => d.item),
  )
  return order.map((item) => all.find((d) => d.item === item)!)
})
const savedItems = computed(
  () => new Map((current.value?.items ?? []).map((item) => [item.item, item.output_id])),
)
const runBadgeList = computed<Badge[]>(() => {
  if (!current.value || !output.value) return []
  return [
    ...runBadges(current.value),
    { text: `k ${output.value.k} · ${vsLabels.variantStandard}` },
    { text: vsLabels.comparisonValue },
  ]
})
const runWarnings = computed(() => {
  if (!current.value) return []
  const out = [...current.value.warnings]
  const detailed = [...(output.value?.vs.warnings ?? []), ...(output.value?.baseline.warnings ?? [])]
  if (!blind.value) out.push(...detailed)
  else if (detailed.length) out.push(`${vsLabels.blindWarnings} (${detailed.length}).`)
  return out
})
const blindJson = computed(() =>
  JSON.stringify(
    currentDrafts.value.map((d, i) => ({
      [vsLabels.draft]: letter(i),
      segments: d.segments,
    })),
    null,
    2,
  ),
)

function draftTitle(draft: VSDraft, position: number): string {
  if (blind.value) return `${vsLabels.draft} ${letter(position)}`
  return draft.source === 'vs' ? `${vsLabels.withVs} #${draft.index + 1}` : vsLabels.withoutVs
}

function setRevealed(runId: string, on: boolean): void {
  const next = new Set(revealed.value)
  if (on) next.add(runId)
  else next.delete(runId)
  revealed.value = next
  writeRevealed(next)
}

// -------------------------------------------------------------- collection

const counts = computed(() => tally(outputs.value, revealed.value))

function collectedTitle(output: ExperimentOutput<unknown>): string {
  return collectedTitleOf(output, revealed.value)
}

function collectedBadges(output: ExperimentOutput<unknown>): Badge[] {
  return collectedBadgesOf(output, revealed.value)
}

function revealAll(): void {
  const next = new Set(revealed.value)
  for (const output of outputs.value) if (output.run_id) next.add(output.run_id)
  revealed.value = next
  writeRevealed(next)
}

// ------------------------------------------------------------------ helpers

function fail(exc: unknown): void {
  error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
}

function flash(message: string): void {
  notice.value = message
  window.setTimeout(() => {
    if (notice.value === message) notice.value = ''
  }, 3500)
}

function freshSetup(): VSSetup {
  return JSON.parse(JSON.stringify(defaults.value)) as VSSetup
}

function updateSettings(next: SharedSettings): void {
  if (!setup.value) return
  setup.value.settings = { ...setup.value.settings, ...next }
}

function setField(name: string, value: string): void {
  if (!setup.value) return
  setup.value.fields = { ...setup.value.fields, [name]: value }
  if (origins.value[name] !== 'custom') origins.value = { ...origins.value, [name]: 'edited' }
}

function removeField(name: string): void {
  if (!setup.value) return
  const { [name]: _removed, ...rest } = setup.value.fields
  setup.value.fields = rest
  const { [name]: _origin, ...others } = origins.value
  origins.value = others
}

function addField(): void {
  if (!setup.value) return
  const raw = window.prompt(labels.fieldName, '')
  if (raw === null) return
  const name = raw.trim()
  if (!FIELD_NAME.test(name)) {
    window.alert(labels.fieldNameInvalid)
    return
  }
  if (name in setup.value.fields) {
    window.alert(labels.fieldExists)
    return
  }
  setup.value.fields = { ...setup.value.fields, [name]: '' }
  origins.value = { ...origins.value, [name]: 'custom' }
}

function applySource(loaded: ExperimentSource): void {
  if (!setup.value) return
  const next = refill(setup.value.fields, origins.value, loaded.fields, 'run')
  setup.value.fields = next.fields
  origins.value = next.origins
  source.value = loaded.source
  flash(labels.loaded)
}

function useSample(): void {
  if (!setup.value || !defaults.value) return
  const next = refill(setup.value.fields, origins.value, defaults.value.fields, 'sample')
  setup.value.fields = next.fields
  origins.value = next.origins
  source.value = null
}

function reset(field: 'base_prompt' | 'vs_instruction' | 'user_template'): void {
  if (setup.value && defaults.value) setup.value[field] = defaults.value[field]
}

function useWording(name: 'kalliope' | 'paper'): void {
  if (setup.value && instructions.value[name]) setup.value.vs_instruction = instructions.value[name]
}

function resetAll(): void {
  if (!setup.value || !defaults.value || !window.confirm(labels.resetAllConfirm)) return
  setup.value = { ...freshSetup(), fields: setup.value.fields }
}

// -------------------------------------------------------------------- runs

async function follow(run: ExperimentRun<VSSetup>): Promise<void> {
  current.value = run
  if (isFinished(run)) return
  running.value = true
  try {
    const done = await waitForRun<VSSetup>(run.id, () => leaving)
    if (done) current.value = done
  } catch (exc) {
    fail(exc)
  } finally {
    running.value = false
    void refreshStats()
  }
}

async function run(): Promise<void> {
  if (!setup.value || !canRun.value) return
  error.value = ''
  try {
    const started = await startRun<VSSetup>(KEY, setup.value, source.value)
    // Every new run opens blind.
    setRevealed(started.id, false)
    await follow(started)
  } catch (exc) {
    fail(exc)
  }
}

async function collectDraft(draft: VSDraft): Promise<void> {
  if (!current.value) return
  try {
    const saved = await collect<VSSetup>(current.value.id, draft.item, null, collectFolder.value)
    current.value = {
      ...current.value,
      items: current.value.items.map((item) =>
        item.item === draft.item ? { ...item, output_id: saved.id } : item,
      ),
    }
    outputs.value = await listOutputs<VSSetup>(KEY)
    void refreshStats()
  } catch (exc) {
    fail(exc)
  }
}

function adopt(output: ExperimentOutput<unknown>): void {
  const adopted = output.setup as VSSetup
  setup.value = JSON.parse(JSON.stringify(adopted)) as VSSetup
  origins.value = originsFor(adopted.fields, [...fieldSpecs.value.keys()], 'edited')
  const meta = output.meta.source
  source.value = meta && typeof meta === 'object' ? (meta as ExperimentSourceMeta) : null
  flash(labels.adopted)
}

async function reloadOutputs(): Promise<void> {
  try {
    outputs.value = await listOutputs<VSSetup>(KEY)
  } catch (exc) {
    fail(exc)
  }
}

async function remove(output: ExperimentOutput<unknown>): Promise<void> {
  try {
    await deleteOutput(output.id)
    outputs.value = outputs.value.filter((item) => item.id !== output.id)
    if (current.value?.id === output.run_id) {
      current.value = {
        ...current.value,
        items: current.value.items.map((item) =>
          item.output_id === output.id ? { ...item, output_id: null } : item,
        ),
      }
    }
    void refreshStats()
  } catch (exc) {
    fail(exc)
  }
}

async function refreshStats(): Promise<void> {
  try {
    const next = await getExperiment<VSSetup>(KEY)
    if (detail.value) detail.value = { ...detail.value, stats: next.stats }
  } catch {
    /* stats are informational */
  }
}

// -------------------------------------------------------------------- load

watch(
  [setup, origins, source],
  () => {
    if (setup.value) {
      writeSetupDraft({ setup: setup.value, origins: origins.value, source: source.value })
    }
  },
  { deep: true },
)

onMounted(async () => {
  try {
    const [loadedDetail, loadedCatalogue, loadedOutputs, history] = await Promise.all([
      getExperiment<VSSetup>(KEY),
      loadModelCatalogue(),
      listOutputs<VSSetup>(KEY),
      listRuns<VSSetup>(KEY, 1),
    ])
    detail.value = loadedDetail
    catalogue.value = loadedCatalogue
    outputs.value = loadedOutputs
    const draft = readSetupDraft()
    if (draft) {
      setup.value = draft.setup
      origins.value = (draft.origins ?? {}) as Record<string, FieldOrigin>
      source.value = draft.source ?? null
    } else {
      setup.value = freshSetup()
      origins.value = originsFor(setup.value.fields, Object.keys(setup.value.fields), 'sample')
    }
    if (history[0]) void follow(history[0])
  } catch (exc) {
    fail(exc)
  } finally {
    loading.value = false
  }
})

onUnmounted(() => {
  leaving = true
})

onBeforeRouteLeave(() => {
  if (running.value) return window.confirm(labels.leaveConfirm)
  return true
})
</script>

<template>
  <ExperimentFrame
    experiment-key="verbalized_sampling"
    :title="detail?.title ?? ''"
    :lead="detail?.summary"
    :spent-usd="detail?.stats.spent_usd"
    :run-count="detail?.stats.run_count"
  >
    <template #actions>
      <button class="btn btn--primary" :disabled="!canRun" @click="run">
        {{ running ? labels.running : labels.run }}
      </button>
    </template>

    <template #banner>
      <p v-if="error" class="banner banner--fail" role="alert">
        {{ error }}
        <button class="btn btn--ghost btn--sm" @click="error = ''">{{ t.common.close }}</button>
      </p>
      <p v-if="notice" class="banner" role="status">{{ notice }}</p>
    </template>

    <template #setup>
      <p v-if="loading" class="muted">{{ t.common.loading }}</p>
      <template v-else-if="setup">
        <div class="spread">
          <p class="eyebrow">{{ labels.setup }}</p>
          <button class="btn btn--ghost btn--sm" @click="resetAll">{{ labels.resetAll }}</button>
        </div>
        <div class="tabs" role="tablist">
          <button
            v-for="name in TABS"
            :key="name"
            class="tab"
            role="tab"
            :aria-selected="tab === name"
            @click="tab = name"
          >
            {{ name === 'vs' ? vsLabels.tab : labels.tabs[name] }}
          </button>
        </div>
        <div class="summary">
          <span v-for="chip in summaryChips" :key="chip" class="badge badge--idle">{{ chip }}</span>
        </div>

        <!-- prompt -->
        <div v-if="tab === 'prompt'" class="stack">
          <div class="param">
            <div class="param__head">
              <span class="param__label">{{ vsLabels.basePrompt }}</span>
              <code class="param__key">base_prompt</code>
              <span v-if="changed('base_prompt')" class="badge badge--mark">{{ labels.changed }}</span>
              <span class="grow" />
              <button
                v-if="changed('base_prompt')"
                class="btn btn--ghost btn--sm"
                @click="reset('base_prompt')"
              >
                {{ labels.toDefault }}
              </button>
            </div>
            <p class="hint">{{ vsLabels.basePromptHint }}</p>
            <textarea
              v-model="setup.base_prompt"
              class="textarea mono"
              rows="16"
              spellcheck="false"
              :aria-label="vsLabels.basePrompt"
            />
          </div>
          <div class="param">
            <div class="param__head">
              <span class="param__label">{{ vsLabels.instruction }}</span>
              <code class="param__key">vs_instruction</code>
              <span v-if="changed('vs_instruction')" class="badge badge--mark">{{ labels.changed }}</span>
              <span class="grow" />
              <button
                v-if="changed('vs_instruction')"
                class="btn btn--ghost btn--sm"
                @click="reset('vs_instruction')"
              >
                {{ labels.toDefault }}
              </button>
            </div>
            <p class="hint">{{ vsLabels.instructionHint }}</p>
            <div class="row wrap wording">
              <span class="meta">{{ vsLabels.wording }}</span>
              <span class="seg" role="group">
                <button :aria-pressed="wording === 'kalliope'" @click="useWording('kalliope')">
                  {{ vsLabels.wordingKalliope }}
                </button>
                <button :aria-pressed="wording === 'paper'" @click="useWording('paper')">
                  {{ vsLabels.wordingPaper }}
                </button>
              </span>
            </div>
            <textarea
              v-model="setup.vs_instruction"
              class="textarea mono"
              rows="12"
              spellcheck="false"
              :aria-label="vsLabels.instruction"
            />
          </div>
          <div class="param">
            <div class="param__head">
              <span class="param__label">{{ labels.userTemplate }}</span>
              <code class="param__key">user_template</code>
              <span v-if="changed('user_template')" class="badge badge--mark">{{ labels.changed }}</span>
              <span class="grow" />
              <button
                v-if="changed('user_template')"
                class="btn btn--ghost btn--sm"
                @click="reset('user_template')"
              >
                {{ labels.toDefault }}
              </button>
            </div>
            <p class="hint">{{ labels.userTemplateHint }}</p>
            <textarea
              v-model="setup.user_template"
              class="textarea mono"
              rows="12"
              spellcheck="false"
              :aria-label="labels.userTemplate"
            />
            <div class="chips">
              <span
                v-for="chip in chips"
                :key="chip.name"
                class="badge"
                :class="{
                  'badge--pass': chip.state === 'ok',
                  'badge--idle': chip.state === 'empty',
                  'badge--fail': chip.state === 'missing',
                }"
              >
                {{ chip.name }}
                <template v-if="chip.state === 'ok'"> ✓</template>
                <template v-else-if="chip.state === 'empty'"> · {{ labels.placeholderEmpty }}</template>
                <template v-else> · {{ labels.placeholderMissing }}</template>
              </span>
            </div>
          </div>
        </div>

        <!-- input -->
        <div v-else-if="tab === 'input'" class="stack">
          <SourceLoader
            experiment-key="verbalized_sampling"
            :source="source"
            @loaded="applySource"
            @sample="useSample"
            @error="fail"
          />
          <p class="hint">{{ vsLabels.inputLead }}</p>
          <FieldCard
            v-for="name in fieldNames"
            :key="name"
            :name="name"
            :model-value="setup.fields[name]"
            :origin="origins[name] ?? 'custom'"
            :label="fieldSpecs.get(name)?.label"
            :hint="fieldSpecs.get(name)?.hint"
            :multiline="fieldSpecs.get(name)?.multiline ?? true"
            :removable="!fieldSpecs.has(name)"
            :start-open="name === 'passages' || !fieldSpecs.has(name)"
            @update:model-value="setField(name, $event)"
            @remove="removeField(name)"
          />
          <div>
            <button class="btn btn--sm" @click="addField">{{ labels.addField }}</button>
            <p class="hint">{{ labels.addFieldHint }}</p>
          </div>
        </div>

        <!-- model -->
        <div v-else-if="tab === 'model'" class="stack">
          <ModelSettings
            v-if="catalogue"
            :catalogue="catalogue"
            :model-value="setup.settings"
            @update:model-value="updateSettings"
          />
          <label class="toggle">
            <input type="checkbox" checked disabled />
            <span><b>{{ labels.structured }}</b><br />{{ vsLabels.structuredLocked }}</span>
          </label>
          <label class="toggle">
            <input v-model="setup.settings.cache_system" type="checkbox" />
            <span><b>{{ labels.cacheSystem }}</b><br />{{ labels.cacheSystemHint }}</span>
          </label>
        </div>

        <!-- vs -->
        <div v-else class="stack">
          <div class="param">
            <div class="param__head">
              <label class="param__label" for="vs-k">{{ vsLabels.k }}</label>
              <code class="param__key">k</code>
              <span v-if="!kValid" class="badge badge--fail">2–8</span>
            </div>
            <p class="hint">{{ vsLabels.kHint }}</p>
            <input
              id="vs-k"
              v-model.number="setup.k"
              class="input num"
              type="number"
              min="2"
              max="8"
              step="1"
            />
          </div>
          <div class="param">
            <div class="param__head">
              <span class="param__label">{{ vsLabels.variant }}</span>
              <code class="param__key">variant</code>
              <span class="badge badge--idle">{{ vsLabels.variantStandard }}</span>
            </div>
            <p class="hint">{{ vsLabels.variantHint }}</p>
          </div>
          <div class="param">
            <div class="param__head">
              <span class="param__label">{{ vsLabels.comparison }}</span>
              <code class="param__key">baseline</code>
              <span class="badge badge--idle">{{ vsLabels.comparisonValue }}</span>
            </div>
            <p class="hint">{{ vsLabels.comparisonHint }}</p>
          </div>
          <details class="param">
            <summary class="param__head">
              <span class="param__label">{{ vsLabels.schema }}</span>
              <code class="param__key">vs_schema</code>
            </summary>
            <p class="hint">{{ vsLabels.schemaHint }}</p>
            <pre class="json">{{ vsSchema }}</pre>
          </details>
        </div>
      </template>
    </template>

    <template #outputs>
      <div class="spread">
        <p class="eyebrow">{{ labels.current }}</p>
        <span class="meta">{{ labels.currentHint }}</span>
      </div>
      <CollectFolder v-model="collectFolder" :experiment-key="KEY" />
      <p v-if="running" class="running"><span class="spinner" />{{ vsLabels.runningHint }}</p>
      <p v-else-if="!current" class="muted small">{{ labels.noOutput }}</p>
      <template v-if="current && isFinished(current)">
        <div v-if="current.status === 'failed'" class="failed">
          <span class="badge badge--fail">{{ labels.failed }}</span>
          <p class="small">{{ current.error }}</p>
          <div>
            <button class="btn btn--sm" :disabled="!canRun" @click="run">{{ vsLabels.runAgain }}</button>
          </div>
        </div>
        <OutputCard
          v-else
          :title="new Date(current.created_at).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })"
          :payload="output"
          :badges="runBadgeList"
          :facts="costFacts(output?.cost, output?.vs.drafts?.length ?? 0)"
          :warnings="runWarnings"
          :calls="current.calls"
          current
        >
          <template #head>
            <span class="badge badge--pass">{{ labels.done }}</span>
            <button class="btn btn--sm" @click="setRevealed(current.id, blind)">
              {{ blind ? vsLabels.reveal : vsLabels.hide }}
            </button>
          </template>
          <template #body="{ mode }">
            <p v-if="blind" class="meta blindnote">{{ vsLabels.blindNote }}</p>
            <p v-if="output?.vs.error" class="failline">
              <span class="badge badge--fail">{{ labels.failed }}</span>
              {{ vsLabels.vsFailed }}: {{ output.vs.error }}
            </p>
            <p v-if="output?.baseline.error" class="failline">
              <span class="badge badge--fail">{{ labels.failed }}</span>
              {{ vsLabels.baselineFailed }}: {{ output.baseline.error }}
            </p>
            <div v-if="mode === 'text'" class="drafts">
              <OutputCard
                v-for="(draft, position) in currentDrafts"
                :key="draft.item"
                :title="draftTitle(draft, position)"
                :payload="{ segments: draft.segments }"
                :badges="draftBadges(draft, output?.word_budget ?? null)"
                collapsible
              >
                <template #head>
                  <template v-if="!blind">
                    <span v-if="draft.source === 'vs'" class="badge badge--mark">{{ vsLabels.withVs }}</span>
                    <span v-else class="badge badge--idle">{{ vsLabels.withoutVs }}</span>
                    <span v-if="draft.source === 'vs'" class="badge badge--idle num">
                      p {{ probabilityText(draft.probability) }}
                    </span>
                    <span
                      v-if="draft.source === 'vs' && draft.probability !== null"
                      class="pbar"
                      aria-hidden="true"
                    >
                      <span :style="{ width: `${Math.round(draft.probability * 100)}%` }" />
                    </span>
                  </template>
                  <span
                    v-if="citationBadge(draft)"
                    class="badge"
                    :class="citationBadge(draft)!.ok ? 'badge--pass' : 'badge--warn'"
                  >
                    {{ citationBadge(draft)!.text }}
                  </span>
                </template>
                <template #foot>
                  <span class="grow" />
                  <button
                    v-if="!savedItems.get(draft.item)"
                    class="btn btn--mark btn--sm"
                    @click="collectDraft(draft)"
                  >
                    {{ labels.collect }}
                  </button>
                  <span v-else class="badge badge--pass">{{ labels.inCollection }} ✓</span>
                </template>
              </OutputCard>
            </div>
            <pre v-else class="json">{{ blind ? blindJson : JSON.stringify(output, null, 2) }}</pre>
          </template>
        </OutputCard>
      </template>

      <CollectionList
        :experiment-key="KEY"
        @changed="reloadOutputs"
        @error="error = $event"
        :outputs="outputs"
        :payload-of="collectedPayload"
        :title-of="collectedTitle"
        :badges-of="collectedBadges"
        @adopt="adopt"
        @delete="remove"
      >
        <template v-if="outputs.length" #meta>
          <p class="row wrap tally">
            <span class="meta num">
              {{ vsLabels.tally }}: {{ counts.vs }} {{ vsLabels.tallyVs }} · {{ counts.baseline }}
              {{ vsLabels.tallyBaseline }}<template v-if="counts.blind">
                · {{ counts.blind }} {{ vsLabels.tallyBlind }}</template>
            </span>
            <button v-if="counts.blind" class="btn btn--ghost btn--sm" @click="revealAll">
              {{ vsLabels.revealAll }}
            </button>
          </p>
        </template>
      </CollectionList>
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

.summary,
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: var(--s3);
}

.chips {
  margin: var(--s2) 0 0;
}

.param {
  padding: var(--s3) var(--s4);
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
}

.param__head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

summary.param__head {
  cursor: pointer;
}

.param__label {
  font-weight: 600;
  font-size: var(--t-sm);
}

.param__key {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.hint {
  margin: 4px 0 var(--s2);
  font-size: var(--t-sm);
  color: var(--ink-3);
}

.mono {
  font-family: var(--mono);
  font-size: 0.78rem;
  line-height: 1.55;
}

.wording {
  margin-bottom: var(--s2);
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
  padding: 3px 10px;
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.06em;
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

.small {
  font-size: var(--t-sm);
}

.toggle {
  display: flex;
  gap: var(--s2);
  align-items: flex-start;
  font-size: var(--t-sm);
  color: var(--ink-2);
}

.toggle input {
  margin-top: 3px;
  accent-color: var(--mark);
}

.toggle b {
  color: var(--ink);
  font-weight: 560;
}

.running {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: var(--s3) 0;
  font-size: var(--t-sm);
  color: var(--ink-2);
}

.spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--rule-strong);
  border-top-color: var(--mark);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.failed {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  padding: var(--s3) var(--s4);
  border: 1px solid var(--fail);
  border-radius: var(--r-lg);
  background: var(--fail-soft);
  color: var(--fail);
}

.failline {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  margin: 0 0 var(--s3);
  font-size: var(--t-sm);
  color: var(--fail);
}

.blindnote {
  margin: 0 0 var(--s3);
}

.drafts {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

.pbar {
  display: inline-block;
  width: 48px;
  height: 4px;
  border-radius: 2px;
  background: var(--sunk);
  overflow: hidden;
}

.pbar span {
  display: block;
  height: 100%;
  background: var(--mark);
}

.tally {
  margin: 0;
}

.json {
  margin: 0;
  max-height: 420px;
  overflow: auto;
  padding: var(--s3);
  background: var(--sunk);
  border-radius: var(--r-md);
  font-family: var(--mono);
  font-size: 0.74rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
