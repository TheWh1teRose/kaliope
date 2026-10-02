<script setup lang="ts">
/**
 * "Gesammelte Ausgaben": the collected outputs of one experiment, newest
 * first, readable as text or JSON. No ranking, no scores: read, adopt a
 * setup, file into a Sammlung folder, or delete. The `text` slot renders an
 * output's Text view.
 */
import { ref } from 'vue'
import { RouterLink } from 'vue-router'

import type { ExperimentOutput } from '@/api/types'
import MoveToFolderDialog from '@/components/experiments/MoveToFolderDialog.vue'
import OutputCard, { type OutputView } from '@/components/experiments/OutputCard.vue'
import { moveOutputs } from '@/experiments/api'
import { type Badge, outputBadges, outputFacts, outputWarnings } from '@/experiments/outputMeta'
import { t } from '@/i18n'

withDefaults(
  defineProps<{
    outputs: ExperimentOutput<unknown>[]
    /** Shows "In der Sammlung ansehen", filtered to this experiment. */
    experimentKey?: string
    /** Which part of an output is the answer to render. */
    payloadOf?: (output: ExperimentOutput<unknown>) => unknown
    /** Card title; defaults to the label. */
    titleOf?: (output: ExperimentOutput<unknown>) => string
    /** Card badges; defaults to the run's settings. */
    badgesOf?: (output: ExperimentOutput<unknown>) => Badge[]
  }>(),
  {
    payloadOf: (output: ExperimentOutput<unknown>) => output.output?.payload ?? null,
    titleOf: (output: ExperimentOutput<unknown>) => output.label || t.experiments.unnamed,
    badgesOf: (output: ExperimentOutput<unknown>) => outputBadges(output.meta),
  },
)
const emit = defineEmits<{
  (e: 'adopt', output: ExperimentOutput<unknown>): void
  (e: 'delete', output: ExperimentOutput<unknown>): void
  /** An output was filed into another folder; reload the list. */
  (e: 'moved'): void
  (e: 'error', message: string): void
}>()

const moving = ref<ExperimentOutput<unknown> | null>(null)

async function file(folderId: string | null): Promise<void> {
  const output = moving.value
  moving.value = null
  if (!output) return
  try {
    await moveOutputs([output.id], folderId)
    emit('moved')
  } catch (exc) {
    emit('error', exc instanceof Error ? exc.message : t.errors.generic)
  }
}

const view = ref<OutputView>('text')

function when(value: string): string {
  return new Date(value).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}

function remove(output: ExperimentOutput<unknown>): void {
  if (window.confirm(t.experiments.deleteConfirm)) emit('delete', output)
}

</script>

<template>
  <section class="collection">
    <div class="listhead">
      <p class="eyebrow grow">{{ t.experiments.collection }} · {{ outputs.length }}</p>
      <RouterLink
        v-if="experimentKey"
        class="btn btn--sm btn--ghost"
        :to="{ name: 'experiments', query: { tab: 'collection', experiment: experimentKey } }"
      >
        {{ t.collection.viewInCollection }} →
      </RouterLink>
      <span class="meta">{{ t.experiments.showAs }}</span>
      <span class="seg" role="group">
        <button :aria-pressed="view === 'text'" @click="view = 'text'">{{ t.experiments.text }}</button>
        <button :aria-pressed="view === 'json'" @click="view = 'json'">{{ t.experiments.json }}</button>
      </span>
    </div>
    <slot name="meta" />
    <p v-if="!outputs.length" class="muted small">{{ t.experiments.collectionEmpty }}</p>
    <OutputCard
      v-for="output in outputs"
      :key="output.id"
      :title="titleOf(output)"
      :payload="payloadOf(output)"
      :text="output.text"
      :badges="badgesOf(output)"
      :facts="outputFacts(output.meta)"
      :warnings="outputWarnings(output.meta)"
      :view="view"
      collapsible
    >
      <template #head>
        <button
          class="folderchip"
          :title="t.collection.moveTitle"
          @click="moving = output"
        >
          {{ output.folder_path.length ? `▸ ${output.folder_path.join(' / ')}` : t.collection.root }}
        </button>
        <span class="meta">{{ when(output.created_at) }}</span>
      </template>
      <template v-if="$slots.text" #text>
        <slot name="text" :output="output" />
      </template>
      <template #foot>
        <button class="btn btn--sm" @click="emit('adopt', output)">{{ t.experiments.adopt }}</button>
        <button class="btn btn--sm" @click="moving = output">{{ t.collection.moveTo }}</button>
        <span class="grow" />
        <button class="btn btn--ghost btn--sm btn--danger" @click="remove(output)">
          {{ t.experiments.delete }}
        </button>
      </template>
    </OutputCard>
    <MoveToFolderDialog
      :open="Boolean(moving)"
      :count="1"
      :current="moving?.folder_id ?? null"
      @close="moving = null"
      @move="file"
    />
  </section>
</template>

<style scoped>
.collection {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

.listhead {
  display: flex;
  align-items: center;
  gap: var(--s3);
  flex-wrap: wrap;
  margin-top: var(--s5);
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

a.btn {
  text-decoration: none;
}

.folderchip {
  display: inline-flex;
  align-items: center;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 1px 7px;
  border: 1px dashed var(--rule-strong);
  border-radius: 999px;
  background: transparent;
  color: var(--ink-2);
  font-family: var(--mono);
  font-size: var(--t-xs);
  cursor: pointer;
}

.folderchip:hover {
  border-color: var(--mark);
  color: var(--mark-deep);
}
</style>
