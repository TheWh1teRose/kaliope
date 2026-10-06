<script setup lang="ts">
/**
 * Where an experiment's fields come from: the loaded run (and beat), or the
 * sample. "Aus Lauf laden" picks a finished run and, when the experiment
 * works on one beat, one of its beats, and hands the loaded fields to the
 * page, which fills its field cards with them.
 */
import { ref } from 'vue'

import { api } from '@/api/client'
import type { ExperimentSource, ExperimentSourceMeta, RunOut } from '@/api/types'
import ModalDialog from '@/components/ModalDialog.vue'
import { loadSource } from '@/experiments/api'
import { t } from '@/i18n'
import { runTitle, sourceTitle } from '@/titles'

const FINISHED_RUN = new Set(['completed', 'in_review', 'reviewed', 'failed', 'stopped', 'paused'])

const props = defineProps<{
  experimentKey: string
  source: ExperimentSourceMeta | null
  /** Overrides the beat-shaped wording for experiments that load a whole run. */
  lead?: string
  sampleLabel?: string
  selectionLabel?: string
  resetLabel?: string
  hideWordBudget?: boolean
}>()
const emit = defineEmits<{
  (e: 'loaded', value: ExperimentSource): void
  (e: 'sample'): void
  (e: 'error', value: unknown): void
}>()

const labels = t.experiments
const picking = ref(false)
const finishedRuns = ref<RunOut[]>([])
const pickedRun = ref('')
const pickedSource = ref<ExperimentSource | null>(null)
const pickedBeat = ref('')
const pickBusy = ref(false)

async function openPicker(): Promise<void> {
  picking.value = true
  pickedSource.value = null
  pickedRun.value = ''
  pickedBeat.value = ''
  try {
    const runs = await api.get<RunOut[]>('/api/runs')
    finishedRuns.value = runs.filter((run) => FINISHED_RUN.has(run.status))
  } catch (exc) {
    emit('error', exc)
  }
}

async function pickRun(id: string): Promise<void> {
  pickedRun.value = id
  pickBusy.value = true
  try {
    pickedSource.value = await loadSource(props.experimentKey, id)
    pickedBeat.value = pickedSource.value.source.beat_id ?? ''
  } catch (exc) {
    pickedSource.value = null
    emit('error', exc)
  } finally {
    pickBusy.value = false
  }
}

async function applyPicked(): Promise<void> {
  if (!pickedRun.value) return
  pickBusy.value = true
  try {
    const loaded = await loadSource(props.experimentKey, pickedRun.value, pickedBeat.value || null)
    picking.value = false
    emit('loaded', loaded)
  } catch (exc) {
    emit('error', exc)
  } finally {
    pickBusy.value = false
  }
}
</script>

<template>
  <div class="source">
    <div class="grow">
      <p class="eyebrow">{{ labels.source }}</p>
      <p v-if="source?.beat_title" class="small">
        <strong>{{ sourceTitle(source) }}</strong> · {{ labels.sourceBeat }}
        {{ source.beat_position }}/{{ source.beat_total }}: <em>{{ source.beat_title }}</em>
      </p>
      <p v-else-if="source?.run_id" class="small">
        <strong>{{ sourceTitle(source) }}</strong> · {{ labels.sourceRun }}
      </p>
      <p v-else class="small muted">{{ sampleLabel ?? labels.sourceSample }}</p>
    </div>
    <div class="row wrap">
      <button class="btn btn--sm" @click="openPicker">{{ labels.loadFromRun }}</button>
      <button v-if="source" class="btn btn--ghost btn--sm" @click="emit('sample')">
        {{ resetLabel ?? labels.sample }}
      </button>
    </div>
  </div>

  <ModalDialog
    :open="picking"
    :title="labels.loadFromRun"
    :lead="lead ?? labels.loadFromRunLead"
    @close="picking = false"
  >
    <p class="eyebrow">{{ labels.pickRun }}</p>
    <p v-if="!finishedRuns.length" class="muted small">{{ labels.noRuns }}</p>
    <ul class="pick">
      <li v-for="item in finishedRuns" :key="item.id">
        <button
          class="pick__item"
          :class="{ 'pick__item--on': pickedRun === item.id }"
          @click="pickRun(item.id)"
        >
          <span>{{ runTitle(item) }}</span>
          <span class="meta">{{ item.flow_id }} · {{ new Date(item.created_at).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }) }}</span>
        </button>
      </li>
    </ul>
    <template v-if="pickedSource?.beats.length">
      <p class="eyebrow pick__label">{{ selectionLabel ?? labels.pickBeat }}</p>
      <ul class="pick">
        <li v-for="beat in pickedSource.beats" :key="beat.id">
          <button
            class="pick__item"
            :class="{ 'pick__item--on': pickedBeat === beat.id }"
            @click="pickedBeat = beat.id"
          >
            <span>{{ beat.title }}</span>
            <span v-if="!hideWordBudget" class="meta">{{ beat.word_budget }} {{ t.common.words }}</span>
          </button>
        </li>
      </ul>
    </template>
    <div class="row pick__foot">
      <span class="grow" />
      <button class="btn" @click="picking = false">{{ t.common.cancel }}</button>
      <button class="btn btn--primary" :disabled="!pickedSource || pickBusy" @click="applyPicked">
        {{ labels.fillFields }}
      </button>
    </div>
  </ModalDialog>
</template>

<style scoped>
.source {
  display: flex;
  gap: var(--s3);
  flex-wrap: wrap;
  align-items: center;
  padding: var(--s3) var(--s4);
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
}

.small {
  font-size: var(--t-sm);
}

.pick {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: var(--s2) 0 0;
  padding: 0;
  list-style: none;
}

.pick__item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  background: var(--card);
  text-align: left;
  cursor: pointer;
}

.pick__item:hover,
.pick__item--on {
  border-color: var(--mark);
  background: var(--mark-soft);
}

.pick__label {
  margin-top: var(--s4);
}

.pick__foot {
  margin-top: var(--s4);
}
</style>
