<script setup lang="ts">
/**
 * "Gesammelte Ausgaben": the collected outputs of one experiment, newest
 * first, readable as text or JSON. No ranking, no scores: read, adopt a
 * setup, or delete. The `text` slot renders an output's Text view.
 */
import { ref } from 'vue'

import type { ExperimentOutput } from '@/api/types'
import OutputCard, { type OutputView } from '@/components/experiments/OutputCard.vue'
import { type Badge, outputBadges, outputFacts, outputWarnings } from '@/experiments/outputMeta'
import { t } from '@/i18n'

withDefaults(
  defineProps<{
    outputs: ExperimentOutput<unknown>[]
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
}>()

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
        <span class="meta">{{ when(output.created_at) }}</span>
      </template>
      <template v-if="$slots.text" #text>
        <slot name="text" :output="output" />
      </template>
      <template #foot>
        <button class="btn btn--sm" @click="emit('adopt', output)">{{ t.experiments.adopt }}</button>
        <span class="grow" />
        <button class="btn btn--ghost btn--sm btn--danger" @click="remove(output)">
          {{ t.experiments.delete }}
        </button>
      </template>
    </OutputCard>
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
</style>
