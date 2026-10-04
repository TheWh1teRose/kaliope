<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { audioApi } from '@/api/audio'
import { ApiError } from '@/api/client'
import type { NodeIOOut, SeriesEvent, SeriesGraphOut, SeriesOut } from '@/api/types'
import AudioPanel from '@/components/AudioPanel.vue'
import ModalDialog from '@/components/ModalDialog.vue'
import NodeInspector from '@/components/NodeInspector.vue'
import PipelineCanvas from '@/components/PipelineCanvas.vue'
import SeriesAudio from '@/components/SeriesAudio.vue'
import StatusPill from '@/components/StatusPill.vue'
import { fill, t } from '@/i18n'
import { seriesTitle } from '@/titles'
import type { PlacedNode } from '@/series/canvas'
import { MAX_EPISODES, MIN_EPISODES } from '@/series/estimate'
import { liveNote, waitingReasons } from '@/series/live'
import { useRunsStore } from '@/stores/runs'
import { useSeriesStore } from '@/stores/series'

const props = defineProps<{ id: string }>()

const seriesStore = useSeriesStore()
const runs = useRunsStore()

const series = ref<SeriesOut | null>(null)
const graph = ref<SeriesGraphOut | null>(null)
const tab = ref<'plan' | number>('plan')
const live = ref<Record<string, string>>({})
const selected = ref<PlacedNode | null>(null)
const io = ref<NodeIOOut | null>(null)
const ioLoading = ref(false)
const busy = ref(false)
const error = ref('')
const replanCount = ref<number | null>(null)
const replanMinutes = ref<number | null>(null)
const replanHint = ref<string | null>(null)
const confirmStop = ref(false)

const IDLE = new Set(['planned', 'completed', 'failed', 'stopped'])

const moving = computed(
  () => !!series.value && series.value.status !== 'stopped' &&
    (!IDLE.has(series.value.status) || series.value.active),
)
const stuck = computed(
  () => !!series.value && !IDLE.has(series.value.status) && !series.value.active,
)
const plan = computed(() => series.value?.plan ?? null)
const episodeCount = computed(() => plan.value?.episodes.length ?? series.value?.request.episodes ?? 0)
const audioFormat = ref<string | null>(null)
const anyDone = computed(() =>
  (series.value?.episodes ?? []).some((e) => ['completed', 'in_review', 'reviewed'].includes(e.status)),
)
const waiting = computed(() => (series.value ? waitingReasons(series.value) : {}))
const selectedEpisode = computed(() => (typeof tab.value === 'number' ? tab.value : null))
const currentEpisode = computed(() =>
  typeof tab.value === 'number' ? series.value?.episodes.find((e) => e.index === tab.value) ?? null : null,
)
const currentPlanEpisode = computed(() =>
  typeof tab.value === 'number' ? plan.value?.episodes.find((e) => e.index === tab.value) ?? null : null,
)
const coverage = computed(() => series.value?.checks.S1 ?? null)
/** The script currently being written, as its node reports it. */
const scriptNote = computed(() => {
  const notes = Object.entries(live.value).filter(([key]) => key.endsWith(':script'))
  return notes.length ? notes[notes.length - 1][1] : ''
})
const countWarning = computed(() => {
  const asked = plan.value?.budget.requested_episodes
  const planned = plan.value?.episodes.length
  if (asked == null || planned == null || asked === planned) return ''
  return fill(t.series.countDiffers, { asked, planned })
})
const stoppedNotice = computed(() => {
  const who = series.value?.stopped_by
  return who ? fill(t.series.stoppedNoticeBy, { who }) : t.series.stoppedNotice
})
const budgetLine = computed(() => {
  if (!plan.value) return ''
  const budget = plan.value.budget
  return `${fill(t.series.supports, { minutes: minutes(budget.max_supportable_minutes) })} · ${fill(
    t.series.episodesOf,
    { n: plan.value.episodes.length, minutes: minutes(budget.minutes_per_episode) },
  )}`
})

function money(value: number): string {
  return `$${value.toFixed(4)}`
}

function minutes(value: number | null | undefined): string {
  return (value ?? 0).toLocaleString('de-DE', { maximumFractionDigits: 1 })
}

function seriesTone(status: string): string {
  if (status === 'failed') return 'badge--fail'
  if (status === 'completed') return 'badge--pass'
  if (status === 'planned') return 'badge--warn'
  if (status === 'stopped') return 'badge--idle'
  return 'badge--mark'
}

async function reload(): Promise<void> {
  series.value = await seriesStore.get(props.id)
  graph.value = await seriesStore.graph(props.id).catch(() => graph.value)
  if (replanCount.value === null && series.value.plan) {
    replanCount.value = series.value.plan.episodes.length
  }
  if (replanMinutes.value === null) {
    replanMinutes.value = series.value.request.minutes_per_episode
  }
  if (replanHint.value === null) {
    replanHint.value = series.value.request.hint ?? ''
  }
  if (series.value.status === 'stopped') {
    if (refreshTimer) clearTimeout(refreshTimer)
    refreshTimer = null
    if (series.value.active) scheduleRefresh(1500)
  }
}

let disposed = false
let refreshTimer: ReturnType<typeof setTimeout> | null = null

function scheduleRefresh(delay = 400): void {
  if (disposed || refreshTimer) return
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    await reload()
  }, delay)
}

function onEvent(event: SeriesEvent): void {
  if (event.type === 'node.progress' && event.run_id && event.node && event.message) {
    live.value = { ...live.value, [`${event.run_id}:${event.node}`]: liveNote(event.message) }
  }
  if (event.type !== 'node.progress' && series.value?.status !== 'stopped') scheduleRefresh()
}

function follow(): void {
  if (!moving.value) {
    seriesStore.stopWatching()
    return
  }
  seriesStore.watch(props.id, {
    onEvent,
    onTerminal: async () => {
      await reload()
      follow()
    },
    onReconnect: () => {
      reload()
    },
  })
}

async function act(action: () => Promise<SeriesOut>): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    series.value = await action()
    await reload()
    follow()
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

function approve(): Promise<void> {
  return act(() => seriesStore.approve(props.id))
}

function resume(): Promise<void> {
  return act(() => seriesStore.resume(props.id))
}

async function stopSeries(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    await seriesStore.stop(props.id)
    confirmStop.value = false
    await reload()
    follow()
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

function replan(): Promise<void> {
  return act(() =>
    seriesStore.replan(props.id, {
      episodes: replanCount.value,
      minutes_per_episode: replanMinutes.value ?? series.value?.request.minutes_per_episode,
      hint: replanHint.value ?? '',
    }),
  )
}

function stepReplan(delta: number): void {
  const base = replanCount.value ?? plan.value?.episodes.length ?? MIN_EPISODES
  replanCount.value = Math.max(MIN_EPISODES, Math.min(MAX_EPISODES, base + delta))
}

async function inspect(node: PlacedNode): Promise<void> {
  if (node.episode > 0) tab.value = node.episode
  if (!node.runId) {
    selected.value = node
    io.value = null
    return
  }
  selected.value = node
  io.value = null
  ioLoading.value = true
  try {
    io.value = await runs.nodeIo(node.runId, node.node.name)
  } finally {
    ioLoading.value = false
  }
}

function closeInspector(): void {
  selected.value = null
  io.value = null
}

onMounted(async () => {
  await reload()
  follow()
  // The episodes share one format; its voices are the default for every episode.
  audioApi
    .series(props.id)
    .then((audio) => (audioFormat.value = audio.format_id))
    .catch(() => undefined)
})

onUnmounted(() => {
  disposed = true
  seriesStore.stopWatching()
  if (refreshTimer) clearTimeout(refreshTimer)
})
</script>

<template>
  <div v-if="series" class="page">
    <header class="head">
      <div class="grow">
        <RouterLink :to="{ name: 'runs' }" class="eyebrow back">← {{ t.nav.runs }}</RouterLink>
        <h1 class="h-page">{{ seriesTitle(series, t.series.title) }}</h1>
        <p class="meta">
          {{ series.flow_id }} v{{ series.flow_version }} · {{ series.plan_flow_id }} ·
          {{ series.document_title }} · {{ series.id }}
        </p>
      </div>
      <div class="row wrap">
        <span class="badge" :class="seriesTone(series.status)" data-series-status>
          <span v-if="moving" class="pulse" aria-hidden="true" />
          {{ t.series.status[series.status] }}
        </span>
        <span class="badge badge--idle num">{{ money(series.total_cost_usd) }}</span>
        <a
          v-if="anyDone"
          class="btn btn--sm"
          :href="`/api/series/${series.id}/export?format=zip`"
          download
        >
          {{ t.series.exportZip }}
        </a>
        <button
          v-if="moving"
          class="btn btn--sm"
          type="button"
          data-stop
          :disabled="busy"
          @click="confirmStop = true"
        >
          {{ t.series.stop }}
        </button>
        <button
          v-if="series.status === 'failed' || series.status === 'stopped' || stuck"
          class="btn btn--sm btn--primary"
          type="button"
          :disabled="busy"
          data-resume
          @click="resume"
        >
          {{ t.series.resume }}
        </button>
      </div>
    </header>

    <p v-if="series.status === 'stopped'" class="notice" data-stopped>{{ stoppedNotice }}</p>
    <p v-if="series.error" class="notice notice--fail" data-series-error>{{ series.error }}</p>
    <p v-else-if="stuck" class="notice notice--warn">{{ t.series.stuck }}</p>
    <p v-if="error" class="notice notice--fail" role="alert">{{ error }}</p>
    <p v-if="seriesStore.reconnecting" class="muted hint">{{ t.run.reconnecting }}</p>

    <div class="progress">
      <div :class="{ done: series.plan_run_status === 'completed' && series.status !== 'planned' }">
        <b>{{ t.series.progressPlan }}</b>
        <span>
          {{
            series.status === 'planned'
              ? t.series.progressPlanHeld
              : series.plan_run_status === 'completed'
                ? `${t.series.progressPlanDone} · ${money(series.plan_cost_usd)}`
                : t.series.progressPlanRunning
          }}
        </span>
        <span class="meter"><i :style="{ width: series.plan ? '100%' : '40%' }" /></span>
      </div>
      <div :class="{ done: episodeCount > 0 && (series.progress.outlined ?? 0) >= episodeCount }">
        <b>{{ fill(t.series.progressOutlines, { done: series.progress.outlined ?? 0, total: episodeCount }) }}</b>
        <span>{{ t.series.progressOutlinesLead }}</span>
        <span class="meter">
          <i :style="{ width: `${episodeCount ? ((series.progress.outlined ?? 0) / episodeCount) * 100 : 0}%` }" />
        </span>
      </div>
      <div :class="{ done: episodeCount > 0 && (series.progress.written ?? 0) >= episodeCount }">
        <b>{{ fill(t.series.progressScripts, { done: series.progress.written ?? 0, total: episodeCount }) }}</b>
        <span>
          {{
            (series.progress.written ?? 0) >= episodeCount && episodeCount
              ? t.series.progressScriptsDone
              : scriptNote || t.series.progressWaiting
          }}
        </span>
        <span class="meter">
          <i :style="{ width: `${episodeCount ? ((series.progress.written ?? 0) / episodeCount) * 100 : 0}%` }" />
        </span>
      </div>
    </div>

    <section v-if="graph" class="sheet">
      <div class="spread">
        <h2 class="h-section">{{ t.series.canvas }}</h2>
        <span class="meta">{{ money(series.total_cost_usd) }}</span>
      </div>
      <p class="muted hint">{{ t.series.canvasLead }}</p>
      <PipelineCanvas
        :graph="graph"
        :selected-episode="selectedEpisode"
        :selected-key="selected?.key ?? null"
        :live="live"
        :waiting="waiting"
        @select="inspect"
        @lane="(episode) => (tab = episode)"
      />
      <div v-if="selected" class="inspector">
        <div class="spread">
          <h3 class="h-section">{{ t.series.inspector }} · {{ selected.node.name }}</h3>
          <button class="btn btn--sm" type="button" @click="closeInspector">{{ t.common.close }}</button>
        </div>
        <NodeInspector v-if="selected.runId" :io="io" :loading="ioLoading" />
        <p v-else class="muted">{{ t.series.noRunYet }}</p>
      </div>
    </section>

    <div class="switcher" role="tablist" :aria-label="t.series.title">
      <button role="tab" :aria-selected="tab === 'plan'" data-tab="plan" @click="tab = 'plan'">
        <b>{{ t.series.planTab }}</b>
        <span class="sub">
          <span class="badge" :class="seriesTone(series.status === 'planned' ? 'planned' : series.plan ? 'completed' : 'running')">
            {{ series.status === 'planned' ? t.series.waitingApproval : series.plan ? t.series.progressPlanDone : t.series.progressPlanRunning }}
          </span>
        </span>
      </button>
      <button
        v-for="episode in series.episodes"
        :key="episode.index"
        role="tab"
        :aria-selected="tab === episode.index"
        :data-tab="episode.index"
        @click="tab = episode.index"
      >
        <b>{{ fill(t.series.episodeTab, { n: episode.index, title: episode.title }) }}</b>
        <span class="sub">
          <StatusPill v-if="episode.run_id" :status="episode.status" />
          <span v-else class="badge badge--idle">{{ t.graph.status.pending }}</span>
          <span class="meta">{{ money(episode.total_cost_usd) }}</span>
        </span>
      </button>
    </div>

    <section v-if="tab === 'plan'" class="stack" data-panel="plan">
      <div v-if="plan" class="sheet">
        <div class="spread">
          <h2 class="h-section">{{ t.series.plan }}</h2>
          <span class="meta">{{ budgetLine }}</span>
        </div>
        <p v-if="plan.through_line" class="prose">{{ plan.through_line }}</p>
        <div class="cov" aria-hidden="true">
          <div
            v-for="episode in plan.episodes"
            :key="episode.index"
            class="cov__part"
            :style="{ flex: episode.block_ids.length }"
          >
            {{ episode.index }}
          </div>
        </div>
        <div class="row wrap">
          <span v-if="coverage" class="badge" :class="coverage.status === 'pass' ? 'badge--pass' : 'badge--warn'">
            {{ fill(t.series.coverage, { share: Math.round(coverage.assigned_share * 100) }) }}
          </span>
          <span v-if="plan.budget.verdict !== 'ok'" class="badge badge--warn" :title="plan.budget.explanation">
            {{ plan.budget.verdict === 'reduced' ? t.series.merged : t.series.clamped }}
          </span>
          <span v-if="countWarning" class="badge badge--warn" data-count-warning>{{ countWarning }}</span>
          <span v-for="warning in plan.warnings" :key="warning" class="badge badge--warn" data-budget-warning>
            {{ warning }}
          </span>
          <span v-if="coverage?.uncited_sections.length" class="meta">
            {{ fill(t.series.coverageUncited, { sections: coverage.uncited_sections.join(', ') }) }}
          </span>
        </div>
        <div class="episodes">
          <article v-for="episode in plan.episodes" :key="episode.index" class="episode">
            <h3>
              <span class="epdot">{{ episode.index }}</span>
              {{ episode.title }}
            </h3>
            <span class="eyebrow">
              {{ episode.role }} · {{ fill(t.series.minutes, { n: minutes(episode.target_minutes) }) }} ·
              {{ fill(t.series.passages, { n: episode.block_ids.length }) }}
            </span>
            <p v-if="episode.summary" class="muted small">{{ episode.summary }}</p>
            <ul class="goals">
              <li v-for="goal in episode.goals" :key="goal.id">
                {{ goal.text }}<template v-if="goal.bloom_level"> ({{ goal.bloom_level }})</template>
              </li>
            </ul>
            <p v-if="episode.recap" class="line">{{ t.series.recap }}: {{ episode.recap }}</p>
            <p v-if="episode.preview" class="line">{{ t.series.preview }}: {{ episode.preview }}</p>
          </article>
        </div>
        <div v-if="plan.terms.length" class="terms">
          <span class="eyebrow">{{ t.series.terms }}</span>
          <span v-for="term in plan.terms" :key="term.term" class="small">
            <b>{{ term.term }}</b> ({{ fill(t.series.termFrom, { n: term.first_episode }) }})<template
              v-if="term.gloss"
              >: {{ term.gloss }}</template
            >
          </span>
        </div>
        <div v-if="plan.unassigned.length" class="small muted">
          {{ t.series.unassigned }}:
          {{ plan.unassigned.map((u) => `${u.block_id} (${u.reason})`).join(', ') }}
        </div>
      </div>

      <div v-if="series.status === 'planned' || (series.status === 'failed' && !series.episodes.some((e) => e.run_id))" class="sheet">
        <div class="row wrap">
          <button
            v-if="series.status === 'planned'"
            class="btn btn--primary"
            type="button"
            :disabled="busy"
            data-approve
            @click="approve"
          >
            {{ busy ? t.series.approving : t.series.approve }}
          </button>
          <span class="meta">{{ t.series.count }}</span>
          <span class="stepper">
            <button
              type="button"
              class="btn btn--sm"
              :aria-label="t.series.countLess"
              data-replan-step="-1"
              @click="stepReplan(-1)"
            >
              −
            </button>
            <output class="num count" data-replan-count>{{ replanCount ?? episodeCount }}</output>
            <button
              type="button"
              class="btn btn--sm"
              :aria-label="t.series.countMore"
              data-replan-step="1"
              @click="stepReplan(1)"
            >
              +
            </button>
          </span>
          <label class="meta" for="replan-minutes">{{ t.series.minutesPerEpisode }}</label>
          <input
            id="replan-minutes"
            v-model.number="replanMinutes"
            class="input minutes-range"
            type="range"
            min="3"
            max="60"
            step="1"
            data-replan-minutes
          />
          <output class="num" data-replan-minutes-out>{{ replanMinutes }}</output>
          <input
            v-model="replanHint"
            class="input hint-input"
            data-replan-hint
            :placeholder="t.series.hintPlaceholder"
          />
          <button class="btn btn--sm" type="button" :disabled="busy" data-replan @click="replan">
            {{ t.series.replan }}
          </button>
        </div>
        <p class="muted hint">{{ t.series.replanHint }}</p>
      </div>

      <SeriesAudio v-if="anyDone" :series-id="series.id" />
    </section>

    <section v-else-if="currentEpisode" class="stack" :data-panel="currentEpisode.index">
      <div class="sheet">
        <div class="spread">
          <h2 class="h-section">
            {{ fill(t.series.episodeTab, { n: currentEpisode.index, title: currentEpisode.title }) }}
          </h2>
          <span class="meta">
            {{ fill(t.series.minutes, { n: minutes(currentEpisode.target_minutes) }) }} ·
            {{ money(currentEpisode.total_cost_usd) }}
          </span>
        </div>
        <p v-if="currentEpisode.error" class="notice notice--fail">{{ currentEpisode.error }}</p>
        <p v-if="!currentEpisode.run_id" class="muted">{{ t.series.noRunYet }}</p>
        <div v-if="currentPlanEpisode" class="got">
          <div>
            <b>{{ fill(t.series.passages, { n: currentPlanEpisode.block_ids.length }) }}</b>
            {{ fill(t.series.recapPassages, { n: currentPlanEpisode.recap_block_ids.length }) }}
          </div>
          <div>
            <b>{{ currentPlanEpisode.goals.length }} {{ t.series.goals }}</b>
            <span v-if="waiting[currentEpisode.index]">{{ waiting[currentEpisode.index] }}</span>
          </div>
        </div>
        <ul v-if="currentPlanEpisode" class="goals">
          <li v-for="goal in currentPlanEpisode.goals" :key="goal.id">
            {{ goal.text }}<template v-if="goal.bloom_level"> ({{ goal.bloom_level }})</template>
          </li>
        </ul>
        <div v-if="currentEpisode.run_id" class="row wrap">
          <RouterLink class="btn" :to="{ name: 'run', params: { id: currentEpisode.run_id } }">
            {{ t.series.openRun }}
          </RouterLink>
          <template v-if="['completed', 'in_review', 'reviewed'].includes(currentEpisode.status)">
            <RouterLink
              class="btn btn--primary"
              :to="{ name: 'review', params: { id: currentEpisode.run_id } }"
            >
              {{ t.series.openReview }}
            </RouterLink>
            <a class="btn" :href="`/api/runs/${currentEpisode.run_id}/export?format=md`" download>
              {{ t.series.exportMd }}
            </a>
          </template>
        </div>
      </div>
      <AudioPanel
        v-if="currentEpisode.run_id && ['completed', 'in_review', 'reviewed'].includes(currentEpisode.status)"
        :key="currentEpisode.run_id"
        :run-id="currentEpisode.run_id"
        :format-id="audioFormat"
      />
    </section>

    <ModalDialog
      :open="confirmStop"
      :title="t.series.stopTitle"
      :lead="t.series.stopLead"
      @close="confirmStop = false"
    >
      <template #actions>
        <button class="btn btn--ghost" type="button" @click="confirmStop = false">
          {{ t.common.cancel }}
        </button>
        <button
          class="btn btn--mark"
          type="button"
          data-action="confirm-stop"
          :disabled="busy"
          @click="stopSeries"
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
  max-width: 1280px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--s4);
}

.head {
  display: flex;
  gap: var(--s4);
  align-items: flex-start;
  flex-wrap: wrap;
}

.grow {
  flex: 1;
  min-width: 0;
}

.back {
  display: inline-block;
  margin-bottom: 6px;
  text-decoration: none;
  color: var(--ink-3);
}

.wrap {
  flex-wrap: wrap;
}

.btn {
  text-decoration: none;
}

.hint {
  font-size: var(--t-sm);
}

.small {
  font-size: var(--t-sm);
}

.notice {
  padding: var(--s3) var(--s4);
  border-radius: var(--r-md);
  font-size: var(--t-sm);
}

.notice--fail {
  background: var(--fail-soft);
  color: var(--fail);
  border: 1px solid var(--fail);
}

.notice--warn {
  background: var(--warn-soft);
  color: var(--warn);
  border: 1px solid var(--warn);
}

.pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  display: inline-block;
}

.progress {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--s2);
}

.progress > div {
  padding: var(--s2) var(--s3);
  border-radius: var(--r-md);
  background: var(--chrome);
  border: 1px solid var(--rule);
  font-size: var(--t-xs);
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.progress b {
  font-size: var(--t-sm);
  font-weight: 600;
}

.meter {
  height: 4px;
  border-radius: 2px;
  background: var(--sunk);
  overflow: hidden;
}

.meter i {
  display: block;
  height: 100%;
  background: var(--mark);
}

.done .meter i {
  background: var(--pass);
}

.inspector {
  border-top: 1px solid var(--rule);
  padding-top: var(--s4);
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

.switcher {
  display: flex;
  gap: 2px;
  border-bottom: 1px solid var(--rule);
  overflow-x: auto;
}

.switcher button {
  border: 0;
  background: transparent;
  padding: var(--s2) var(--s3) var(--s3);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  min-width: 120px;
  text-align: left;
}

.switcher button b {
  font-size: var(--t-sm);
  font-weight: 600;
  color: var(--ink-2);
}

.switcher button[aria-selected='true'] {
  border-bottom-color: var(--ink);
}

.switcher button[aria-selected='true'] b {
  color: var(--ink);
}

.sub {
  display: flex;
  gap: var(--s1);
  align-items: center;
  flex-wrap: wrap;
}

.stack {
  display: flex;
  flex-direction: column;
  gap: var(--s4);
}

.cov {
  display: flex;
  gap: 2px;
  height: 26px;
  border-radius: var(--r-md);
  overflow: hidden;
}

.cov__part {
  min-width: 0;
  display: flex;
  align-items: center;
  padding: 0 8px;
  font-family: var(--mono);
  font-size: var(--t-xs);
  background: var(--mark-soft);
  color: var(--mark-deep);
  border: 1px solid var(--mark);
}

.cov__part:nth-child(2n) {
  background: var(--mark);
  color: var(--page);
}

.episodes {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--s3);
}

.episode {
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  padding: var(--s4);
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  background: var(--card);
  min-width: 0;
}

.episode h3 {
  font-size: var(--t-md);
  font-weight: 600;
  display: flex;
  gap: var(--s2);
  align-items: center;
}

.epdot {
  display: inline-grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 999px;
  font-family: var(--mono);
  font-size: var(--t-xs);
  background: var(--mark-soft);
  color: var(--mark-deep);
  border: 1px solid var(--mark);
  flex: none;
}

.goals {
  margin: 0;
  padding-left: 1.1em;
  font-size: var(--t-sm);
  color: var(--ink-2);
}

.line {
  font-family: var(--serif);
  font-size: var(--t-sm);
  color: var(--ink-2);
  border-left: 3px solid var(--rule-strong);
  padding-left: var(--s2);
}

.terms {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s2) var(--s4);
  align-items: baseline;
}

.stepper {
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
}

.count {
  font-size: var(--t-lg);
  font-weight: 600;
  min-width: 1.6ch;
  text-align: center;
}

.hint-input {
  max-width: 320px;
}

.minutes-range {
  width: 140px;
}

.got {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--s2);
}

.got > div {
  padding: var(--s2) var(--s3);
  background: var(--chrome);
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  font-size: var(--t-xs);
  color: var(--ink-3);
  display: flex;
  flex-direction: column;
}

.got b {
  font-size: var(--t-sm);
  color: var(--ink);
  font-weight: 600;
}

@media (max-width: 760px) {
  .page {
    padding: var(--s4);
  }

  .progress {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
