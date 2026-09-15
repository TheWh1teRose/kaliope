<script setup lang="ts">
/**
 * The pipelines page: what a run can be made of.
 *
 * Three things belong together here and are shown as three tabs of one page,
 * because editing any of them changes what a run does. Pipelines are the ordered
 * steps; formats are the shape of the episode those steps produce; the node
 * catalogue is the parts list both are assembled from, with each part explaining
 * itself.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import type { FormatSpec, FormatSummary, NodeSpecOut, PipelineSummary } from '@/api/types'
import ModalDialog from '@/components/ModalDialog.vue'
import NodeDocPanel from '@/components/NodeDocPanel.vue'
import { t } from '@/i18n'
import { usePipelinesStore } from '@/stores/pipelines'

type Tab = 'pipelines' | 'formats' | 'nodes'

const store = usePipelinesStore()
const router = useRouter()

const tab = ref<Tab>('pipelines')
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const showArchived = ref(false)
const openNode = ref<string | null>(null)

const creating = ref(false)
const draftId = ref('')
const draftName = ref('')
const draftFrom = ref('')

const creatingFormat = ref(false)
const formatId = ref('')
const formatName = ref('')

const archiveTarget = ref<PipelineSummary | null>(null)
const archiveFormatTarget = ref<FormatSummary | null>(null)

const nodes = computed<NodeSpecOut[]>(() => store.nodes?.nodes ?? [])

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([
      store.list(showArchived.value),
      store.listFormats(showArchived.value),
      store.loadNodes(),
    ])
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    loading.value = false
  }
}

async function createPipeline(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const source = store.pipelines.find((p) => p.id === draftFrom.value)
    const base = source ? await store.get(source.id) : null
    const detail = await store.create({
      id: draftId.value.trim(),
      name: draftName.value.trim() || draftId.value.trim(),
      version: '1.0',
      description: base?.definition.description ?? null,
      nodes: base?.definition.nodes ?? [],
      gates: base?.definition.gates ?? [],
      note: source ? `${t.pipelines.copyOf} ${source.id}` : null,
    })
    creating.value = false
    draftId.value = ''
    draftName.value = ''
    draftFrom.value = ''
    await router.push({ name: 'pipeline', params: { id: detail.id } })
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

async function createFormat(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const spec: FormatSpec = {
      id: formatId.value.trim(),
      name: formatName.value.trim() || formatId.value.trim(),
      speakers: [
        { id: 'host', name: 'Moderator', role: 'stellt die Fragen', voice_note: null },
        { id: 'expert', name: 'Expertin', role: 'erklärt den Stoff', voice_note: null },
      ],
      register: 'formal',
      target_minutes: 15,
      opening: null,
      closing: null,
      beats_hint: null,
    }
    const detail = await store.createFormat(spec)
    creatingFormat.value = false
    formatId.value = ''
    formatName.value = ''
    await router.push({ name: 'format', params: { id: detail.id } })
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

async function applyArchive(): Promise<void> {
  const target = archiveTarget.value
  if (!target) return
  busy.value = true
  try {
    await store.archive(target.id, !target.archived)
    archiveTarget.value = null
    await load()
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

async function applyArchiveFormat(): Promise<void> {
  const target = archiveFormatTarget.value
  if (!target) return
  busy.value = true
  try {
    await store.archiveFormat(target.id, !target.archived)
    archiveFormatTarget.value = null
    await load()
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

function toggleNode(name: string): void {
  openNode.value = openNode.value === name ? null : name
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="head">
      <div class="grow">
        <p class="eyebrow">{{ t.app.name }}</p>
        <h1 class="h-page">{{ t.pipelines.title }}</h1>
        <p class="muted lead">{{ t.pipelines.lead }}</p>
      </div>
      <button
        v-if="tab === 'pipelines'"
        class="btn btn--primary"
        @click="creating = true"
      >
        {{ t.pipelines.new }}
      </button>
      <button
        v-else-if="tab === 'formats'"
        class="btn btn--primary"
        @click="creatingFormat = true"
      >
        {{ t.formats.new }}
      </button>
    </header>

    <div class="tabs">
      <button class="tab" :class="{ 'tab--on': tab === 'pipelines' }" @click="tab = 'pipelines'">
        {{ t.pipelines.tabPipelines }}
        <span class="tab__count num">{{ store.pipelines.length }}</span>
      </button>
      <button class="tab" :class="{ 'tab--on': tab === 'formats' }" @click="tab = 'formats'">
        {{ t.pipelines.tabFormats }}
        <span class="tab__count num">{{ store.formats.length }}</span>
      </button>
      <button class="tab" :class="{ 'tab--on': tab === 'nodes' }" @click="tab = 'nodes'">
        {{ t.pipelines.tabNodes }}
        <span class="tab__count num">{{ nodes.length }}</span>
      </button>
      <span class="grow" />
      <label v-if="tab !== 'nodes'" class="toggle">
        <input v-model="showArchived" type="checkbox" @change="load" />
        {{ t.pipelines.showArchived }}
      </label>
    </div>

    <p v-if="error" class="banner banner--fail">{{ error }}</p>
    <p v-if="loading" class="muted pad">{{ t.common.loading }}</p>

    <!-- ------------------------------------------------------- pipelines -->
    <ul v-else-if="tab === 'pipelines'" class="cards">
      <li v-if="!store.pipelines.length" class="muted pad">{{ t.pipelines.empty }}</li>
      <li
        v-for="pipeline in store.pipelines"
        :key="pipeline.id"
        class="card item"
        :class="{ 'item--off': pipeline.archived }"
      >
        <RouterLink :to="{ name: 'pipeline', params: { id: pipeline.id } }" class="item__main">
          <span class="item__title">
            <span class="item__name">{{ pipeline.name }}</span>
            <code class="item__id">{{ pipeline.id }}</code>
            <span class="badge badge--idle num">v{{ pipeline.version }}</span>
            <span class="badge badge--mark num">r{{ pipeline.revision }}</span>
            <span v-if="!pipeline.valid" class="badge badge--fail">{{ t.pipelines.invalid }}</span>
            <span v-if="pipeline.archived" class="badge badge--warn">
              {{ t.pipelines.archived }}
            </span>
          </span>
          <span v-if="pipeline.description" class="item__lead">{{ pipeline.description }}</span>
          <span class="item__chain">
            <template v-for="(node, index) in pipeline.nodes" :key="node">
              <span v-if="index" class="item__arrow" aria-hidden="true">·</span>
              <span class="item__node num">{{ node }}</span>
            </template>
          </span>
        </RouterLink>

        <div class="item__side">
          <span class="meta">
            {{ pipeline.origin === 'file' ? t.pipelines.originFile : t.pipelines.originUser }}
          </span>
          <span class="meta num">{{ pipeline.run_count }} {{ t.pipelines.runs }}</span>
          <button class="btn btn--ghost btn--sm" @click="archiveTarget = pipeline">
            {{ pipeline.archived ? t.pipelines.unarchive : t.pipelines.archive }}
          </button>
        </div>
      </li>
    </ul>

    <!-- --------------------------------------------------------- formats -->
    <ul v-else-if="tab === 'formats'" class="cards">
      <li v-if="!store.formats.length" class="muted pad">{{ t.formats.empty }}</li>
      <li
        v-for="format in store.formats"
        :key="format.id"
        class="card item"
        :class="{ 'item--off': format.archived }"
      >
        <RouterLink :to="{ name: 'format', params: { id: format.id } }" class="item__main">
          <span class="item__title">
            <span class="item__name">{{ format.name }}</span>
            <code class="item__id">{{ format.id }}</code>
            <span class="badge badge--mark num">r{{ format.revision }}</span>
            <span v-if="format.archived" class="badge badge--warn">{{ t.pipelines.archived }}</span>
          </span>
          <span class="item__lead">
            {{ format.speakers }} {{ t.formats.speakers }} · {{ format.register }} ·
            {{ format.target_minutes }} {{ t.common.minutes }}
          </span>
        </RouterLink>
        <div class="item__side">
          <span class="meta">
            {{ format.origin === 'file' ? t.pipelines.originFile : t.pipelines.originUser }}
          </span>
          <span class="meta num">{{ format.run_count }} {{ t.pipelines.runs }}</span>
          <button class="btn btn--ghost btn--sm" @click="archiveFormatTarget = format">
            {{ format.archived ? t.pipelines.unarchive : t.pipelines.archive }}
          </button>
        </div>
      </li>
    </ul>

    <!-- ----------------------------------------------------------- nodes -->
    <div v-else class="nodes">
      <p class="muted lead">{{ t.nodeCatalogue.lead }}</p>
      <ul class="cards">
        <li v-for="node in nodes" :key="node.name" class="card node">
          <button class="node__head" :aria-expanded="openNode === node.name" @click="toggleNode(node.name)">
            <span class="node__title grow">
              <span class="item__name">{{ node.title }}</span>
              <code class="item__id">{{ node.name }}</code>
              <span class="badge badge--idle num">v{{ node.version }}</span>
            </span>
            <span class="node__io meta">
              <span v-for="key in node.consumes" :key="key" class="num">{{ key }}</span>
              <span aria-hidden="true">→</span>
              <span class="num node__out">{{ node.produces }}</span>
            </span>
            <span class="chev" aria-hidden="true">{{ openNode === node.name ? '−' : '+' }}</span>
          </button>
          <div v-if="openNode === node.name" class="node__body">
            <NodeDocPanel :node="node" />
            <section v-if="node.params.length" class="node__params">
              <p class="eyebrow">{{ t.nodeCatalogue.parameters }}</p>
              <ul class="kv">
                <li v-for="param in node.params" :key="param.key">
                  <span class="kv__key num">{{ param.key }}</span>
                  <span class="kv__text">{{ param.label }} — {{ param.description }}</span>
                </li>
              </ul>
            </section>
          </div>
        </li>
      </ul>
    </div>

    <!-- --------------------------------------------------------- dialogs -->
    <ModalDialog
      :open="creating"
      :title="t.pipelines.newTitle"
      :lead="t.pipelines.newLead"
      @close="creating = false"
    >
      <div class="form">
        <div class="field">
          <label for="new-id">{{ t.pipelines.id }}</label>
          <input id="new-id" v-model="draftId" class="input" data-autofocus />
          <p class="hint">{{ t.pipelines.idHint }}</p>
        </div>
        <div class="field">
          <label for="new-name">{{ t.pipelines.name }}</label>
          <input id="new-name" v-model="draftName" class="input" />
        </div>
        <div class="field">
          <label for="new-from">{{ t.pipelines.startFrom }}</label>
          <select id="new-from" v-model="draftFrom" class="select">
            <option value="">{{ t.pipelines.startEmpty }}</option>
            <option v-for="p in store.pipelines" :key="p.id" :value="p.id">
              {{ p.name }} ({{ p.id }})
            </option>
          </select>
        </div>
      </div>
      <template #actions>
        <button class="btn" @click="creating = false">{{ t.common.cancel }}</button>
        <button class="btn btn--primary" :disabled="busy || !draftId.trim()" @click="createPipeline">
          {{ t.common.create }}
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="creatingFormat"
      :title="t.formats.newTitle"
      @close="creatingFormat = false"
    >
      <div class="form">
        <div class="field">
          <label for="fmt-id">{{ t.formats.id }}</label>
          <input id="fmt-id" v-model="formatId" class="input" data-autofocus />
          <p class="hint">{{ t.pipelines.idHint }}</p>
        </div>
        <div class="field">
          <label for="fmt-name">{{ t.formats.name }}</label>
          <input id="fmt-name" v-model="formatName" class="input" />
        </div>
      </div>
      <template #actions>
        <button class="btn" @click="creatingFormat = false">{{ t.common.cancel }}</button>
        <button
          class="btn btn--primary"
          :disabled="busy || !formatId.trim()"
          @click="createFormat"
        >
          {{ t.common.create }}
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="Boolean(archiveTarget)"
      :title="t.pipelines.archiveTitle"
      :lead="t.pipelines.archiveLead"
      @close="archiveTarget = null"
    >
      <p class="prose">{{ archiveTarget?.name }}</p>
      <template #actions>
        <button class="btn" @click="archiveTarget = null">{{ t.common.cancel }}</button>
        <button class="btn btn--primary" :disabled="busy" @click="applyArchive">
          {{ archiveTarget?.archived ? t.pipelines.unarchive : t.pipelines.archive }}
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="Boolean(archiveFormatTarget)"
      :title="t.formats.archiveTitle"
      :lead="t.formats.archiveLead"
      @close="archiveFormatTarget = null"
    >
      <p class="prose">{{ archiveFormatTarget?.name }}</p>
      <template #actions>
        <button class="btn" @click="archiveFormatTarget = null">{{ t.common.cancel }}</button>
        <button class="btn btn--primary" :disabled="busy" @click="applyArchiveFormat">
          {{ archiveFormatTarget?.archived ? t.pipelines.unarchive : t.pipelines.archive }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.page {
  padding: var(--s6);
  max-width: 1180px;
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

.lead {
  margin-top: 6px;
  font-size: var(--t-sm);
  max-width: 82ch;
}

.tabs {
  display: flex;
  align-items: center;
  gap: var(--s2);
  border-bottom: 1px solid var(--rule);
  padding-bottom: var(--s2);
}

.tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border: 0;
  border-radius: var(--r-md);
  background: transparent;
  color: var(--ink-3);
  font-size: var(--t-sm);
  cursor: pointer;
}

.tab:hover {
  background: var(--chrome);
  color: var(--ink);
}

.tab--on {
  background: var(--ink);
  color: var(--chrome);
}

.tab__count {
  font-family: var(--mono);
  font-size: var(--t-xs);
  opacity: 0.72;
}

.toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--t-sm);
  color: var(--ink-2);
}

.cards {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s2);
}

.item {
  display: flex;
  align-items: center;
  gap: var(--s4);
  padding: var(--s3) var(--s4);
}

.item--off {
  opacity: 0.6;
}

.item__main {
  display: grid;
  gap: 5px;
  flex: 1;
  min-width: 0;
  text-decoration: none;
  color: inherit;
}

.item__title {
  display: flex;
  align-items: center;
  gap: var(--s2);
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

.item__lead {
  font-size: var(--t-sm);
  color: var(--ink-2);
  max-width: 78ch;
}

.item__chain {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.item__node {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-3);
  padding: 1px 6px;
  border-radius: var(--r-sm);
  background: var(--chrome);
}

.item__arrow {
  color: var(--ink-4);
}

.item__side {
  display: grid;
  justify-items: end;
  gap: 4px;
  text-align: right;
}

.node__head {
  display: flex;
  align-items: center;
  gap: var(--s3);
  width: 100%;
  padding: var(--s3) var(--s4);
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.node__head:hover {
  background: var(--chrome);
}

.node__title {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.node__io {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-4);
}

.node__out {
  color: var(--mark-deep);
}

.node__body {
  display: grid;
  gap: var(--s4);
  padding: 0 var(--s4) var(--s4);
}

.node__params {
  display: grid;
  gap: var(--s2);
}

.kv {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s1);
}

.kv li {
  display: grid;
  gap: 2px;
  padding: var(--s2) var(--s3);
  background: var(--chrome);
  border-radius: var(--r-sm);
}

.kv__key {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--mark-deep);
}

.kv__text {
  font-size: var(--t-sm);
  color: var(--ink-2);
  line-height: 1.5;
}

.chev {
  font-family: var(--mono);
  color: var(--ink-3);
  width: 12px;
  text-align: center;
}

.nodes {
  display: grid;
  gap: var(--s3);
}

.form {
  display: grid;
  gap: var(--s4);
}

.hint {
  margin: 0;
  font-size: var(--t-xs);
  color: var(--ink-3);
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

.pad {
  padding: var(--s7);
  text-align: center;
}

@media (max-width: 760px) {
  .item {
    flex-direction: column;
    align-items: flex-start;
  }
  .item__side {
    justify-items: start;
    text-align: left;
  }
}
</style>
