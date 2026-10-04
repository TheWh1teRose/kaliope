<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError } from '@/api/client'
import type { SeriesOut } from '@/api/types'
import ModalDialog from '@/components/ModalDialog.vue'
import StatusPill from '@/components/StatusPill.vue'
import { fill, t } from '@/i18n'
import { runTitle, seriesTitle } from '@/titles'
import { useRunsStore } from '@/stores/runs'
import { useSeriesStore } from '@/stores/series'

/** How often the list asks again while a run or series is still moving. */
const POLL_MS = 4000
const LIVE_RUN = new Set(['queued', 'running', 'outlined'])
const STOPPABLE_RUN = new Set(['queued', 'running'])
const LIVE_SERIES = new Set(['queued', 'planning', 'outlining', 'writing'])

const runs = useRunsStore()
const seriesStore = useSeriesStore()
const series = ref<SeriesOut[]>([])
const confirmStop = ref<{ kind: 'run' | 'series'; id: string } | null>(null)
const stopping = ref(false)
const stopError = ref('')
let disposed = false
let timer: ReturnType<typeof setInterval> | null = null

function when(value: string): string {
  return new Date(value).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}

async function load(): Promise<void> {
  await runs.load()
  series.value = await seriesStore.list().catch(() => [])
  const moving =
    runs.items.some((run) => LIVE_RUN.has(run.status) || run.active) ||
    series.value.some((row) => LIVE_SERIES.has(row.status) || row.active)
  if (!disposed && moving && !timer) timer = setInterval(load, POLL_MS)
  if (!moving && timer) {
    clearInterval(timer)
    timer = null
  }
}

function askStop(kind: 'run' | 'series', id: string): void {
  stopError.value = ''
  confirmStop.value = { kind, id }
}

async function confirm(): Promise<void> {
  const target = confirmStop.value
  if (!target || stopping.value) return
  stopping.value = true
  stopError.value = ''
  try {
    if (target.kind === 'run') await runs.stop(target.id)
    else await seriesStore.stop(target.id)
    confirmStop.value = null
    await load()
  } catch (exc) {
    stopError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    stopping.value = false
  }
}

onMounted(load)
onUnmounted(() => {
  disposed = true
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="page">
    <header>
      <p class="eyebrow">{{ t.app.name }}</p>
      <h1 class="h-page">{{ t.nav.runs }}</h1>
    </header>

    <table v-if="series.length" class="runs">
      <thead>
        <tr>
          <th>{{ t.series.title }}</th>
          <th>{{ t.run.flow }}</th>
          <th>Status</th>
          <th class="right">{{ t.common.cost }}</th>
          <th class="right">{{ t.series.count }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in series" :key="row.id">
          <td>
            <RouterLink :to="{ name: 'series', params: { id: row.id } }" class="link">
              {{ seriesTitle(row) }}
            </RouterLink>
            <span class="meta block">{{ row.document_title }} · {{ when(row.created_at) }}</span>
          </td>
          <td class="meta">{{ row.flow_id }} v{{ row.flow_version }}</td>
          <td>
            <span
              class="badge"
              :class="
                row.status === 'failed'
                  ? 'badge--fail'
                  : row.status === 'completed'
                    ? 'badge--pass'
                    : row.status === 'stopped'
                      ? 'badge--idle'
                      : 'badge--mark'
              "
            >
              {{ t.series.status[row.status] }}
            </span>
            <button
              v-if="LIVE_SERIES.has(row.status)"
              class="btn btn--sm"
              type="button"
              data-stop-series
              @click="askStop('series', row.id)"
            >
              {{ t.run.stop }}
            </button>
          </td>
          <td class="right num">${{ row.total_cost_usd.toFixed(4) }}</td>
          <td class="right num">{{ row.episodes.length || row.request.episodes || t.common.none }}</td>
        </tr>
      </tbody>
    </table>

    <p v-if="!runs.items.length" class="muted empty">{{ t.run.empty }}</p>

    <table v-else class="runs">
      <thead>
        <tr>
          <th>{{ t.run.document }}</th>
          <th>{{ t.run.flow }}</th>
          <th>Status</th>
          <th class="right">{{ t.common.cost }}</th>
          <th class="right">{{ t.run.targetMinutes }}</th>
          <th class="right">{{ t.common.page }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="run in runs.items" :key="run.id">
          <td>
            <RouterLink :to="{ name: 'run', params: { id: run.id } }" class="link">
              {{ runTitle(run) }}
            </RouterLink>
            <span class="meta block">
              <template v-if="run.name && run.document_title">{{ run.document_title }} · </template>{{ when(run.created_at) }}
            </span>
            <RouterLink
              v-if="run.series_id && run.episode_index"
              class="badge badge--mark series-badge"
              :to="{ name: 'series', params: { id: run.series_id } }"
            >
              {{ fill(t.run.seriesBadge, { n: run.episode_index }) }}
            </RouterLink>
          </td>
          <td class="meta">{{ run.flow_id }} v{{ run.flow_version }}</td>
          <td>
            <StatusPill :status="run.status" />
            <button
              v-if="STOPPABLE_RUN.has(run.status)"
              class="btn btn--sm"
              type="button"
              data-stop
              @click="askStop('run', run.id)"
            >
              {{ t.run.stop }}
            </button>
          </td>
          <td class="right num">${{ run.total_cost_usd.toFixed(4) }}</td>
          <td class="right num">{{ run.target_minutes ?? t.common.none }}</td>
          <td class="right">
            <RouterLink
              v-if="['completed', 'in_review', 'reviewed'].includes(run.status)"
              class="btn btn--sm"
              :to="{ name: 'review', params: { id: run.id } }"
            >
              {{ t.review.title }}
            </RouterLink>
          </td>
        </tr>
      </tbody>
    </table>

    <ModalDialog
      :open="confirmStop !== null"
      :title="confirmStop?.kind === 'series' ? t.series.stopTitle : t.run.stopTitle"
      :lead="confirmStop?.kind === 'series' ? t.series.stopLead : t.run.stopLead"
      @close="confirmStop = null"
    >
      <p v-if="stopError" class="notice notice--fail" role="alert">{{ stopError }}</p>
      <template #actions>
        <button class="btn btn--ghost" type="button" @click="confirmStop = null">
          {{ t.common.cancel }}
        </button>
        <button
          class="btn btn--mark"
          type="button"
          data-action="confirm-stop"
          :disabled="stopping"
          @click="confirm"
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
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--s5);
}

.runs {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--t-sm);
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  overflow: hidden;
}

.runs th {
  text-align: left;
  font-family: var(--mono);
  font-size: 0.625rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--ink-3);
  font-weight: 500;
  padding: var(--s3) var(--s4);
  background: var(--chrome);
  border-bottom: 1px solid var(--rule);
}

.runs td {
  padding: var(--s3) var(--s4);
  border-bottom: 1px solid var(--rule);
  vertical-align: middle;
}

.runs tbody tr:last-child td {
  border-bottom: 0;
}

.runs tbody tr:hover {
  background: var(--chrome);
}

.right {
  text-align: right;
}

.link {
  color: var(--ink);
  font-weight: 550;
  text-decoration: none;
}

.link:hover {
  color: var(--mark);
}

.block {
  display: block;
  margin-top: 2px;
}

.btn {
  text-decoration: none;
}

.series-badge {
  text-decoration: none;
  margin-top: 4px;
}

.empty {
  padding: var(--s8);
  text-align: center;
}
</style>
