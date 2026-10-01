<script setup lang="ts">
/**
 * The readable (Text) view of an answer: dialogue for speaker/text lists,
 * labelled fields for the rest. See `experiments/outputText.ts`.
 */
import { computed } from 'vue'

import { textBlocks } from '@/experiments/outputText'
import { t } from '@/i18n'

const props = defineProps<{ payload: unknown; text?: string | null }>()

const blocks = computed(() => textBlocks(props.payload, props.text ?? ''))

function kindLabel(kind: string): string {
  if (kind === 'claim') return t.experiments.claim
  if (kind === 'pedagogy') return t.experiments.pedagogy
  return kind
}
</script>

<template>
  <div class="text-view">
    <p v-if="!blocks.length" class="muted small">{{ t.experiments.noAnswer }}</p>
    <template v-for="(block, index) in blocks" :key="index">
      <p v-if="block.type === 'prose'" class="prose prose--plain">{{ block.text }}</p>
      <div v-else-if="block.type === 'field'" class="kv">
        <p class="kv__k">{{ block.key }}</p>
        <p class="kv__v">{{ block.value }}</p>
      </div>
      <div v-else-if="block.type === 'json'" class="kv">
        <p v-if="block.key" class="kv__k">{{ block.key }}</p>
        <pre class="json">{{ block.json }}</pre>
      </div>
      <div v-else class="dialog">
        <div v-for="(line, li) in block.lines" :key="li" class="line">
          <span class="line__who">{{ line.speaker }}</span>
          <div>
            <p class="line__text">{{ line.text }}</p>
            <p v-if="line.kind || line.citations.length" class="line__meta">
              <span
                v-if="line.kind"
                class="badge"
                :class="line.kind === 'claim' ? 'badge--mark' : 'badge--idle'"
              >
                {{ kindLabel(line.kind) }}
              </span>
              <span
                v-for="(cite, ci) in line.citations"
                :key="ci"
                class="cite"
                :title="cite.quote"
              >[{{ cite.blockId }}]</span>
            </p>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.text-view {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

.prose--plain {
  white-space: pre-wrap;
}

.kv__k {
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--ink-3);
}

.kv__v {
  font-family: var(--serif);
  font-size: 1rem;
  line-height: 1.55;
  white-space: pre-wrap;
}

.dialog {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.line {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: var(--s3);
}

.line__who {
  padding-top: 4px;
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--ink-3);
  overflow-wrap: anywhere;
}

.line__text {
  font-family: var(--serif);
  font-size: 1rem;
  line-height: 1.6;
}

.line__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 2px;
}

.cite {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--mark-deep);
}

.json {
  margin: 0;
  padding: var(--s3);
  background: var(--sunk);
  border-radius: var(--r-md);
  font-family: var(--mono);
  font-size: 0.74rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}

.small {
  font-size: var(--t-sm);
}

@media (max-width: 920px) {
  .line {
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
  }
}
</style>
