<script setup lang="ts">
/**
 * Experiment "Direkter Stil": the script step's prompt, editable end to end.
 *
 * Left: the setup (system prompt and user template, the named input fields,
 * model settings). Right: the current answer and the collected ones. Starts
 * from exactly what production sends; nothing here creates a run, a note or
 * a pipeline node.
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
  PromptSetup,
} from '@/api/types'
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
import { runBadges, runFacts } from '@/experiments/outputMeta'
import {
  FIELD_NAME,
  missingPlaceholders,
  originsFor,
  placeholders,
  readDraft,
  refill,
  writeDraft,
} from '@/experiments/promptSetup'
import { t } from '@/i18n'

const KEY = 'direct_style'
const labels = t.experiments

type Tab = 'prompt' | 'input' | 'model'

const detail = ref<ExperimentDetail | null>(null)
const catalogue = ref<ModelCatalogue | null>(null)
const setup = ref<PromptSetup | null>(null)
const origins = ref<Record<string, FieldOrigin>>({})
const source = ref<ExperimentSourceMeta | null>(null)
const current = ref<ExperimentRun | null>(null)
const outputs = ref<ExperimentOutput[]>([])
const tab = ref<Tab>('prompt')
const loading = ref(true)
const error = ref('')
const notice = ref('')
const running = ref(false)
const label = ref('')
let leaving = false

const defaults = computed(() => detail.value?.defaults ?? null)
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
const missing = computed(() => (setup.value ? missingPlaceholders(setup.value) : []))
const model = computed(() =>
  catalogue.value?.models.find((m) => m.id === setup.value?.settings.model),
)
const settingsValid = computed(() =>
  model.value && setup.value ? settingsState(model.value, setup.value.settings).valid : false,
)
const canRun = computed(
  () => Boolean(setup.value) && !running.value && !missing.value.length && settingsValid.value,
)
const systemChanged = computed(
  () => !!setup.value && !!defaults.value && setup.value.system_prompt !== defaults.value.system_prompt,
)
const templateChanged = computed(
  () =>
    !!setup.value && !!defaults.value && setup.value.user_template !== defaults.value.user_template,
)
const summaryChips = computed(() => {
  const s = setup.value?.settings
  if (!s) return []
  return [
    s.model,
    s.temperature !== null ? `T ${s.temperature}` : 'T —',
    `max ${s.max_tokens.toLocaleString('de-DE')}`,
    s.structured ? 'JSON an' : 'JSON aus',
    source.value?.beat_title
      ? `${labels.sourceBeat} ${source.value.beat_position}/${source.value.beat_total}`
      : labels.sample,
  ]
})
const currentItem = computed(() => current.value?.items.find((item) => item.item === 'main'))

function fail(exc: unknown): void {
  error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
}

function flash(message: string): void {
  notice.value = message
  window.setTimeout(() => {
    if (notice.value === message) notice.value = ''
  }, 3500)
}

function freshSetup(): PromptSetup {
  return JSON.parse(JSON.stringify(defaults.value)) as PromptSetup
}

function updateSettings(next: SharedSettings): void {
  if (!setup.value) return
  setup.value.settings = { ...setup.value.settings, ...next }
}

// ------------------------------------------------------------------ fields

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

// ------------------------------------------------------------- load from run

function applySource(loaded: ExperimentSource): void {
  if (!setup.value) return
  const next = refill(setup.value.fields, origins.value, loaded.fields, 'run')
  setup.value.fields = next.fields
  origins.value = next.origins
  source.value = loaded.source
  tab.value = 'input'
  flash(labels.loaded)
}

function useSample(): void {
  if (!setup.value || !defaults.value) return
  const next = refill(setup.value.fields, origins.value, defaults.value.fields, 'sample')
  setup.value.fields = next.fields
  origins.value = next.origins
  source.value = null
}

// ---------------------------------------------------------------- resets

function resetSystem(): void {
  if (setup.value && defaults.value) setup.value.system_prompt = defaults.value.system_prompt
}

function resetTemplate(): void {
  if (setup.value && defaults.value) setup.value.user_template = defaults.value.user_template
}

function resetAll(): void {
  if (!setup.value || !defaults.value || !window.confirm(labels.resetAllConfirm)) return
  const fresh = freshSetup()
  setup.value = { ...fresh, fields: setup.value.fields }
}

// ------------------------------------------------------------------- runs

async function follow(run: ExperimentRun): Promise<void> {
  current.value = run
  label.value = ''
  if (isFinished(run)) return
  running.value = true
  try {
    const done = await waitForRun<PromptSetup>(run.id, () => leaving)
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
    const started = await startRun(KEY, setup.value, source.value)
    await follow(started)
  } catch (exc) {
    fail(exc)
  }
}

async function collectCurrent(): Promise<void> {
  if (!current.value) return
  try {
    await collect(current.value.id, 'main', label.value.trim() || null)
    current.value = {
      ...current.value,
      items: current.value.items.map((item) =>
        item.item === 'main' ? { ...item, output_id: 'saved' } : item,
      ),
    }
    outputs.value = await listOutputs<PromptSetup>(KEY)
    void refreshStats()
  } catch (exc) {
    fail(exc)
  }
}

function adopt(output: ExperimentOutput<unknown>): void {
  const adopted = output.setup as PromptSetup
  setup.value = JSON.parse(JSON.stringify(adopted)) as PromptSetup
  origins.value = originsFor(adopted.fields, [...fieldSpecs.value.keys()], 'edited')
  const meta = output.meta.source
  source.value = meta && typeof meta === 'object' ? (meta as ExperimentSourceMeta) : null
  flash(labels.adopted)
}

async function remove(output: ExperimentOutput<unknown>): Promise<void> {
  try {
    await deleteOutput(output.id)
    outputs.value = outputs.value.filter((item) => item.id !== output.id)
    if (current.value?.id === output.run_id) {
      current.value = {
        ...current.value,
        items: current.value.items.map((item) => ({ ...item, output_id: null })),
      }
    }
    void refreshStats()
  } catch (exc) {
    fail(exc)
  }
}

async function refreshStats(): Promise<void> {
  try {
    const next = await getExperiment<PromptSetup>(KEY)
    if (detail.value) detail.value = { ...detail.value, stats: next.stats }
  } catch {
    /* stats are informational */
  }
}

// ------------------------------------------------------------------- load

watch(
  [setup, origins, source],
  () => {
    if (setup.value) {
      writeDraft(KEY, { setup: setup.value, origins: origins.value, source: source.value as never })
    }
  },
  { deep: true },
)

onMounted(async () => {
  try {
    const [loadedDetail, loadedCatalogue, loadedOutputs, history] = await Promise.all([
      getExperiment<PromptSetup>(KEY),
      loadModelCatalogue(),
      listOutputs<PromptSetup>(KEY),
      listRuns<PromptSetup>(KEY, 1),
    ])
    detail.value = loadedDetail
    catalogue.value = loadedCatalogue
    outputs.value = loadedOutputs
    const draft = readDraft(KEY)
    if (draft) {
      setup.value = draft.setup
      origins.value = draft.origins ?? {}
      source.value = (draft.source as ExperimentSourceMeta | null) ?? null
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
    experiment-key="direct_style"
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
            v-for="name in ['prompt', 'input', 'model'] as const"
            :key="name"
            class="tab"
            role="tab"
            :aria-selected="tab === name"
            @click="tab = name"
          >
            {{ labels.tabs[name] }}
          </button>
        </div>
        <div class="summary">
          <span v-for="chip in summaryChips" :key="chip" class="badge badge--idle">{{ chip }}</span>
        </div>

        <!-- prompt -->
        <div v-if="tab === 'prompt'" class="stack">
          <div class="param">
            <div class="param__head">
              <span class="param__label">{{ labels.systemPrompt }}</span>
              <code class="param__key">system_prompt</code>
              <span v-if="systemChanged" class="badge badge--mark">{{ labels.changed }}</span>
              <span class="grow" />
              <button v-if="systemChanged" class="btn btn--ghost btn--sm" @click="resetSystem">
                {{ labels.toDefault }}
              </button>
            </div>
            <p class="hint">{{ labels.systemPromptHint }}</p>
            <textarea
              v-model="setup.system_prompt"
              class="textarea mono"
              rows="18"
              spellcheck="false"
              :aria-label="labels.systemPrompt"
            />
          </div>
          <div class="param">
            <div class="param__head">
              <span class="param__label">{{ labels.userTemplate }}</span>
              <code class="param__key">user_template</code>
              <span v-if="templateChanged" class="badge badge--mark">{{ labels.changed }}</span>
              <span class="grow" />
              <button v-if="templateChanged" class="btn btn--ghost btn--sm" @click="resetTemplate">
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
            experiment-key="direct_style"
            :source="source"
            @loaded="applySource"
            @sample="useSample"
            @error="fail"
          />
          <p class="hint">{{ labels.fieldsLead }}</p>
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
          <details v-if="source?.reference?.length" class="reference">
            <summary class="eyebrow">{{ labels.reference }}</summary>
            <OutputCard
              :title="source.beat_title ?? ''"
              :payload="{ segments: source.reference }"
            />
          </details>
        </div>

        <!-- model -->
        <div v-else class="stack">
          <ModelSettings
            v-if="catalogue"
            :catalogue="catalogue"
            :model-value="setup.settings"
            @update:model-value="updateSettings"
          />
          <label class="toggle">
            <input v-model="setup.settings.structured" type="checkbox" />
            <span><b>{{ labels.structured }}</b><br />{{ labels.structuredHint }}</span>
          </label>
          <label class="toggle">
            <input v-model="setup.settings.cache_system" type="checkbox" />
            <span><b>{{ labels.cacheSystem }}</b><br />{{ labels.cacheSystemHint }}</span>
          </label>
        </div>
      </template>
    </template>

    <template #outputs>
      <div class="spread">
        <p class="eyebrow">{{ labels.current }}</p>
        <span class="meta">{{ labels.currentHint }}</span>
      </div>
      <p v-if="running" class="running"><span class="spinner" />{{ labels.runningHint }}</p>
      <p v-else-if="!current" class="muted small">{{ labels.noOutput }}</p>
      <template v-if="current && isFinished(current)">
        <div v-if="current.status === 'failed'" class="failed">
          <span class="badge badge--fail">{{ labels.failed }}</span>
          <p class="small">{{ current.error }}</p>
        </div>
        <OutputCard
          v-else
          :title="new Date(current.created_at).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })"
          :payload="current.output?.payload ?? null"
          :text="(current.output?.text as string | undefined) ?? null"
          :badges="runBadges(current)"
          :facts="runFacts(current)"
          :warnings="current.warnings"
          :calls="current.calls"
          current
        >
          <template #head>
            <span class="badge badge--pass">{{ labels.done }}</span>
          </template>
          <template #foot>
            <template v-if="currentItem && !currentItem.output_id">
              <input
                v-model="label"
                class="input label"
                :placeholder="labels.label"
                :aria-label="labels.label"
                maxlength="200"
              />
              <button class="btn btn--mark" @click="collectCurrent">{{ labels.collect }}</button>
            </template>
            <span v-else-if="currentItem" class="badge badge--pass">{{ labels.inCollection }} ✓</span>
          </template>
        </OutputCard>
      </template>

      <CollectionList :outputs="outputs" @adopt="adopt" @delete="remove" />
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

.reference summary {
  cursor: pointer;
  margin-bottom: var(--s2);
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

.label {
  flex: 1;
  min-width: 180px;
  padding: 6px 10px;
  font-size: var(--t-sm);
}
</style>
