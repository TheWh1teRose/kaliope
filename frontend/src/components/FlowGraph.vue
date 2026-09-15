<script setup lang="ts">
/**
 * The flow as a chain of typed nodes.
 *
 * A run that fails currently reads as one error string at the top of the page.
 * The graph turns it into a place: the node that failed carries the message,
 * everything downstream is drawn as never reached, and each node names the
 * value it consumed and the value it published, so a wrong output can be
 * traced back to the input that produced it.
 *
 * Laid out as a horizontal chain rather than a free-form canvas because the
 * flow *is* a chain — nodes are wired by field name in declaration order, so
 * drawing arbitrary geometry would suggest a freedom the runner does not have.
 */
import { computed } from 'vue'

import type { PortOut, RunGraphNodeOut } from '@/api/types'
import { t } from '@/i18n'

const props = defineProps<{
  nodes: RunGraphNodeOut[]
  seeds: PortOut[]
  selected?: string | null
  failedNode?: string | null
}>()

const emit = defineEmits<{ (event: 'select', node: string): void }>()

/** Which node published each key, so an input can name where it came from. */
const producers = computed(() => {
  const map = new Map<string, string>()
  for (const node of props.nodes) map.set(node.produces.key, node.name)
  return map
})

const tone: Record<RunGraphNodeOut['status'], string> = {
  pending: 'idle',
  running: 'mark',
  cached: 'pass',
  ok: 'pass',
  failed: 'fail',
  blocked: 'idle',
  paused: 'mark',
}

function worstFinding(node: RunGraphNodeOut): 'fail' | 'warn' | null {
  if (node.findings.some((f) => f.status === 'fail' && f.violations > 0)) return 'fail'
  if (node.findings.some((f) => f.status === 'warn' && f.violations > 0)) return 'warn'
  return null
}

function duration(node: RunGraphNodeOut): string {
  if (node.wall_ms === null) return t.common.none
  return node.wall_ms < 1000 ? `${node.wall_ms} ms` : `${(node.wall_ms / 1000).toFixed(1)} s`
}

function sourceOf(key: string): string {
  const from = producers.value.get(key)
  return from ? `${t.graph.fromNode} ${from}` : t.graph.fromSeed
}
</script>

<template>
  <div class="graph scroll">
    <div class="chain">
      <div class="seedbox">
        <p class="eyebrow">{{ t.graph.seeds }}</p>
        <ul class="ports">
          <li v-for="seed in seeds" :key="seed.key" class="port port--seed">
            <span class="port__key">{{ seed.key }}</span>
            <span class="port__model">{{ seed.model }}</span>
          </li>
        </ul>
      </div>

      <template v-for="node in nodes" :key="node.name">
        <div class="arrow" aria-hidden="true">
          <svg viewBox="0 0 40 12" width="40" height="12">
            <path d="M0 6h32" stroke="currentColor" stroke-width="1.25" />
            <path d="M31 2l6 4-6 4z" fill="currentColor" />
          </svg>
        </div>

        <button
          class="node"
          :class="[
            `node--${tone[node.status]}`,
            {
              'node--on': node.name === selected,
              'node--broke': node.name === failedNode,
            },
          ]"
          :aria-pressed="node.name === selected"
          @click="emit('select', node.name)"
        >
          <header class="node__head">
            <span class="node__name">{{ node.name }}</span>
            <span class="meta">v{{ node.version }}</span>
          </header>

          <span class="badge" :class="`badge--${tone[node.status]}`">
            {{ t.graph.status[node.status] }}
          </span>

          <ul class="ports">
            <li
              v-for="port in node.consumes"
              :key="port.key"
              class="port port--in"
              :title="sourceOf(port.key)"
            >
              <span class="port__arrow" aria-hidden="true">↳</span>
              <span class="port__key">{{ port.key }}</span>
            </li>
            <li class="port port--out" :title="node.produces.model">
              <span class="port__arrow" aria-hidden="true">↦</span>
              <span class="port__key">{{ node.produces.key }}</span>
              <span class="port__model">{{ node.produces.model }}</span>
            </li>
          </ul>

          <dl class="node__facts">
            <div v-if="node.model_id">
              <dt class="meta">{{ t.run.model }}</dt>
              <dd class="truncate">{{ node.model_id }}</dd>
            </div>
            <div v-if="node.tokens_in + node.tokens_out > 0">
              <dt class="meta">{{ t.run.tokens }}</dt>
              <dd class="num">{{ (node.tokens_in + node.tokens_out).toLocaleString('de-DE') }}</dd>
            </div>
            <div v-if="node.cost_usd > 0">
              <dt class="meta">{{ t.common.cost }}</dt>
              <dd class="num">${{ node.cost_usd.toFixed(4) }}</dd>
            </div>
            <div v-if="node.wall_ms !== null">
              <dt class="meta">{{ t.run.duration }}</dt>
              <dd class="num">{{ duration(node) }}</dd>
            </div>
          </dl>

          <p v-if="node.error" class="node__error">{{ node.error.split('\n')[0] }}</p>

          <div v-if="node.findings.length" class="node__gates">
            <span
              v-for="finding in node.findings"
              :key="finding.gate"
              class="gatechip"
              :class="`gatechip--${finding.violations ? finding.status : 'quiet'}`"
              :title="finding.name"
            >
              {{ finding.gate }}<em v-if="finding.violations">{{ finding.violations }}</em>
            </span>
          </div>

          <span v-if="worstFinding(node)" class="node__rule" :class="`node__rule--${worstFinding(node)}`" />
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.graph {
  overflow-x: auto;
  padding-bottom: var(--s2);
}

.chain {
  display: flex;
  align-items: stretch;
  gap: var(--s1);
  min-width: min-content;
}

.arrow {
  display: grid;
  place-items: center;
  color: var(--rule-strong);
  flex: 0 0 auto;
}

.seedbox {
  flex: 0 0 auto;
  align-self: center;
  display: grid;
  gap: var(--s2);
  padding: var(--s3);
  border: 1px dashed var(--rule-strong);
  border-radius: var(--r-lg);
  background: var(--chrome);
  max-width: 190px;
}

.node {
  position: relative;
  flex: 0 0 auto;
  width: 216px;
  display: grid;
  align-content: start;
  gap: var(--s2);
  padding: var(--s3);
  border: 1px solid var(--rule);
  border-top: 3px solid var(--idle);
  border-radius: var(--r-lg);
  background: var(--card);
  text-align: left;
  cursor: pointer;
  transition:
    border-color var(--fast),
    box-shadow var(--fast),
    transform var(--fast);
}

.node:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}

.node--pass {
  border-top-color: var(--pass);
}
.node--fail {
  border-top-color: var(--fail);
}
.node--mark {
  border-top-color: var(--mark);
}
.node--idle {
  border-top-color: var(--rule-strong);
  opacity: 0.72;
}

.node--on {
  box-shadow:
    0 0 0 1px var(--mark),
    var(--shadow-md);
}

.node--broke {
  background: color-mix(in srgb, var(--fail-soft) 45%, var(--card));
}

.node__head {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.node__name {
  font-family: var(--mono);
  font-size: var(--t-sm);
  font-weight: 600;
  color: var(--ink);
}

.node .badge {
  justify-self: start;
}

.ports {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 2px;
}

.port {
  display: flex;
  align-items: baseline;
  gap: 5px;
  font-family: var(--mono);
  font-size: 0.625rem;
  color: var(--ink-3);
  min-width: 0;
}

.port__arrow {
  color: var(--ink-4);
}

.port__key {
  color: var(--ink-2);
}

.port--out .port__key {
  color: var(--mark-deep);
  font-weight: 600;
}

.port__model {
  color: var(--ink-4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node__facts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px var(--s2);
  margin: 0;
  padding-top: var(--s2);
  border-top: 1px solid var(--rule);
}

.node__facts dt {
  font-size: 0.5625rem;
}

.node__facts dd {
  margin: 0;
  font-size: var(--t-xs);
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
}

.node__error {
  font-size: var(--t-xs);
  color: var(--fail);
  background: var(--fail-soft);
  padding: var(--s2);
  border-radius: var(--r-sm);
  overflow-wrap: anywhere;
}

.node__gates {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
}

.gatechip {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
  padding: 1px 5px;
  border-radius: 999px;
  font-family: var(--mono);
  font-size: 0.5625rem;
  background: var(--sunk);
  color: var(--ink-3);
}

.gatechip em {
  font-style: normal;
  font-weight: 700;
}

.gatechip--fail {
  background: var(--fail-soft);
  color: var(--fail);
}

.gatechip--warn {
  background: var(--warn-soft);
  color: var(--warn);
}

.node__rule {
  position: absolute;
  inset: auto 0 0 0;
  height: 2px;
  border-radius: 0 0 var(--r-lg) var(--r-lg);
}

.node__rule--fail {
  background: var(--fail);
}

.node__rule--warn {
  background: var(--warn);
}
</style>
