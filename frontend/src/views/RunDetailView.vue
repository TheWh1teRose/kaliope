<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError } from '@/api/client'
import type { FeedbackOut, NodeIOOut, Note, RunGraphOut, RunOut } from '@/api/types'
import AudioPanel from '@/components/AudioPanel.vue'
import FlowGraph from '@/components/FlowGraph.vue'
import GateList from '@/components/GateList.vue'
import ModalDialog from '@/components/ModalDialog.vue'
import NodeInspector from '@/components/NodeInspector.vue'
import NotesEditor from '@/components/NotesEditor.vue'
import StatusPill from '@/components/StatusPill.vue'
import { fill, t } from '@/i18n'
import { type ProgressLine, useRunsStore } from '@/stores/runs'
import { runTitle } from '@/titles'

const props = defineProps<{ id: string }>()

const runs = useRunsStore()
const run = ref<RunOut | null>(null)
const graph = ref<RunGraphOut | null>(null)
const inspected = ref<string | null>(null)
const io = ref<NodeIOOut | null>(null)
const ioLoading = ref(false)
const notesOpen = ref(false)
const feedback = ref<FeedbackOut | null>(null)
const draftNotes = ref<Note[]>([])
const notesBusy = ref(false)
const notesError = ref('')

const live = computed(() =>
  ['queued', 'running', 'outlined'].includes(run.value?.status ?? ''),
)
const totalTokens = computed(() =>
  (run.value?.nodes ?? []).reduce((sum, node) => sum + node.tokens_in + node.tokens_out, 0),
)
const reviewable = computed(
  () => run.value && ['completed', 'in_review', 'reviewed'].includes(run.value.status),
)
const awaitingNotes = computed(() => run.value?.status === 'paused')
const stoppable = computed(() => ['queued', 'running'].includes(run.value?.status ?? ''))
const stopped = computed(() => run.value?.status === 'stopped')
const stoppedNotice = computed(() => {
  const who = run.value?.stopped_by
  return who ? fill(t.run.stoppedNoticeBy, { who }) : t.run.stoppedNotice
})
const confirmStop = ref(false)
const stopping = ref(false)
const stopError = ref('')

function duration(node: { started_at: string | null; finished_at: string | null }): string {
  if (!node.started_at || !node.finished_at) return t.common.none
  const ms = new Date(node.finished_at).getTime() - new Date(node.started_at).getTime()
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

async function reload(): Promise<void> {
  run.value = await runs.get(props.id)
  // The graph needs the flow definition; a flow that has since been removed
  // leaves the rest of the page usable rather than blanking it.
  graph.value = await runs.graph(props.id).catch(() => null)
  if (run.value.status === 'paused') await loadFeedback()
  else {
    feedback.value = null
    notesOpen.value = false
  }
}

let refreshTimer: ReturnType<typeof setTimeout> | null = null

/**
 * A node event arrived: refresh the run and its graph shortly after, so the
 * node table and the flow chart move with the run instead of waiting for its
 * end. Bursts of events collapse into one refresh.
 */
function onLiveEvent(line: ProgressLine): void {
  if (!line.type.startsWith('node.') && line.type !== 'run.started') return
  if (refreshTimer) return
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    run.value = await runs.get(props.id)
    graph.value = await runs.graph(props.id).catch(() => graph.value)
  }, 400)
}

function follow(): void {
  if (live.value) runs.watch(props.id, reload, onLiveEvent)
}

async function loadFeedback(): Promise<void> {
  notesError.value = ''
  try {
    feedback.value = await runs.feedback(props.id)
    draftNotes.value = [...feedback.value.draft_notes]
  } catch (exc) {
    feedback.value = null
    notesError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  }
}

async function openNotes(): Promise<void> {
  notesOpen.value = true
  if (!feedback.value) await loadFeedback()
}

async function saveNotes(): Promise<void> {
  notesBusy.value = true
  notesError.value = ''
  try {
    feedback.value = await runs.saveFeedback(props.id, draftNotes.value)
    draftNotes.value = [...feedback.value.draft_notes]
  } catch (exc) {
    notesError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    notesBusy.value = false
  }
}

async function submitNotes(): Promise<void> {
  notesBusy.value = true
  notesError.value = ''
  try {
    await runs.submitFeedback(props.id, draftNotes.value)
    notesOpen.value = false
    feedback.value = null
    await reload()
    follow()
  } catch (exc) {
    notesError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    notesBusy.value = false
  }
}

async function stopRun(): Promise<void> {
  if (!run.value || stopping.value) return
  stopping.value = true
  stopError.value = ''
  try {
    await runs.stop(props.id)
    confirmStop.value = false
    await reload()
    follow()
  } catch (exc) {
    stopError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    stopping.value = false
  }
}

async function inspect(node: string): Promise<void> {
  inspected.value = node
  io.value = null
  ioLoading.value = true
  try {
    io.value = await runs.nodeIo(props.id, node)
  } finally {
    ioLoading.value = false
  }
}

function closeInspector(): void {
  inspected.value = null
  io.value = null
}

onMounted(async () => {
  await reload()
  follow()
})

onUnmounted(() => {
  runs.stopWatching()
  if (refreshTimer) clearTimeout(refreshTimer)
})
</script>

<template>
  <div v-if="run" class="page">
    <header class="head">
      <div class="grow">
        <RouterLink :to="{ name: 'runs' }" class="eyebrow back">← {{ t.nav.runs }}</RouterLink>
        <h1 class="h-page">
          {{ runTitle(run, t.run.title) }}
        </h1>
        <p class="meta">
          {{ run.flow_id }} v{{ run.flow_version }} · {{ run.id }}
        </p>
      </div>
      <div class="row wrap">
        <StatusPill :status="run.status" />
        <button
          v-if="stoppable"
          class="btn btn--sm"
          type="button"
          data-stop
          :disabled="stopping"
          @click="stopError = ''; confirmStop = true"
        >
          {{ t.run.stop }}
        </button>
        <RouterLink
          v-if="stopped"
          class="btn btn--sm"
          data-again
          :to="{ name: 'new-run', params: { id: run.document_id } }"
        >
          {{ t.run.again }}
        </RouterLink>
        <span class="badge badge--idle num">${{ run.total_cost_usd.toFixed(4) }}</span>
        <span class="badge badge--idle num">{{ totalTokens.toLocaleString('de-DE') }} tok</span>
        <button v-if="awaitingNotes" class="btn btn--mark" @click="openNotes">
          {{ t.run.openFeedback }}
        </button>
        <RouterLink class="btn btn--sm" :to="{ name: 'bench', query: { run: run.id } }">
          {{ t.bench.openInBench }}
        </RouterLink>
      </div>
    </header>

    <p v-if="run.verdict === 'insufficient'" class="notice notice--fail">
      {{ t.run.verdictInsufficient }}
    </p>
    <p v-if="stopped" class="notice" data-stopped>{{ stoppedNotice }}</p>

    <section v-if="graph" class="sheet">
      <div class="spread">
        <h2 class="h-section">{{ t.graph.title }}</h2>
        <span class="meta">{{ graph.flow_id }} v{{ graph.flow_version }}</span>
      </div>
      <p class="muted hint">{{ t.graph.lead }}</p>
      <FlowGraph
        :nodes="graph.nodes"
        :seeds="graph.seeds"
        :selected="inspected"
        :failed-node="graph.failed_node"
        @select="inspect"
      />
    </section>

    <div class="columns">
      <section class="stack">
        <div class="sheet">
          <h2 class="h-section">{{ t.run.nodes }}</h2>
          <table class="nodes">
            <thead>
              <tr>
                <th>{{ t.run.node }}</th>
                <th>{{ t.run.cacheHit }}</th>
                <th>{{ t.run.model }}</th>
                <th class="right">{{ t.run.tokens }}</th>
                <th class="right">{{ t.common.cost }}</th>
                <th class="right">{{ t.run.duration }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="node in run.nodes"
                :key="node.node_name"
                class="nodes__row"
                :class="{ bad: node.error }"
                @click="inspect(node.node_name)"
              >
                <td>
                  <span class="node__name">{{ node.node_name }}</span>
                  <span class="meta">v{{ node.node_version }}</span>
                </td>
                <td>
                  <span class="badge" :class="node.cache_hit ? 'badge--pass' : 'badge--idle'">
                    {{ node.cache_hit ? t.run.cached : t.run.computed }}
                  </span>
                </td>
                <td class="meta truncate">{{ node.model_id ?? t.common.none }}</td>
                <td class="right num">
                  {{ (node.tokens_in + node.tokens_out).toLocaleString('de-DE') }}
                </td>
                <td class="right num">${{ node.cost_usd.toFixed(4) }}</td>
                <td class="right num">{{ duration(node) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="run.error" class="sheet">
          <h2 class="h-section">{{ t.run.error }}</h2>
          <pre class="trace scroll">{{ run.error }}</pre>
        </div>

        <div v-if="live || runs.progress.length" class="sheet">
          <h2 class="h-section">{{ t.run.progress }}</h2>
          <p v-if="runs.reconnecting" class="muted hint">{{ t.run.reconnecting }}</p>
          <ol class="log scroll">
            <li v-for="(line, index) in runs.progress" :key="index" class="log__line">
              <span class="meta">{{ new Date(line.at).toLocaleTimeString('de-DE') }}</span>
              <span class="log__type meta">{{ line.type }}</span>
              <span class="log__text">
                {{ line.message ?? line.error ?? line.node ?? '' }}
              </span>
            </li>
            <li v-if="!runs.progress.length" class="muted log__line">{{ t.run.liveWaiting }}</li>
          </ol>
        </div>
      </section>

      <aside class="stack">
        <div class="sheet">
          <div class="spread">
            <h2 class="h-section">{{ t.run.gates }}</h2>
            <RouterLink class="detail-link" :to="{ name: 'run-gates', params: { id: run.id } }">
              {{ t.gate.openDetail }} →
            </RouterLink>
          </div>
          <p class="muted hint">{{ t.run.gatesLead }}</p>
          <GateList :gates="run.gates" />
        </div>

        <div v-if="awaitingNotes" class="sheet actions">
          <p class="muted hint">{{ t.bench.feedbackLead }}</p>
          <button class="btn btn--mark" @click="openNotes">
            {{ t.run.openFeedback }}
          </button>
        </div>

        <div v-if="reviewable" class="sheet actions">
          <RouterLink class="btn btn--mark" :to="{ name: 'review', params: { id: run.id } }">
            {{ t.run.openReview }}
          </RouterLink>
          <a class="btn" :href="`/api/runs/${run.id}/export?format=md`" download>
            {{ t.run.exportMd }}
          </a>
        </div>
      </aside>
    </div>

    <AudioPanel v-if="reviewable" :run-id="run.id" :format-id="run.format_spec?.id ?? null" />

    <Teleport to="body">
      <div v-if="notesOpen && awaitingNotes" class="notes-overlay">
        <button class="notes-overlay__close btn btn--ghost btn--sm" @click="notesOpen = false">
          {{ t.bench.closeNotes }}
        </button>
        <p v-if="notesError" class="notice notice--fail overlay-error">{{ notesError }}</p>
        <NotesEditor
          v-if="feedback"
          :feedback="feedback"
          :notes="draftNotes"
          :busy="notesBusy"
          @update:notes="draftNotes = $event"
          @save="saveNotes"
          @submit="submitNotes"
        />
        <p v-else class="muted pad">{{ t.common.loading }}</p>
      </div>
    </Teleport>

    <ModalDialog
      :open="Boolean(inspected)"
      :title="inspected ?? ''"
      :lead="t.graph.inspect"
      wide
      @close="closeInspector"
    >
      <NodeInspector :io="io" :loading="ioLoading" />
      <template #actions>
        <a
          v-if="io?.output?.artifact_hash"
          class="btn"
          :href="`/api/runs/${props.id}/artifacts/${io.node_name}`"
          target="_blank"
          rel="noopener"
        >
          {{ t.run.viewArtifact }}
        </a>
        <button class="btn btn--mark" @click="closeInspector">{{ t.common.close }}</button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="confirmStop"
      :title="t.run.stopTitle"
      :lead="t.run.stopLead"
      @close="confirmStop = false"
    >
      <p v-if="stopError" class="notice notice--fail" role="alert">{{ stopError }}</p>
      <template #actions>
        <button class="btn btn--ghost" type="button" @click="confirmStop = false">
          {{ t.common.cancel }}
        </button>
        <button
          class="btn btn--mark"
          type="button"
          data-action="confirm-stop"
          :disabled="stopping"
          @click="stopRun"
        >
          {{ t.run.stopConfirm }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.page {
  padding: var(--s6);
  max-width: 1400px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--s5);
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

.columns {
  display: grid;
  grid-template-columns: 1fr minmax(320px, 400px);
  gap: var(--s4);
  align-items: start;
}

.nodes {
  width: 100%;
  border-collapse: collapse;
  margin-top: var(--s3);
  font-size: var(--t-sm);
}

.nodes th {
  text-align: left;
  font-family: var(--mono);
  font-size: 0.625rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--ink-3);
  font-weight: 500;
  padding: 0 var(--s3) var(--s2);
  border-bottom: 1px solid var(--rule);
}

.nodes td {
  padding: var(--s2) var(--s3);
  border-bottom: 1px solid var(--rule);
  vertical-align: baseline;
}

.nodes tr.bad td {
  background: var(--fail-soft);
}

.nodes__row {
  cursor: pointer;
}

.nodes__row:hover td {
  background: var(--chrome);
}

.nodes__row.bad:hover td {
  background: var(--fail-soft);
}

.detail-link {
  font-size: var(--t-xs);
  color: var(--mark);
  text-decoration: none;
}

.detail-link:hover {
  text-decoration: underline;
}

.right {
  text-align: right;
}

.node__name {
  font-weight: 550;
  margin-right: 6px;
}

.trace {
  margin: var(--s3) 0 0;
  padding: var(--s3);
  max-height: 300px;
  border-radius: var(--r-md);
  background: var(--sunk);
  font-family: var(--mono);
  font-size: var(--t-xs);
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}

.log {
  list-style: none;
  margin: var(--s3) 0 0;
  padding: 0;
  max-height: 320px;
  display: grid;
  gap: 2px;
}

.log__line {
  display: grid;
  grid-template-columns: 68px 128px 1fr;
  gap: var(--s2);
  padding: 3px var(--s2);
  border-radius: var(--r-sm);
  font-size: var(--t-xs);
}

.log__line:nth-child(odd) {
  background: var(--chrome);
}

.log__type {
  color: var(--mark);
}

.log__text {
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
}

.hint {
  font-size: var(--t-xs);
  margin: 4px 0 var(--s3);
}

.actions {
  display: flex;
  gap: var(--s3);
  flex-wrap: wrap;
}

.actions .btn {
  text-decoration: none;
}

.notice {
  padding: var(--s3) var(--s4);
  border-radius: var(--r-md);
  font-size: var(--t-sm);
}

.notice--fail {
  background: var(--fail-soft);
  color: var(--fail);
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

.overlay-error {
  margin: var(--s5) var(--s6) 0;
}

.pad {
  padding: var(--s5);
}

@media (max-width: 1080px) {
  .columns {
    grid-template-columns: 1fr;
  }
}
</style>
