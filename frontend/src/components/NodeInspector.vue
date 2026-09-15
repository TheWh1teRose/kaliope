<script setup lang="ts">
/**
 * One node's actual inputs and output in one run.
 *
 * The point is provenance: each input names the node that published it and the
 * artifact hash it resolved to, so "this output is wrong" can be walked back to
 * "because this input was". The preview is deliberately the raw artifact JSON —
 * a prettified rendering would be a second interpretation of the data, and the
 * thing being debugged is the data.
 */
import { computed, ref } from 'vue'

import type { NodeIOOut, NodeValueOut } from '@/api/types'
import { t } from '@/i18n'

const props = defineProps<{ io: NodeIOOut | null; loading?: boolean }>()

const openKey = ref<string | null>(null)

const values = computed<NodeValueOut[]>(() =>
  props.io ? [...props.io.inputs, ...(props.io.output ? [props.io.output] : [])] : [],
)

function toggle(key: string): void {
  openKey.value = openKey.value === key ? null : key
}

function origin(value: NodeValueOut): string {
  return value.produced_by ? `${t.graph.fromNode} ${value.produced_by}` : t.graph.fromSeed
}

function summaryRows(value: NodeValueOut): { key: string; text: string }[] {
  return Object.entries(value.summary).map(([key, raw]) => ({ key, text: describe(raw) }))
}

/** One line per field: a count for a collection, the value for a scalar. */
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

function configRows(config: Record<string, unknown>): { key: string; text: string }[] {
  return Object.entries(config).map(([key, raw]) => ({ key, text: describe(raw) }))
}
</script>

<template>
  <div v-if="loading" class="muted pad">{{ t.common.loading }}</div>

  <div v-else-if="io" class="inspector">
    <dl class="facts">
      <div>
        <dt class="meta">{{ t.run.node }}</dt>
        <dd class="mono">{{ io.node_name }} v{{ io.node_version }}</dd>
      </div>
      <div>
        <dt class="meta">{{ t.run.cacheHit }}</dt>
        <dd>{{ io.cache_hit ? t.run.cached : t.run.computed }}</dd>
      </div>
      <div v-if="io.model_id">
        <dt class="meta">{{ t.run.model }}</dt>
        <dd class="mono truncate">{{ io.model_id }}</dd>
      </div>
      <div v-if="io.tokens_in + io.tokens_out > 0">
        <dt class="meta">{{ t.run.tokens }}</dt>
        <dd class="num">
          {{ io.tokens_in.toLocaleString('de-DE') }} / {{ io.tokens_out.toLocaleString('de-DE') }}
        </dd>
      </div>
      <div v-if="io.cost_usd > 0">
        <dt class="meta">{{ t.common.cost }}</dt>
        <dd class="num">${{ io.cost_usd.toFixed(4) }}</dd>
      </div>
      <div v-if="io.cache_key">
        <dt class="meta">{{ t.graph.cacheKey }}</dt>
        <dd class="mono truncate">{{ io.cache_key.slice(0, 16) }}…</dd>
      </div>
    </dl>

    <p v-if="io.error" class="trace">{{ io.error }}</p>

    <section v-if="configRows(io.config).length" class="block">
      <p class="eyebrow">{{ t.graph.config }}</p>
      <ul class="kv">
        <li v-for="row in configRows(io.config)" :key="row.key">
          <span class="kv__key">{{ row.key }}</span>
          <span class="kv__value mono">{{ row.text }}</span>
        </li>
      </ul>
    </section>

    <section class="block">
      <p class="eyebrow">{{ t.graph.inputs }}</p>
      <p v-if="!io.inputs.length" class="muted small">{{ t.common.none }}</p>
    </section>

    <article
      v-for="value in values"
      :key="value.key"
      class="value"
      :class="{ 'value--out': value.key === io.output?.key && value.produced_by === io.node_name }"
    >
      <button class="value__head" :aria-expanded="openKey === value.key" @click="toggle(value.key)">
        <span class="value__key mono">{{ value.key }}</span>
        <span class="value__model meta truncate">{{ value.model }}</span>
        <span class="grow" />
        <span class="meta">{{ origin(value) }}</span>
        <span v-if="value.artifact_hash" class="hash mono">{{ value.artifact_hash.slice(0, 10) }}</span>
        <span class="chev" aria-hidden="true">{{ openKey === value.key ? '−' : '+' }}</span>
      </button>

      <div v-if="openKey === value.key" class="value__body">
        <p v-if="!value.available" class="muted small">{{ t.graph.unavailable }}</p>
        <template v-else>
          <ul v-if="summaryRows(value).length" class="kv">
            <li v-for="row in summaryRows(value)" :key="row.key">
              <span class="kv__key">{{ row.key }}</span>
              <span class="kv__value mono">{{ row.text }}</span>
            </li>
          </ul>
          <pre v-if="value.preview" class="json scroll">{{ value.preview }}</pre>
          <p v-if="value.truncated" class="muted small">{{ t.graph.truncated }}</p>
        </template>
      </div>
    </article>
  </div>
</template>

<style scoped>
.inspector {
  display: grid;
  gap: var(--s3);
}

.facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: var(--s3);
  margin: 0;
  padding-bottom: var(--s3);
  border-bottom: 1px solid var(--rule);
}

.facts dd {
  margin: 2px 0 0;
  font-size: var(--t-sm);
  min-width: 0;
}

.mono {
  font-family: var(--mono);
}

.block {
  display: grid;
  gap: var(--s2);
}

.kv {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 1px;
}

.kv li {
  display: flex;
  align-items: baseline;
  gap: var(--s3);
  padding: 3px var(--s2);
  border-radius: var(--r-sm);
  font-size: var(--t-xs);
}

.kv li:nth-child(odd) {
  background: var(--chrome);
}

.kv__key {
  color: var(--ink-3);
  min-width: 12ch;
}

.kv__value {
  color: var(--ink);
  overflow-wrap: anywhere;
}

.value {
  border: 1px solid var(--rule);
  border-left: 3px solid var(--rule-strong);
  border-radius: 0 var(--r-md) var(--r-md) 0;
  background: var(--card);
  overflow: hidden;
}

.value--out {
  border-left-color: var(--mark);
}

.value__head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  width: 100%;
  padding: 7px var(--s3);
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.value__head:hover {
  background: var(--chrome);
}

.value__key {
  font-size: var(--t-sm);
  font-weight: 600;
  color: var(--ink);
}

.value__model {
  min-width: 0;
}

.hash {
  font-size: 0.625rem;
  color: var(--ink-4);
}

.chev {
  font-family: var(--mono);
  color: var(--ink-3);
  width: 12px;
  text-align: center;
}

.value__body {
  display: grid;
  gap: var(--s2);
  padding: 0 var(--s3) var(--s3);
}

.json {
  margin: 0;
  padding: var(--s3);
  max-height: 340px;
  border-radius: var(--r-md);
  background: var(--sunk);
  font-family: var(--mono);
  font-size: var(--t-xs);
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.trace {
  padding: var(--s3);
  border-radius: var(--r-md);
  background: var(--fail-soft);
  color: var(--fail);
  font-family: var(--mono);
  font-size: var(--t-xs);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 220px;
  overflow: auto;
}

.small {
  font-size: var(--t-sm);
}

.pad {
  padding: var(--s5);
  text-align: center;
}
</style>
