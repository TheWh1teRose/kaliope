<script setup lang="ts">
/**
 * One value in the bench bag: summary, preview, and an editable JSON body.
 *
 * Large artifacts (a full parse) stay behind their hash until someone asks to
 * edit them, so opening the bench over a real document does not dump megabytes
 * into a textarea.
 */
import { computed, ref, watch } from 'vue'

import type { BenchValueOut } from '@/api/types'
import { t } from '@/i18n'

const props = defineProps<{
  value: BenchValueOut
  text: string
  dirty: boolean
  jsonError: string
  open: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle'): void
  (e: 'edit', text: string): void
  (e: 'apply'): void
  (e: 'revert'): void
  (e: 'loadFull'): void
}>()

const draft = ref(props.text)
watch(
  () => props.text,
  (next) => {
    if (!props.dirty) draft.value = next
  },
)

const sourceLabel = computed(() => {
  switch (props.value.source) {
    case 'document':
      return t.bench.fromDocument
    case 'run':
      return t.bench.fromRun
    case 'bench':
      return t.bench.fromBench
    case 'format':
      return t.bench.fromFormat
    case 'produced':
      return t.bench.produced
    case 'edited':
      return t.bench.edited
    default:
      return t.bench.fromSeed
  }
})

function summaryRows(): { key: string; text: string }[] {
  return Object.entries(props.value.summary ?? {}).map(([key, raw]) => ({
    key,
    text: describe(raw),
  }))
}

function describe(raw: unknown): string {
  if (raw === null || raw === undefined) return t.common.none
  if (typeof raw === 'object') {
    const record = raw as Record<string, unknown>
    if ('count' in record) return `${record.count}`
    if ('size' in record) return `${record.size}`
    return JSON.stringify(raw).slice(0, 120)
  }
  return String(raw).slice(0, 120)
}

function onInput(event: Event): void {
  draft.value = (event.target as HTMLTextAreaElement).value
  emit('edit', draft.value)
}
</script>

<template>
  <article class="card value" :class="{ 'value--open': open, 'value--dirty': dirty }">
    <button class="value__head" :aria-expanded="open" @click="emit('toggle')">
      <code class="value__key">{{ value.key }}</code>
      <span class="value__model meta truncate">{{ value.model }}</span>
      <span class="grow" />
      <span v-if="dirty" class="badge badge--warn">{{ t.bench.edited }}</span>
      <span v-else-if="value.available" class="badge badge--idle">{{ sourceLabel }}</span>
      <span v-else class="badge badge--fail">{{ t.bench.empty }}</span>
      <span class="chev" aria-hidden="true">{{ open ? '−' : '+' }}</span>
    </button>

    <div v-if="open" class="value__body">
      <p v-if="!value.available && !dirty" class="muted small">{{ t.bench.emptyHint }}</p>

      <ul v-if="summaryRows().length" class="kv">
        <li v-for="row in summaryRows()" :key="row.key">
          <span class="kv__key">{{ row.key }}</span>
          <span class="kv__value mono">{{ row.text }}</span>
        </li>
      </ul>

      <p v-if="value.truncated && !dirty" class="muted small">{{ t.graph.truncated }}</p>

      <textarea
        class="textarea json"
        spellcheck="false"
        rows="14"
        :value="draft"
        :placeholder="t.bench.emptyHint"
        @input="onInput"
      />
      <p v-if="jsonError" class="err">{{ t.bench.jsonInvalid }} {{ jsonError }}</p>

      <div class="row wrap">
        <button
          v-if="value.truncated && value.artifact_hash"
          class="btn btn--sm"
          @click="emit('loadFull')"
        >
          {{ t.bench.loadFull }}
        </button>
        <button v-if="dirty" class="btn btn--sm btn--primary" @click="emit('apply')">
          {{ t.bench.applyEdit }}
        </button>
        <button v-if="dirty" class="btn btn--sm" @click="emit('revert')">
          {{ t.bench.revert }}
        </button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.value__head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  width: 100%;
  padding: var(--s3);
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.value--open .value__head {
  border-bottom: 1px solid var(--rule);
}

.value--dirty {
  box-shadow: inset 3px 0 0 var(--warn);
}

.value__key {
  font-size: var(--t-sm);
}

.value__model {
  max-width: 140px;
}

.chev {
  color: var(--ink-4);
  width: 1em;
  text-align: center;
}

.value__body {
  display: grid;
  gap: var(--s3);
  padding: var(--s3);
}

.kv {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 4px;
}

.kv li {
  display: grid;
  grid-template-columns: minmax(80px, 28%) 1fr;
  gap: var(--s2);
  font-size: var(--t-xs);
}

.kv__key {
  color: var(--ink-3);
  font-family: var(--mono);
}

.kv__value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.json {
  font-family: var(--mono);
  font-size: var(--t-xs);
  line-height: 1.45;
  min-height: 220px;
  max-width: 100%;
  background: var(--sunk);
  overflow-wrap: anywhere;
  word-break: break-word;
}

.err {
  color: var(--fail);
  font-size: var(--t-xs);
}

.small {
  font-size: var(--t-xs);
}

.mono {
  font-family: var(--mono);
}
</style>
