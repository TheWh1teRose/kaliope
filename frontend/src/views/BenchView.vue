<script setup lang="ts">
/**
 * The node testing bench.
 *
 * A value bag in the middle, a short chain on the left, the last outcome on
 * the right. Load a real document or a previous run, change any input or
 * parameter, run one node or the chain. Nothing here becomes a production run.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'

import { ApiError } from '@/api/client'
import type {
  BenchValueOut,
  FeedbackOut,
  FlowNodeIn,
  FlowValidation,
  NodeCheck,
  NodeSpecOut,
  Note,
} from '@/api/types'
import BenchValueCard from '@/components/BenchValueCard.vue'
import ModalDialog from '@/components/ModalDialog.vue'
import NodeDocPanel from '@/components/NodeDocPanel.vue'
import NotesEditor from '@/components/NotesEditor.vue'
import ParamField from '@/components/ParamField.vue'
import StatusPill from '@/components/StatusPill.vue'
import { t } from '@/i18n'
import { useBenchStore } from '@/stores/bench'
import { useCatalogueStore } from '@/stores/catalogue'
import { useDocumentsStore } from '@/stores/documents'
import { usePipelinesStore } from '@/stores/pipelines'
import { useRunsStore } from '@/stores/runs'

const STORAGE = 'kalliope-bench'

const route = useRoute()
const bench = useBenchStore()
const documents = useDocumentsStore()
const runs = useRunsStore()
const pipelines = usePipelinesStore()
const catalogue = useCatalogueStore()

const nodes = ref<FlowNodeIn[]>([])
const selected = ref<string | null>(null)
const bag = ref<Record<string, Slot>>({})
const openKey = ref<string | null>(null)
const documentId = ref<string | null>(null)
const formatId = ref<string>('')
const force = ref(false)
const adding = ref(false)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const validation = ref<FlowValidation | null>(null)
const picker = ref<'document' | 'run' | null>(null)
const inspected = ref<string | null>(null)
const openIo = ref<Set<string>>(new Set())
const feedback = ref<FeedbackOut | null>(null)
const draftNotes = ref<Note[]>([])
const notesOpen = ref(false)

interface Slot {
  value: BenchValueOut
  text: string
  original: string
  dirty: boolean
  jsonError: string
}

const specs = computed(() => new Map((pipelines.nodes?.nodes ?? []).map((n) => [n.name, n])))
const models = computed(() => pipelines.nodes?.models ?? bench.catalogue?.models ?? [])
const available = computed(() =>
  (pipelines.nodes?.nodes ?? []).filter((n) => !nodes.value.some((e) => e.node === n.name)),
)
const checks = computed(
  () => new Map((validation.value?.nodes ?? []).map((c: NodeCheck) => [c.node, c])),
)
const selectedSpec = computed(() => (selected.value ? specs.value.get(selected.value) : undefined))
const selectedEntry = computed(() => nodes.value.find((e) => e.node === selected.value))
const paused = computed(() => bench.current?.status === 'paused')
const live = computed(
  () => bench.current?.status === 'queued' || bench.current?.status === 'running',
)
const bagKeys = computed(() =>
  Object.keys(bag.value).sort((a, b) => {
    const order = neededKeys.value
    const ai = order.indexOf(a)
    const bi = order.indexOf(b)
    if (ai === -1 && bi === -1) return a.localeCompare(b)
    if (ai === -1) return 1
    if (bi === -1) return -1
    return ai - bi
  }),
)
const neededKeys = computed(() => {
  const keys: string[] = []
  for (const entry of nodes.value) {
    const spec = specs.value.get(entry.node)
    for (const key of spec?.consumes ?? []) {
      if (!keys.includes(key)) keys.push(key)
    }
  }
  return keys
})
const parsedDocuments = computed(() => documents.items.filter((d) => d.parse_status === 'parsed'))
const finishedRuns = computed(() =>
  runs.items.filter((r) => ['completed', 'in_review', 'reviewed', 'failed'].includes(r.status)),
)

function spec(name: string): NodeSpecOut | undefined {
  return specs.value.get(name)
}

function emptySlot(key: string): Slot {
  const meta = bench.catalogue?.values.find((v) => v.key === key)
  const template = meta?.template ?? null
  const text = template == null ? '' : JSON.stringify(template, null, 2)
  return {
    value: {
      key,
      model: meta?.model ?? 'unknown',
      produced_by: null,
      artifact_hash: null,
      summary: {},
      preview: text || null,
      truncated: false,
      available: template != null,
      source: template != null ? 'format' : 'empty',
      source_id: null,
      payload: template,
    },
    text,
    original: text,
    dirty: false,
    jsonError: '',
  }
}

function applyLoaded(values: BenchValueOut[], markOpen?: string): void {
  for (const item of values) {
    const text =
      item.payload != null
        ? JSON.stringify(item.payload, null, 2)
        : (item.preview ?? '')
    bag.value[item.key] = {
      value: item,
      text,
      original: text,
      dirty: false,
      jsonError: '',
    }
  }
  if (markOpen && bag.value[markOpen]) openKey.value = markOpen
}

function persist(): void {
  localStorage.setItem(
    STORAGE,
    JSON.stringify({
      nodes: nodes.value,
      documentId: documentId.value,
      formatId: formatId.value,
      selected: selected.value,
      force: force.value,
    }),
  )
}

function restore(): void {
  try {
    const raw = localStorage.getItem(STORAGE)
    if (!raw) return
    const saved = JSON.parse(raw) as {
      nodes?: FlowNodeIn[]
      documentId?: string | null
      formatId?: string
      selected?: string | null
      force?: boolean
    }
    if (saved.nodes?.length) nodes.value = saved.nodes
    if (saved.documentId) documentId.value = saved.documentId
    if (saved.formatId) formatId.value = saved.formatId
    if (saved.selected) selected.value = saved.selected
    if (saved.force) force.value = saved.force
  } catch {
    /* ignore a broken draft */
  }
}

async function refreshValidation(): Promise<void> {
  try {
    validation.value = await bench.validate(
      nodes.value,
      Object.keys(bag.value).filter((key) => bag.value[key]?.value.available || bag.value[key]?.dirty),
    )
  } catch {
    validation.value = null
  }
}

function addNode(name: string): void {
  nodes.value = [...nodes.value, { node: name, config: {} }]
  selected.value = name
  adding.value = false
  for (const key of spec(name)?.consumes ?? []) {
    if (!bag.value[key]) bag.value[key] = emptySlot(key)
  }
}

function removeNode(index: number): void {
  const name = nodes.value[index]?.node
  nodes.value = nodes.value.filter((_, i) => i !== index)
  if (selected.value === name) selected.value = nodes.value[0]?.node ?? null
}

function move(index: number, delta: number): void {
  const next = index + delta
  if (next < 0 || next >= nodes.value.length) return
  const copy = [...nodes.value]
  const [item] = copy.splice(index, 1)
  copy.splice(next, 0, item)
  nodes.value = copy
}

function setParam(key: string, value: unknown): void {
  const entry = selectedEntry.value
  if (!entry) return
  const config = { ...entry.config }
  if (value === null || value === undefined || value === '') delete config[key]
  else config[key] = value
  entry.config = config
}

function onEdit(key: string, text: string): void {
  const slot = bag.value[key]
  if (!slot) return
  slot.text = text
  slot.dirty = text !== slot.original
  try {
    const parsed = text.trim() === '' ? null : JSON.parse(text)
    slot.jsonError = ''
    slot.value = {
      ...slot.value,
      payload: parsed,
      available: parsed !== null,
      source: slot.dirty ? 'edited' : slot.value.source,
    }
  } catch (exc) {
    slot.jsonError = exc instanceof Error ? exc.message : t.bench.jsonInvalid
  }
}

function applyEdit(key: string): void {
  const slot = bag.value[key]
  if (!slot || slot.jsonError) return
  slot.original = slot.text
  slot.dirty = true
  slot.value = { ...slot.value, source: 'edited', artifact_hash: null }
}

function revert(key: string): void {
  const slot = bag.value[key]
  if (!slot) return
  slot.text = slot.original
  slot.dirty = false
  slot.jsonError = ''
  try {
    slot.value = {
      ...slot.value,
      payload: slot.original.trim() ? JSON.parse(slot.original) : null,
      source: slot.value.source === 'edited' ? 'empty' : slot.value.source,
    }
  } catch {
    slot.value = { ...slot.value, payload: null }
  }
}

async function loadFull(key: string): Promise<void> {
  const slot = bag.value[key]
  const hash = slot?.value.artifact_hash
  if (!hash) return
  busy.value = true
  try {
    const payload = await bench.artifact(hash)
    const text = JSON.stringify(payload, null, 2)
    slot.value = { ...slot.value, payload, preview: text, truncated: false }
    slot.text = text
    slot.original = text
    slot.dirty = false
    slot.jsonError = ''
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

function collectSeeds(): Record<string, { payload?: unknown; artifact_hash?: string }> {
  const seeds: Record<string, { payload?: unknown; artifact_hash?: string }> = {}
  for (const [key, slot] of Object.entries(bag.value)) {
    if (slot.jsonError) throw new Error(t.bench.jsonInvalid)
    if (slot.dirty || slot.value.payload != null) {
      let payload = slot.value.payload
      if (slot.dirty && !slot.jsonError) {
        payload = slot.text.trim() === '' ? null : JSON.parse(slot.text)
      }
      if (payload !== null && payload !== undefined) seeds[key] = { payload }
      continue
    }
    if (slot.value.artifact_hash) seeds[key] = { artifact_hash: slot.value.artifact_hash }
  }
  return seeds
}

async function run(opts: { only?: string; fromNode?: string } = {}): Promise<void> {
  if (!nodes.value.length) return
  error.value = ''
  busy.value = true
  try {
    const created = await bench.create({
      document_id: documentId.value,
      nodes: nodes.value,
      seeds: collectSeeds(),
      force: force.value,
      only: opts.only,
      from_node: opts.fromNode,
    })
    inspected.value = opts.only ?? created.nodes[created.nodes.length - 1] ?? null
    await followBenchRun(created.id)
  } catch (exc) {
    busy.value = false
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  }
}

async function followBenchRun(id: string): Promise<void> {
  const settle = async () => {
    const done = await bench.get(id)
    mergeResult(done)
    await bench.list()
    if (done.status === 'paused') await loadFeedback(id)
    if (done.status !== 'queued' && done.status !== 'running') busy.value = false
  }
  bench.watch(id, () => void settle())
  await settle()
}

function mergeResult(result: { values: BenchValueOut[]; status: string; error: string | null }): void {
  applyLoaded(result.values)
  if (result.status === 'failed') error.value = result.error || t.errors.generic
  if (result.status !== 'paused') feedback.value = null
}

async function loadFeedback(id: string): Promise<void> {
  try {
    feedback.value = await bench.feedback(id)
    draftNotes.value = [...feedback.value.draft_notes]
  } catch (exc) {
    feedback.value = null
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  }
}

async function saveBenchNotes(): Promise<void> {
  if (!bench.current) return
  busy.value = true
  error.value = ''
  try {
    feedback.value = await bench.saveFeedback(bench.current.id, draftNotes.value)
    draftNotes.value = [...feedback.value.draft_notes]
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

async function submitBenchNotes(): Promise<void> {
  if (!bench.current) return
  busy.value = true
  error.value = ''
  try {
    const submitted = await bench.submitFeedback(bench.current.id, draftNotes.value)
    feedback.value = null
    notesOpen.value = false
    await followBenchRun(submitted.run_id)
  } catch (exc) {
    busy.value = false
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  }
}

async function onDocumentChange(id: string): Promise<void> {
  if (!id) {
    documentId.value = null
    return
  }
  await loadDocument(id)
}

async function loadDocument(id: string): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const loaded = await bench.load({
      document_id: id,
      format_id: formatId.value || undefined,
    })
    documentId.value = loaded.document_id ?? id
    applyLoaded(loaded.values, 'parsed')
    picker.value = null
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

async function loadRun(id: string): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const loaded = await bench.load({ run_id: id })
    documentId.value = loaded.document_id
    applyLoaded(loaded.values)
    picker.value = null
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

async function loadBenchRun(id: string): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const loaded = await bench.load({ bench_run_id: id, include_payload: false })
    documentId.value = loaded.document_id
    if (loaded.nodes?.length) {
      nodes.value = loaded.nodes
      selected.value = loaded.nodes[0]?.node ?? null
    }
    applyLoaded(loaded.values)
    const detail = await bench.get(id)
    inspected.value = detail.nodes[detail.nodes.length - 1] ?? null
    if (detail.status === 'paused') await loadFeedback(id)
    else feedback.value = null
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

async function onFormatChange(): Promise<void> {
  if (!formatId.value) return
  const loaded = await bench.load({ format_id: formatId.value })
  applyLoaded(loaded.values)
}

function sourceOf(entry: FlowNodeIn, key: string): string {
  const wire = checks.value.get(entry.node)?.wiring.find((w) => w.key === key)
  if (!wire) return ''
  if (!wire.satisfied) return t.pipelines.unsatisfied
  if (wire.source) return `${t.pipelines.fromNode} ${wire.source}`
  return t.bench.fromBag
}

function when(value: string): string {
  return new Date(value).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}

function duration(ms: number | null): string {
  if (ms == null) return t.common.none
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

function recordOf(name: string) {
  return bench.current?.records.find((r) => r.node_name === name)
}

function outputOf(name: string): BenchValueOut | undefined {
  const node = spec(name)
  if (!node) return undefined
  return bench.current?.values.find((v) => v.key === node.produces)
}

function tracesOf(name: string) {
  return (bench.current?.llm_traces ?? []).filter((trace) => trace.node_name === name)
}

function roleLabel(role: string): string {
  if (role === 'user') return t.bench.user
  if (role === 'assistant') return t.bench.assistant
  return role
}

function ioKey(node: string, index: number, part: 'in' | 'reply'): string {
  return `${node}:${index}:${part}`
}

function ioOpen(key: string): boolean {
  return openIo.value.has(key)
}

function toggleIo(key: string): void {
  const next = new Set(openIo.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  openIo.value = next
}

watch([nodes, documentId, formatId, selected, force], persist, { deep: true })
watch([nodes, bag], () => void refreshValidation(), { deep: true })
watch(paused, (isPaused) => {
  if (!isPaused) notesOpen.value = false
})

onMounted(async () => {
  loading.value = true
  restore()
  try {
    await Promise.all([
      pipelines.loadNodes(),
      catalogue.load(),
      documents.load(),
      runs.load(),
      bench.loadCatalogue(),
      bench.list(),
    ])
    if (!formatId.value) formatId.value = catalogue.formats[0]?.id ?? ''
    if (!nodes.value.length) {
      nodes.value = [{ node: 'content_budget', config: {} }]
      selected.value = 'content_budget'
    }
    const qDoc = typeof route.query.document === 'string' ? route.query.document : null
    const qRun = typeof route.query.run === 'string' ? route.query.run : null
    if (qDoc) await loadDocument(qDoc)
    else if (qRun) await loadRun(qRun)
    else if (documentId.value) await loadDocument(documentId.value)
    else if (formatId.value) await onFormatChange()
    for (const key of neededKeys.value) {
      if (!bag.value[key]) bag.value[key] = emptySlot(key)
    }
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    loading.value = false
  }
})

onUnmounted(() => bench.stopWatching())

onBeforeRouteLeave(() => {
  if (live.value) return window.confirm(t.bench.leaveConfirm)
  return true
})
</script>

<template>
  <div class="page">
    <header class="head">
      <div class="grow">
        <p class="eyebrow">{{ t.app.name }}</p>
        <h1 class="h-page">{{ t.bench.title }}</h1>
        <p class="muted lead">{{ t.bench.lead }}</p>
      </div>
      <div class="row wrap tools">
        <label class="force">
          <input v-model="force" type="checkbox" />
          {{ t.bench.force }}
        </label>
        <button
          v-if="paused"
          class="btn btn--mark"
          @click="notesOpen = true"
        >
          {{ t.bench.openNotes }}
        </button>
        <button
          class="btn btn--primary"
          :disabled="busy || !nodes.length || !validation?.valid"
          @click="run()"
        >
          {{ busy && live ? t.bench.running : t.bench.runAll }}
        </button>
      </div>
    </header>

    <div class="body">
    <p v-if="error" class="banner">{{ error }}</p>
    <p v-if="loading" class="muted pad">{{ t.common.loading }}</p>

    <div v-if="!loading" class="work">
      <!-- ----------------------------------------------------------- chain -->
      <aside class="col col--chain scroll">
        <div class="spread">
          <p class="eyebrow">{{ t.bench.chain }}</p>
          <button class="btn btn--sm" @click="adding = true">{{ t.bench.addNode }}</button>
        </div>
        <p class="hint">{{ t.bench.chainLead }}</p>

        <p v-if="!nodes.length" class="muted small">{{ t.bench.noNodes }}</p>

        <ol class="chain">
          <li
            v-for="(entry, index) in nodes"
            :key="entry.node"
            class="step"
            :class="{
              'step--on': selected === entry.node,
              'step--bad': (checks.get(entry.node)?.missing.length ?? 0) > 0,
            }"
          >
            <button class="step__pick" @click="selected = entry.node">
              <span class="step__n num">{{ index + 1 }}</span>
              <span class="grow">
                <span class="step__title">{{ spec(entry.node)?.title ?? entry.node }}</span>
                <code class="step__id">{{ entry.node }}</code>
              </span>
              <StatusPill
                v-if="recordOf(entry.node)"
                :status="recordOf(entry.node)!.status"
                kind="node"
              />
            </button>
            <ul class="wires">
              <li
                v-for="key in spec(entry.node)?.consumes ?? []"
                :key="key"
                class="wire"
                :class="{ 'wire--bad': checks.get(entry.node)?.missing.includes(key) }"
              >
                <code>{{ key }}</code>
                <span class="meta">{{ sourceOf(entry, key) }}</span>
              </li>
              <li class="wire wire--out">
                <span class="meta">{{ t.pipelines.produces }}</span>
                <code>{{ spec(entry.node)?.produces }}</code>
              </li>
            </ul>
            <div class="step__tools">
              <button class="btn btn--ghost btn--sm" :disabled="index === 0" @click="move(index, -1)">
                ↑
              </button>
              <button
                class="btn btn--ghost btn--sm"
                :disabled="index === nodes.length - 1"
                @click="move(index, 1)"
              >
                ↓
              </button>
              <button class="btn btn--ghost btn--sm" :disabled="busy" @click="run({ only: entry.node })">
                {{ t.bench.runNode }}
              </button>
              <button
                class="btn btn--ghost btn--sm"
                :disabled="busy || index === 0"
                @click="run({ fromNode: entry.node })"
              >
                {{ t.bench.runFrom }}
              </button>
              <button class="btn btn--ghost btn--sm btn--danger" @click="removeNode(index)">✕</button>
            </div>
          </li>
        </ol>

        <section v-if="selectedSpec && selectedEntry" class="params">
          <p class="eyebrow">{{ t.pipelines.parameters }}</p>
          <p v-if="!selectedSpec.params.length" class="muted small">{{ t.pipelines.noParameters }}</p>
          <ParamField
            v-for="param in selectedSpec.params"
            :key="param.key"
            :param="param"
            :value="selectedEntry.config[param.key]"
            :models="models"
            @update="setParam(param.key, $event)"
          />
          <NodeDocPanel :node="selectedSpec" compact />
        </section>
        <p v-else class="muted small pad">{{ t.bench.selectNode }}</p>
      </aside>

      <!-- ----------------------------------------------------------- values -->
      <section class="col col--bag scroll">
        <div class="spread">
          <p class="eyebrow">{{ t.bench.values }}</p>
          <div class="row wrap">
            <button class="btn btn--sm" @click="picker = 'document'">{{ t.bench.loadDocument }}</button>
            <button class="btn btn--sm" @click="picker = 'run'">{{ t.bench.loadRun }}</button>
          </div>
        </div>
        <p class="hint">{{ t.bench.valuesLead }}</p>

        <div class="sources">
          <div class="field">
            <label for="bench-doc">{{ t.run.document }}</label>
            <select
              id="bench-doc"
              class="select"
              :value="documentId ?? ''"
              @change="onDocumentChange(($event.target as HTMLSelectElement).value)"
            >
              <option value="">{{ t.common.none }}</option>
              <option v-for="doc in parsedDocuments" :key="doc.id" :value="doc.id">
                {{ doc.title || doc.filename }}
              </option>
            </select>
          </div>
          <div class="field">
            <label for="bench-fmt">{{ t.bench.loadFormat }}</label>
            <select id="bench-fmt" v-model="formatId" class="select" @change="onFormatChange">
              <option v-for="fmt in catalogue.formats" :key="fmt.id" :value="fmt.id">
                {{ fmt.name }}
              </option>
            </select>
          </div>
        </div>

        <p v-if="!bagKeys.length" class="muted pad">{{ t.bench.emptyHint }}</p>
        <div class="stack">
          <BenchValueCard
            v-for="key in bagKeys"
            :key="key"
            :value="bag[key].value"
            :text="bag[key].text"
            :dirty="bag[key].dirty"
            :json-error="bag[key].jsonError"
            :open="openKey === key"
            @toggle="openKey = openKey === key ? null : key"
            @edit="onEdit(key, $event)"
            @apply="applyEdit(key)"
            @revert="revert(key)"
            @load-full="loadFull(key)"
          />
        </div>
      </section>

      <!-- ---------------------------------------------------------- result -->
      <aside class="col col--out scroll">
        <p class="eyebrow">{{ t.bench.result }}</p>
        <p class="hint">{{ t.bench.resultLead }}</p>

        <div v-if="bench.current" class="result">
          <div class="spread">
            <StatusPill :status="bench.current.status" />
            <span class="badge badge--idle num">${{ bench.current.total_cost_usd.toFixed(4) }}</span>
          </div>
          <p v-if="bench.current.error" class="trace">{{ bench.current.error }}</p>

          <ol class="records">
            <li
              v-for="record in bench.current.records"
              :key="record.node_name"
              class="record"
              :class="{ 'record--on': inspected === record.node_name }"
            >
              <button class="record__head" @click="inspected = record.node_name">
                <span>{{ spec(record.node_name)?.title ?? record.node_name }}</span>
                <StatusPill :status="record.status" kind="node" />
                <span class="meta num">{{ duration(record.wall_ms) }}</span>
              </button>
              <dl v-if="inspected === record.node_name" class="facts">
                <div v-if="record.model_id">
                  <dt class="meta">{{ t.run.model }}</dt>
                  <dd class="mono truncate">{{ record.model_id }}</dd>
                </div>
                <div v-if="record.tokens_in + record.tokens_out > 0">
                  <dt class="meta">{{ t.run.tokens }}</dt>
                  <dd class="num">
                    {{ record.tokens_in.toLocaleString('de-DE') }} /
                    {{ record.tokens_out.toLocaleString('de-DE') }}
                  </dd>
                </div>
                <div v-if="record.cost_usd > 0">
                  <dt class="meta">{{ t.common.cost }}</dt>
                  <dd class="num">${{ record.cost_usd.toFixed(4) }}</dd>
                </div>
                <div>
                  <dt class="meta">{{ t.run.cacheHit }}</dt>
                  <dd>{{ record.cache_hit ? t.run.cached : t.run.computed }}</dd>
                </div>
              </dl>
              <div v-if="inspected === record.node_name" class="io">
                <p v-if="record.error" class="trace">{{ record.error }}</p>

                <template v-if="tracesOf(record.node_name).length">
                  <article
                    v-for="(trace, index) in tracesOf(record.node_name)"
                    :key="index"
                    class="call"
                  >
                    <button
                      class="fold"
                      :aria-expanded="ioOpen(ioKey(record.node_name, index, 'in'))"
                      @click="toggleIo(ioKey(record.node_name, index, 'in'))"
                    >
                      <span class="eyebrow">
                        {{ t.bench.llmInput }}
                        <template v-if="tracesOf(record.node_name).length > 1">
                          · {{ t.bench.call }} {{ index + 1 }}
                        </template>
                      </span>
                      <span class="meta truncate">{{ trace.model }}</span>
                      <span class="grow" />
                      <span class="chev" aria-hidden="true">{{
                        ioOpen(ioKey(record.node_name, index, 'in')) ? '−' : '+'
                      }}</span>
                    </button>
                    <div v-if="ioOpen(ioKey(record.node_name, index, 'in'))">
                      <p class="meta call__meta">
                        <template v-if="trace.tokens_in">
                          {{ trace.tokens_in.toLocaleString('de-DE') }} /
                          {{ trace.tokens_out.toLocaleString('de-DE') }}
                        </template>
                      </p>
                      <p v-for="warning in trace.warnings" :key="warning" class="muted small pad-sm">
                        {{ warning }}
                      </p>
                      <template v-if="trace.system">
                        <p class="call__role">{{ t.bench.system }}</p>
                        <pre class="json scroll">{{ trace.system }}</pre>
                      </template>
                      <template v-for="(message, mi) in trace.messages" :key="mi">
                        <p class="call__role">{{ roleLabel(message.role) }}</p>
                        <pre class="json scroll">{{ message.content }}</pre>
                      </template>
                    </div>

                    <button
                      class="fold"
                      :aria-expanded="ioOpen(ioKey(record.node_name, index, 'reply'))"
                      @click="toggleIo(ioKey(record.node_name, index, 'reply'))"
                    >
                      <span class="eyebrow">{{ t.bench.llmReply }}</span>
                      <span class="grow" />
                      <span class="chev" aria-hidden="true">{{
                        ioOpen(ioKey(record.node_name, index, 'reply')) ? '−' : '+'
                      }}</span>
                    </button>
                    <pre
                      v-if="ioOpen(ioKey(record.node_name, index, 'reply'))"
                      class="json scroll"
                    >{{ trace.response_text }}</pre>
                  </article>
                </template>
                <p v-else-if="record.cache_hit" class="muted small pad-sm">
                  {{ t.bench.cachedNoLlm }}
                </p>
                <p v-else-if="record.status === 'ok'" class="muted small pad-sm">
                  {{ t.bench.noLlm }}
                </p>

                <template v-if="outputOf(record.node_name)?.preview">
                  <p class="eyebrow">{{ t.bench.nodeOutput }}</p>
                  <pre class="json scroll">{{ outputOf(record.node_name)?.preview }}</pre>
                </template>
              </div>
            </li>
          </ol>
        </div>
        <p v-else class="muted small">{{ t.bench.noResult }}</p>

        <section v-if="bench.progress.length" class="log">
          <p class="eyebrow">{{ t.bench.progress }}</p>
          <ul>
            <li v-for="(line, i) in bench.progress" :key="i" class="meta">
              <span v-if="line.node">{{ line.node }}</span>
              {{ line.message || line.type }}
            </li>
          </ul>
        </section>

        <section class="history">
          <p class="eyebrow">{{ t.bench.history }}</p>
          <p class="hint">{{ t.bench.historyLead }}</p>
          <p v-if="!bench.history.length" class="muted small">{{ t.bench.noHistory }}</p>
          <ul v-else class="hist">
            <li v-for="item in bench.history" :key="item.id">
              <button class="hist__item" @click="loadBenchRun(item.id)">
                <span class="hist__nodes">{{ item.nodes.join(' → ') }}</span>
                <span class="meta">{{ when(item.created_at) }}</span>
                <StatusPill :status="item.status" />
              </button>
            </li>
          </ul>
        </section>
      </aside>
    </div>
    </div>

    <Teleport to="body">
      <div v-if="notesOpen && paused" class="notes-overlay">
        <button class="notes-overlay__close btn btn--ghost btn--sm" @click="notesOpen = false">
          {{ t.bench.closeNotes }}
        </button>
        <NotesEditor
          v-if="feedback"
          :feedback="feedback"
          :notes="draftNotes"
          :busy="busy"
          @update:notes="draftNotes = $event"
          @save="saveBenchNotes"
          @submit="submitBenchNotes"
        />
        <p v-else-if="error" class="notice notice--fail">{{ error }}</p>
        <p v-else class="muted pad">{{ t.common.loading }}</p>
      </div>
    </Teleport>

    <ModalDialog :open="adding" :title="t.bench.addNode" @close="adding = false">
      <p class="hint">{{ t.bench.addNodeLead }}</p>
      <ul class="pick">
        <li v-for="node in available" :key="node.name">
          <button class="pick__item" @click="addNode(node.name)">
            <span class="item__name">{{ node.title }}</span>
            <code class="item__id">{{ node.name }}</code>
            <span class="muted small">{{ node.doc.summary }}</span>
          </button>
        </li>
      </ul>
    </ModalDialog>

    <ModalDialog
      :open="picker === 'document'"
      :title="t.bench.pickDocument"
      @close="picker = null"
    >
      <p class="hint">{{ t.bench.pickDocumentLead }}</p>
      <ul class="pick">
        <li v-for="doc in parsedDocuments" :key="doc.id">
          <button class="pick__item" @click="loadDocument(doc.id)">
            <span class="item__name">{{ doc.title || doc.filename }}</span>
            <span class="meta">{{ doc.page_count }} {{ t.documents.pages }} · {{ doc.language }}</span>
          </button>
        </li>
      </ul>
    </ModalDialog>

    <ModalDialog :open="picker === 'run'" :title="t.bench.pickRun" @close="picker = null">
      <p class="hint">{{ t.bench.pickRunLead }}</p>
      <ul class="pick">
        <li v-for="run in finishedRuns" :key="run.id">
          <button class="pick__item" @click="loadRun(run.id)">
            <span class="item__name">{{ run.document_title || run.document_id }}</span>
            <span class="meta">{{ run.flow_id }} · {{ when(run.created_at) }}</span>
          </button>
        </li>
      </ul>
    </ModalDialog>
  </div>
</template>

<style scoped>
.page {
  display: grid;
  grid-template-rows: auto 1fr;
  height: 100%;
  min-height: 0;
}

.body {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: auto;
}

.head {
  display: flex;
  align-items: flex-end;
  gap: var(--s5);
  padding: var(--s5) var(--s6) var(--s4);
  border-bottom: 1px solid var(--rule);
}

.lead {
  max-width: 62ch;
  margin-top: var(--s2);
  font-size: var(--t-sm);
}

.tools {
  padding-bottom: 2px;
}

.force {
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
  font-size: var(--t-sm);
  color: var(--ink-2);
}

.banner {
  margin: var(--s3) var(--s6) 0;
  padding: var(--s3) var(--s4);
  background: var(--fail-soft);
  color: var(--fail);
  border-radius: var(--r-md);
  font-size: var(--t-sm);
}

.notes-overlay {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
  flex-direction: column;
  background: var(--press);
}

.notes-overlay__close {
  position: absolute;
  top: var(--s3);
  right: var(--s4);
  z-index: 3;
}

.pad {
  padding: var(--s5) var(--s6);
}

.work {
  display: grid;
  grid-template-columns: minmax(280px, 320px) minmax(0, 1fr) minmax(300px, 36%);
  min-height: 0;
  flex: 1;
}

.col {
  padding: var(--s4);
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  min-width: 0;
  min-height: 0;
}

.col--chain,
.col--out {
  background: var(--chrome);
}

.col--chain {
  border-right: 1px solid var(--rule);
}

.col--out {
  border-left: 1px solid var(--rule);
}

.hint {
  font-size: var(--t-xs);
  color: var(--ink-3);
  margin: 0;
}

.small {
  font-size: var(--t-xs);
}

.chain {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s3);
}

.step {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  overflow: hidden;
}

.step--on {
  border-color: var(--ink-4);
  box-shadow: var(--shadow-sm);
}

.step--bad {
  border-color: var(--fail);
}

.step__pick {
  display: flex;
  align-items: center;
  gap: var(--s2);
  width: 100%;
  padding: var(--s3);
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.step__n {
  width: 1.4em;
  color: var(--ink-3);
}

.step__title {
  display: block;
  font-weight: 600;
  font-size: var(--t-sm);
}

.step__id {
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.wires {
  list-style: none;
  margin: 0;
  padding: 0 var(--s3) var(--s2);
  display: grid;
  gap: 2px;
}

.wire {
  display: flex;
  justify-content: space-between;
  gap: var(--s2);
  font-size: var(--t-xs);
}

.wire--bad code {
  color: var(--fail);
}

.wire--out {
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px dashed var(--rule);
}

.step__tools {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  padding: 0 var(--s2) var(--s2);
}

.params {
  display: grid;
  gap: var(--s3);
  padding-top: var(--s3);
  border-top: 1px solid var(--rule);
}

.sources {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--s3);
}

.result {
  display: grid;
  gap: var(--s3);
  min-width: 0;
}

.trace {
  margin: 0;
  padding: var(--s3);
  background: var(--fail-soft);
  color: var(--fail);
  border-radius: var(--r-sm);
  font-family: var(--mono);
  font-size: var(--t-xs);
  white-space: pre-wrap;
  max-height: 160px;
  overflow: auto;
}

.records {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s2);
}

.record {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  min-width: 0;
  overflow: hidden;
}

.record--on {
  border-color: var(--ink-4);
}

.record__head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  width: 100%;
  padding: var(--s3);
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.facts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--s2);
  margin: 0;
  padding: 0 var(--s3) var(--s3);
}

.facts dd {
  margin: 0;
  font-size: var(--t-sm);
}

.io {
  display: grid;
  gap: var(--s3);
  min-width: 0;
}

.call {
  display: grid;
  gap: var(--s2);
  min-width: 0;
}

.fold {
  display: flex;
  align-items: center;
  gap: var(--s2);
  width: 100%;
  padding: var(--s2) var(--s3);
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.fold .eyebrow {
  margin: 0;
}

.chev {
  color: var(--ink-4);
  width: 1em;
  text-align: center;
}

.call__meta {
  padding: 0 var(--s3);
}

.call__role {
  margin: 0;
  padding: var(--s2) var(--s3) 0;
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--ink-3);
}

.pad-sm {
  padding: 0 var(--s3) var(--s3);
}

.json {
  margin: 0;
  padding: var(--s3);
  background: var(--sunk);
  font-family: var(--mono);
  font-size: var(--t-xs);
  line-height: 1.45;
  max-height: 280px;
  max-width: 100%;
  min-width: 0;
  border-top: 1px solid var(--rule);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.log ul,
.hist {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 4px;
}

.hist__item {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: var(--s2);
  align-items: center;
  width: 100%;
  padding: var(--s2) var(--s3);
  border: 1px solid var(--rule);
  border-radius: var(--r-sm);
  background: var(--card);
  cursor: pointer;
  text-align: left;
}

.hist__nodes {
  font-family: var(--mono);
  font-size: var(--t-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pick {
  list-style: none;
  margin: var(--s3) 0 0;
  padding: 0;
  display: grid;
  gap: var(--s2);
  max-height: 420px;
  overflow: auto;
}

.pick__item {
  display: grid;
  gap: 2px;
  width: 100%;
  padding: var(--s3);
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  background: var(--card);
  cursor: pointer;
  text-align: left;
}

.pick__item:hover {
  border-color: var(--ink-4);
}

.item__name {
  font-weight: 600;
}

.item__id {
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.mono {
  font-family: var(--mono);
}

@media (max-width: 1100px) {
  .work {
    grid-template-columns: 1fr;
    grid-template-rows: auto auto auto;
  }

  .col--chain,
  .col--out {
    border: 0;
    border-top: 1px solid var(--rule);
  }
}
</style>
