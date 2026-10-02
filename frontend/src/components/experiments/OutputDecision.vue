<script setup lang="ts">
/**
 * The decision on a collected output: a status (Kandidat, Gewählt, Verworfen)
 * and one short note, with who set them. No ranking, no scores. Clicking the
 * active status clears it.
 */
import { ref } from 'vue'

import { ApiError } from '@/api/client'
import type { ExperimentOutput, OutputStatus } from '@/api/types'
import { updateOutput } from '@/experiments/api'
import { t } from '@/i18n'

const props = defineProps<{ output: ExperimentOutput<unknown> }>()
const emit = defineEmits<{ (e: 'updated', output: ExperimentOutput<unknown>): void }>()

const labels = t.collection
const STATUSES: OutputStatus[] = ['kandidat', 'gewaehlt', 'verworfen']

const editing = ref(false)
const draft = ref('')
const busy = ref(false)
const error = ref('')

async function save(patch: { status?: OutputStatus | null; note?: string | null }): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    emit('updated', await updateOutput(props.output.id, patch))
    editing.value = false
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

function setStatus(status: OutputStatus): void {
  void save({ status: props.output.status === status ? null : status })
}

function edit(): void {
  draft.value = props.output.note ?? ''
  editing.value = true
}
</script>

<template>
  <div class="decision">
    <p v-if="output.note && !editing" class="notebox">
      <span class="notebox__who">
        {{ labels.note }}<template v-if="output.decided_by"> · {{ output.decided_by }}</template>:
      </span>
      {{ output.note }}
    </p>
    <form v-if="editing" class="noteedit" @submit.prevent="save({ note: draft })">
      <label class="eyebrow" :for="`note-${output.id}`">{{ labels.note }}</label>
      <textarea
        :id="`note-${output.id}`"
        v-model="draft"
        class="textarea"
        maxlength="2000"
        :placeholder="labels.notePlaceholder"
      />
      <div class="row">
        <button class="btn btn--sm btn--primary" type="submit" :disabled="busy">
          {{ t.common.save }}
        </button>
        <button class="btn btn--sm btn--ghost" type="button" @click="editing = false">
          {{ t.common.cancel }}
        </button>
      </div>
    </form>
    <div class="bar">
      <span class="seg" role="group" :aria-label="labels.status">
        <button
          v-for="status in STATUSES"
          :key="status"
          type="button"
          :data-status="status"
          :aria-pressed="output.status === status"
          :disabled="busy"
          @click="setStatus(status)"
        >
          {{ labels.statuses[status] }}
        </button>
      </span>
      <button v-if="!editing" class="btn btn--sm" type="button" @click="edit">
        {{ output.note ? labels.editNote : labels.note }}
      </button>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
/* Its parts join the card foot's row: the note above it, the controls in line. */
.decision {
  display: contents;
}

.notebox,
.noteedit,
.error {
  flex-basis: 100%;
  order: -1;
}

.notebox {
  padding: 6px 10px;
  border-left: 3px solid var(--rule-strong);
  border-radius: 0 var(--r-md) var(--r-md) 0;
  background: var(--chrome);
  font-family: var(--serif);
  font-size: var(--t-sm);
}

.notebox__who {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-3);
  letter-spacing: 0.04em;
}

.noteedit {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.noteedit .textarea {
  min-height: 64px;
}

.row {
  display: flex;
  gap: var(--s2);
}

.bar {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
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

.seg button[aria-pressed='true'][data-status='kandidat'] {
  background: var(--mark-soft);
  color: var(--mark-deep);
}

.seg button[aria-pressed='true'][data-status='gewaehlt'] {
  background: var(--pass-soft);
  color: var(--pass);
}

.seg button[aria-pressed='true'][data-status='verworfen'] {
  background: var(--idle-soft);
  color: var(--idle);
}

.error {
  color: var(--fail);
  font-size: var(--t-sm);
}
</style>
