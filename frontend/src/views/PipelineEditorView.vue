<script setup lang="ts">
/**
 * The pipeline editor.
 *
 * A flow is an ordered list of nodes, and the runner wires them by value name:
 * a node consumes whatever an earlier node published under the name it asks for.
 * So the editor is a list, not a canvas — and rather than let someone draw an
 * edge that cannot exist, every change is validated against that rule and the
 * resulting connections are shown on each node.
 *
 * Nothing is saved implicitly. Saving appends a revision with a note; the
 * previous one stays readable and restorable in the history panel.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, onBeforeRouteLeave } from 'vue-router'

import { ApiError } from '@/api/client'
import type {
  FlowNodeIn,
  FlowValidation,
  NodeCheck,
  NodeSpecOut,
  PipelineDetail,
  PipelineDraft,
  RevisionOut,
} from '@/api/types'
import ModalDialog from '@/components/ModalDialog.vue'
import NodeDocPanel from '@/components/NodeDocPanel.vue'
import ParamField from '@/components/ParamField.vue'
import RevisionList from '@/components/RevisionList.vue'
import { t } from '@/i18n'
import { useCatalogueStore } from '@/stores/catalogue'
import { usePipelinesStore } from '@/stores/pipelines'

const props = defineProps<{ id: string }>()

const store = usePipelinesStore()
const catalogue = useCatalogueStore()

const detail = ref<PipelineDetail | null>(null)
const draft = ref<PipelineDraft>({ name: '', version: '1.0', description: '', nodes: [], gates: [] })
const saved = ref('')
const validation = ref<FlowValidation | null>(null)
const revisions = ref<RevisionOut[]>([])

const loading = ref(true)
const busy = ref(false)
const error = ref('')
const expanded = ref<string | null>(null)
const adding = ref(false)
const saving = ref(false)
const note = ref('')
const restoreTarget = ref<number | null>(null)

const specs = computed(() => new Map((store.nodes?.nodes ?? []).map((n) => [n.name, n])))
const models = computed(() => store.nodes?.models ?? [])
const dirty = computed(() => JSON.stringify(draft.value) !== saved.value)

const checks = computed(
  () => new Map((validation.value?.nodes ?? []).map((c: NodeCheck) => [c.node, c])),
)

const available = computed(() =>
  (store.nodes?.nodes ?? []).filter((n) => !draft.value.nodes.some((e) => e.node === n.name)),
)

function spec(name: string): NodeSpecOut | undefined {
  return specs.value.get(name)
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([store.loadNodes(), catalogue.load()])
    const loaded = await store.get(props.id)
    apply(loaded)
    revisions.value = await store.versions(props.id)
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    loading.value = false
  }
}

function apply(loaded: PipelineDetail): void {
  detail.value = loaded
  draft.value = JSON.parse(JSON.stringify(loaded.definition)) as PipelineDraft
  saved.value = JSON.stringify(draft.value)
  validation.value = loaded.validation
}

let timer: ReturnType<typeof setTimeout> | undefined

watch(
  draft,
  () => {
    clearTimeout(timer)
    timer = setTimeout(async () => {
      try {
        validation.value = await store.validate(draft.value)
      } catch {
        /* the editor keeps working; the save call reports the real problem */
      }
    }, 250)
  },
  { deep: true },
)

// ------------------------------------------------------------------ nodes

function addNode(name: string): void {
  draft.value.nodes.push({ node: name, config: {} })
  expanded.value = name
  adding.value = false
}

function removeNode(index: number): void {
  draft.value.nodes.splice(index, 1)
}

function move(index: number, by: number): void {
  const target = index + by
  if (target < 0 || target >= draft.value.nodes.length) return
  const nodes = draft.value.nodes
  ;[nodes[index], nodes[target]] = [nodes[target], nodes[index]]
}

function setParam(entry: FlowNodeIn, key: string, value: unknown): void {
  if (value === null || value === undefined || value === '') delete entry.config[key]
  else entry.config[key] = value
}

function toggleGate(id: string): void {
  const index = draft.value.gates.indexOf(id)
  if (index >= 0) draft.value.gates.splice(index, 1)
  else draft.value.gates.push(id)
  draft.value.gates.sort()
}

function extraConfig(entry: FlowNodeIn): string[] {
  const declared = new Set((spec(entry.node)?.params ?? []).map((p) => p.key))
  return Object.keys(entry.config).filter((key) => !declared.has(key))
}

// ----------------------------------------------------------------- saving

async function save(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const result = await store.save(props.id, { ...draft.value, note: note.value.trim() || null })
    apply(result)
    revisions.value = await store.versions(props.id)
    saving.value = false
    note.value = ''
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

async function restore(): Promise<void> {
  const revision = restoreTarget.value
  if (revision === null) return
  busy.value = true
  try {
    apply(await store.restore(props.id, revision))
    revisions.value = await store.versions(props.id)
    restoreTarget.value = null
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

function discard(): void {
  if (detail.value) apply(detail.value)
}

onBeforeRouteLeave(() => (dirty.value ? window.confirm(t.pipelines.leaveConfirm) : true))

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="head">
      <div class="grow">
        <RouterLink :to="{ name: 'pipelines' }" class="eyebrow back">
          ← {{ t.pipelines.title }}
        </RouterLink>
        <h1 class="h-page">{{ draft.name || props.id }}</h1>
        <p class="meta">
          <code>{{ props.id }}</code>
          · {{ t.pipelines.revision }} {{ detail?.revision ?? 0 }}
          ·
          {{ detail?.origin === 'file' ? t.pipelines.originFile : t.pipelines.originUser }}
          <template v-if="detail?.run_count">
            · {{ detail.run_count }} {{ t.pipelines.runs }}
          </template>
        </p>
      </div>
      <div class="row wrap">
        <span v-if="dirty" class="badge badge--warn">{{ t.common.unsaved }}</span>
        <button v-if="dirty" class="btn btn--sm" @click="discard">{{ t.common.discard }}</button>
        <button class="btn btn--primary" :disabled="!dirty || busy" @click="saving = true">
          {{ t.pipelines.save }}
        </button>
      </div>
    </header>

    <p v-if="error" class="banner banner--fail">{{ error }}</p>
    <p v-if="loading" class="muted pad">{{ t.common.loading }}</p>

    <div v-else class="layout">
      <div class="main">
        <!-- ------------------------------------------------------ identity -->
        <section class="card block">
          <div class="grid2">
            <div class="field">
              <label for="pl-name">{{ t.pipelines.name }}</label>
              <input id="pl-name" v-model="draft.name" class="input" />
            </div>
            <div class="field">
              <label for="pl-version">{{ t.pipelines.version }}</label>
              <input id="pl-version" v-model="draft.version" class="input" />
              <p class="hint">{{ t.pipelines.versionHint }}</p>
            </div>
          </div>
          <div class="field">
            <label for="pl-desc">{{ t.pipelines.description }}</label>
            <textarea id="pl-desc" v-model="draft.description" class="textarea desc" rows="2" />
          </div>
        </section>

        <!-- ---------------------------------------------------- validation -->
        <section
          v-if="validation && (validation.errors.length || validation.warnings.length)"
          class="card block"
        >
          <template v-if="validation.errors.length">
            <p class="eyebrow eyebrow--fail">{{ t.pipelines.validationErrors }}</p>
            <ul class="issues">
              <li v-for="(message, i) in validation.errors" :key="i" class="issue issue--fail">
                {{ message }}
              </li>
            </ul>
          </template>
          <template v-if="validation.warnings.length">
            <p class="eyebrow">{{ t.pipelines.validationWarnings }}</p>
            <ul class="issues">
              <li v-for="(message, i) in validation.warnings" :key="i" class="issue issue--warn">
                {{ message }}
              </li>
            </ul>
          </template>
        </section>
        <p v-else-if="validation?.valid" class="banner banner--pass">
          {{ t.pipelines.validationOk }}
        </p>

        <!-- --------------------------------------------------------- seeds -->
        <section class="card block seeds">
          <p class="eyebrow">{{ t.pipelines.seeds }}</p>
          <p class="hint">{{ t.pipelines.seedsHint }}</p>
          <div class="chips">
            <span v-for="seed in store.nodes?.seeds ?? []" :key="seed.key" class="chip">
              <code>{{ seed.key }}</code>
              <span class="chip__type">{{ seed.model }}</span>
            </span>
          </div>
        </section>

        <!-- --------------------------------------------------------- nodes -->
        <section class="stack nodes">
          <p v-if="!draft.nodes.length" class="muted pad card">{{ t.pipelines.noNodes }}</p>

          <div
            v-for="(entry, index) in draft.nodes"
            :key="`${entry.node}-${index}`"
            class="card node"
            :class="{ 'node--bad': (checks.get(entry.node)?.missing.length ?? 0) > 0 }"
          >
            <div class="node__head">
              <span class="node__step num">{{ index + 1 }}</span>
              <button class="node__name grow" @click="expanded = expanded === entry.node ? null : entry.node">
                <span class="item__name">{{ spec(entry.node)?.title ?? entry.node }}</span>
                <code class="item__id">{{ entry.node }}</code>
                <span v-if="Object.keys(entry.config).length" class="badge badge--mark num">
                  {{ Object.keys(entry.config).length }}
                </span>
              </button>
              <div class="node__tools">
                <button
                  class="btn btn--ghost btn--sm"
                  :title="t.common.up"
                  :disabled="index === 0"
                  @click="move(index, -1)"
                >
                  ↑
                </button>
                <button
                  class="btn btn--ghost btn--sm"
                  :title="t.common.down"
                  :disabled="index === draft.nodes.length - 1"
                  @click="move(index, 1)"
                >
                  ↓
                </button>
                <button
                  class="btn btn--ghost btn--sm btn--danger"
                  :title="t.pipelines.removeNode"
                  @click="removeNode(index)"
                >
                  ✕
                </button>
                <button
                  class="btn btn--ghost btn--sm"
                  @click="expanded = expanded === entry.node ? null : entry.node"
                >
                  {{ expanded === entry.node ? '−' : '+' }}
                </button>
              </div>
            </div>

            <!-- wiring, always visible: it is the thing that can be wrong -->
            <ul class="wiring">
              <li
                v-for="wire in checks.get(entry.node)?.wiring ?? []"
                :key="wire.key"
                class="wire"
                :class="{ 'wire--bad': !wire.satisfied }"
              >
                <code>{{ wire.key }}</code>
                <span class="wire__from meta">
                  <template v-if="!wire.satisfied">{{ t.pipelines.unsatisfied }}</template>
                  <template v-else-if="wire.from_seed">{{ t.pipelines.fromSeed }}</template>
                  <template v-else>{{ t.pipelines.fromNode }} {{ wire.source }}</template>
                </span>
              </li>
              <li class="wire wire--out">
                <span class="meta">{{ t.pipelines.produces }}</span>
                <code>{{ spec(entry.node)?.produces }}</code>
              </li>
            </ul>

            <div v-if="expanded === entry.node && spec(entry.node)" class="node__body">
              <details class="docs">
                <summary>{{ t.pipelines.documentation }}</summary>
                <NodeDocPanel :node="spec(entry.node)!" />
              </details>

              <section class="params">
                <p class="eyebrow">{{ t.pipelines.parameters }}</p>
                <p class="hint">{{ t.pipelines.cacheHint }}</p>
                <p v-if="!spec(entry.node)!.params.length" class="muted small">
                  {{ t.pipelines.noParameters }}
                </p>
                <div class="params__grid">
                  <ParamField
                    v-for="param in spec(entry.node)!.params.filter((p) => !p.advanced)"
                    :key="param.key"
                    :param="param"
                    :value="entry.config[param.key]"
                    :models="models"
                    @update="(value) => setParam(entry, param.key, value)"
                  />
                </div>

                <details v-if="spec(entry.node)!.params.some((p) => p.advanced)" class="more">
                  <summary>{{ t.pipelines.advanced }}</summary>
                  <div class="params__grid">
                    <ParamField
                      v-for="param in spec(entry.node)!.params.filter((p) => p.advanced)"
                      :key="param.key"
                      :param="param"
                      :value="entry.config[param.key]"
                      :models="models"
                      @update="(value) => setParam(entry, param.key, value)"
                    />
                  </div>
                </details>

                <ul v-if="extraConfig(entry).length" class="issues">
                  <li v-for="key in extraConfig(entry)" :key="key" class="issue issue--warn">
                    {{ t.pipelines.unknownConfig }}: <code>{{ key }}</code>
                    <button class="btn btn--ghost btn--sm" @click="setParam(entry, key, null)">
                      {{ t.common.remove }}
                    </button>
                  </li>
                </ul>
              </section>
            </div>
          </div>

          <button class="btn add" @click="adding = true">+ {{ t.pipelines.addNode }}</button>
        </section>

        <!-- --------------------------------------------------------- gates -->
        <section class="card block">
          <p class="eyebrow">{{ t.pipelines.gates }}</p>
          <p class="hint">{{ t.pipelines.gatesHint }}</p>
          <div class="gates">
            <label v-for="gate in catalogue.gates" :key="gate.id" class="gate">
              <input
                type="checkbox"
                :checked="draft.gates.includes(gate.id)"
                @change="toggleGate(gate.id)"
              />
              <span class="gate__id num">{{ gate.id }}</span>
              <span class="gate__name">{{ gate.name }}</span>
              <span class="gate__rule meta">{{ gate.rule }}</span>
            </label>
          </div>
        </section>
      </div>

      <!-- ------------------------------------------------------- history -->
      <aside class="side card">
        <p class="eyebrow">{{ t.pipelines.history }}</p>
        <p class="hint">{{ t.pipelines.historyLead }}</p>
        <RevisionList
          :revisions="revisions"
          :current="detail?.revision ?? 0"
          :busy="busy"
          @restore="(revision) => (restoreTarget = revision)"
        />
      </aside>
    </div>

    <!-- --------------------------------------------------------- dialogs -->
    <ModalDialog
      :open="adding"
      :title="t.pipelines.addNode"
      :lead="t.pipelines.addNodeLead"
      wide
      @close="adding = false"
    >
      <ul class="picker">
        <li v-for="node in available" :key="node.name">
          <button class="pick" @click="addNode(node.name)">
            <span class="pick__title">
              <span class="item__name">{{ node.title }}</span>
              <code class="item__id">{{ node.name }}</code>
            </span>
            <span class="pick__summary">{{ node.doc.summary }}</span>
            <span class="pick__io meta">
              <span v-for="key in node.consumes" :key="key" class="num">{{ key }}</span>
              <span aria-hidden="true">→</span>
              <span class="num">{{ node.produces }}</span>
            </span>
          </button>
        </li>
        <li v-if="!available.length" class="muted small">{{ t.pipelines.alreadyUsed }}</li>
      </ul>
      <template #actions>
        <button class="btn" @click="adding = false">{{ t.common.cancel }}</button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="saving"
      :title="t.pipelines.saveTitle"
      :lead="t.pipelines.saveLead"
      @close="saving = false"
    >
      <div class="field">
        <label for="pl-note">{{ t.pipelines.note }}</label>
        <textarea id="pl-note" v-model="note" class="textarea" rows="3" data-autofocus />
      </div>
      <p v-if="validation && !validation.valid" class="banner banner--fail">
        {{ t.pipelines.validationErrors }}
      </p>
      <template #actions>
        <button class="btn" @click="saving = false">{{ t.common.cancel }}</button>
        <button class="btn btn--primary" :disabled="busy" @click="save">
          {{ busy ? t.common.saving : t.common.save }}
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="restoreTarget !== null"
      :title="t.pipelines.restoreTitle"
      :lead="t.pipelines.restoreLead"
      @close="restoreTarget = null"
    >
      <p class="prose num">r{{ restoreTarget }}</p>
      <template #actions>
        <button class="btn" @click="restoreTarget = null">{{ t.common.cancel }}</button>
        <button class="btn btn--primary" :disabled="busy" @click="restore">
          {{ t.pipelines.restore }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.page {
  padding: var(--s6);
  max-width: 1320px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--s4);
}

.head {
  display: flex;
  align-items: flex-end;
  gap: var(--s5);
  flex-wrap: wrap;
}

.back {
  display: inline-block;
  margin-bottom: 6px;
  text-decoration: none;
  color: var(--ink-3);
}

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: var(--s4);
  align-items: start;
}

.main {
  display: grid;
  gap: var(--s3);
  min-width: 0;
}

.block {
  padding: var(--s4);
  display: grid;
  gap: var(--s3);
}

.grid2 {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: var(--s4);
}

.desc {
  font-family: var(--sans);
  font-size: var(--t-sm);
  min-height: 0;
}

.hint {
  margin: 0;
  font-size: var(--t-xs);
  color: var(--ink-3);
  line-height: 1.5;
  max-width: 82ch;
}

.small {
  font-size: var(--t-sm);
}

/* ------------------------------------------------------------ validation */

.eyebrow--fail {
  color: var(--fail);
}

.issues {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s1);
}

.issue {
  padding: var(--s2) var(--s3);
  border-radius: var(--r-sm);
  font-size: var(--t-sm);
  line-height: 1.5;
}

.issue--fail {
  background: var(--fail-soft);
  color: var(--fail);
}

.issue--warn {
  background: var(--warn-soft);
  color: var(--warn);
  display: flex;
  align-items: center;
  gap: var(--s2);
}

.banner {
  margin: 0;
  padding: var(--s3) var(--s4);
  border-radius: var(--r-md);
  font-size: var(--t-sm);
}

.banner--fail {
  background: var(--fail-soft);
  color: var(--fail);
}

.banner--pass {
  background: var(--pass-soft);
  color: var(--pass);
}

/* ----------------------------------------------------------------- seeds */

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s2);
}

.chip {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 3px 9px;
  border-radius: var(--r-sm);
  background: var(--chrome);
  border: 1px dashed var(--rule-strong);
  font-family: var(--mono);
  font-size: var(--t-xs);
}

.chip__type {
  color: var(--ink-4);
}

/* ----------------------------------------------------------------- nodes */

.nodes {
  gap: var(--s2);
}

.node {
  padding: var(--s3) var(--s4) var(--s3);
  border-left: 3px solid var(--rule-strong);
}

.node--bad {
  border-left-color: var(--fail);
}

.node__head {
  display: flex;
  align-items: center;
  gap: var(--s3);
}

.node__step {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-4);
  width: 18px;
}

.node__name {
  display: flex;
  align-items: center;
  gap: var(--s2);
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
  flex-wrap: wrap;
}

.item__name {
  font-size: var(--t-md);
  font-weight: 600;
  letter-spacing: -0.01em;
}

.item__id {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-4);
}

.node__tools {
  display: flex;
  gap: 2px;
}

.wiring {
  list-style: none;
  margin: var(--s2) 0 0 30px;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--s1) var(--s3);
}

.wire {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  font-size: var(--t-xs);
}

.wire code {
  font-family: var(--mono);
  color: var(--mark-deep);
}

.wire--bad code {
  color: var(--fail);
}

.wire--bad .wire__from {
  color: var(--fail);
}

.wire--out code {
  color: var(--ink-2);
}

.wire__from {
  color: var(--ink-4);
}

.node__body {
  margin: var(--s4) 0 var(--s2) 30px;
  display: grid;
  gap: var(--s4);
}

.docs,
.more {
  border-top: 1px solid var(--rule);
  padding-top: var(--s3);
}

.docs > summary,
.more > summary {
  cursor: pointer;
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-3);
  margin-bottom: var(--s3);
}

.params {
  display: grid;
  gap: var(--s2);
}

.params__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--s2);
  align-items: start;
}

.add {
  justify-self: start;
  border-style: dashed;
}

/* ----------------------------------------------------------------- gates */

.gates {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--s1);
}

.gate {
  display: grid;
  grid-template-columns: auto auto 1fr;
  align-items: baseline;
  gap: var(--s2);
  padding: var(--s2) var(--s3);
  border-radius: var(--r-sm);
  background: var(--chrome);
  cursor: pointer;
}

.gate__id {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.gate__name {
  font-size: var(--t-sm);
}

.gate__rule {
  grid-column: 3;
  font-size: var(--t-xs);
  color: var(--ink-3);
  line-height: 1.4;
}

/* --------------------------------------------------------------- history */

.side {
  position: sticky;
  top: var(--s4);
  padding: var(--s4);
  display: grid;
  gap: var(--s2);
  max-height: calc(100vh - var(--s7));
  overflow: auto;
}

/* ---------------------------------------------------------------- picker */

.picker {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s2);
}

.pick {
  display: grid;
  gap: 4px;
  width: 100%;
  padding: var(--s3);
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  background: var(--card);
  cursor: pointer;
  text-align: left;
}

.pick:hover {
  border-color: var(--mark);
  background: var(--mark-soft);
}

.pick__title {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
}

.pick__summary {
  font-size: var(--t-sm);
  color: var(--ink-2);
  line-height: 1.5;
}

.pick__io {
  display: flex;
  gap: 6px;
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-4);
}

.pad {
  padding: var(--s7);
  text-align: center;
}

@media (max-width: 1080px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .side {
    position: static;
  }
  .grid2 {
    grid-template-columns: 1fr;
  }
}
</style>
