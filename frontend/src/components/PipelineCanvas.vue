<script setup lang="ts">
/**
 * The whole pipeline of a series on one pannable surface: the planner's nodes
 * in a column, one lane per episode. "Kompakt" shows each node's state, cost,
 * tokens and duration; "Ausführlich" adds its inputs and output like the run
 * page's flow chart. A node shows what it is doing right now when the series'
 * event stream reports progress for it.
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import type { NodeRunStatus, RunGraphNodeOut, SeriesGraphOut } from '@/api/types'
import { fill, t } from '@/i18n'
import {
  type CanvasMode,
  fitView,
  GEOMETRY,
  layoutCanvas,
  type PlacedNode,
  SERIES_INPUTS,
  zoomAt,
} from '@/series/canvas'

const props = defineProps<{
  graph: SeriesGraphOut
  selectedEpisode?: number | null
  selectedKey?: string | null
  /** `${runId}:${node}` → what the node is doing right now, from the event stream. */
  live?: Record<string, string>
  /** Episode index → why its next node waits, e.g. "wartet auf Folge 1". */
  waiting?: Record<number, string>
}>()

const emit = defineEmits<{
  (event: 'select', node: PlacedNode): void
  (event: 'lane', episode: number): void
}>()

const mode = ref<CanvasMode>('compact')
const view = ref({ x: 8, y: 8, k: 1 })
const canvas = ref<HTMLElement | null>(null)
const layout = computed(() => layoutCanvas(props.graph, mode.value))
const geometry = computed(() => GEOMETRY[mode.value])

const tone: Record<NodeRunStatus, string> = {
  pending: 'idle',
  running: 'mark',
  cached: 'pass',
  ok: 'pass',
  failed: 'fail',
  blocked: 'idle',
  paused: 'warn',
}

function money(value: number): string {
  return `$${value.toFixed(4)}`
}

function tokens(node: RunGraphNodeOut): string {
  return (node.tokens_in + node.tokens_out).toLocaleString('de-DE')
}

function duration(node: RunGraphNodeOut): string {
  if (node.wall_ms === null || node.wall_ms === undefined) return t.common.none
  if (node.wall_ms < 1000) return `${node.wall_ms} ms`
  const seconds = node.wall_ms / 1000
  return seconds < 90 ? `${seconds.toFixed(0)} s` : `${(seconds / 60).toFixed(1)} min`
}

/** Gate findings worth a glance on a small node: only those that found something. */
function findings(node: RunGraphNodeOut) {
  return node.findings.filter((finding) => finding.violations > 0)
}

function note(placed: PlacedNode): string {
  const key = `${placed.runId}:${placed.node.name}`
  if (placed.node.status === 'running' && props.live?.[key]) return props.live[key]
  if (placed.node.status === 'pending' && props.waiting?.[placed.episode]) {
    return props.waiting[placed.episode]
  }
  return ''
}

function fit(): void {
  const element = canvas.value
  if (!element) return
  view.value = fitView(layout.value, { width: element.clientWidth, height: element.clientHeight })
}

function zoom(factor: number): void {
  const element = canvas.value
  if (!element) return
  view.value = zoomAt(view.value, factor, element.clientWidth / 2, element.clientHeight / 2)
}

let drag: { x: number; y: number; vx: number; vy: number } | null = null
const dragged = ref(false)

function onPointerDown(event: PointerEvent): void {
  if (event.button !== 0) return
  drag = { x: event.clientX, y: event.clientY, vx: view.value.x, vy: view.value.y }
  dragged.value = false
}

function onPointerMove(event: PointerEvent): void {
  if (!drag) return
  const dx = event.clientX - drag.x
  const dy = event.clientY - drag.y
  if (!dragged.value && Math.hypot(dx, dy) > 4) dragged.value = true
  if (dragged.value) view.value = { ...view.value, x: drag.vx + dx, y: drag.vy + dy }
}

function onPointerUp(): void {
  drag = null
  // A click fires after pointerup; let it see that this was a drag.
  setTimeout(() => {
    dragged.value = false
  }, 0)
}

function onWheel(event: WheelEvent): void {
  if (!event.ctrlKey && !event.metaKey) return
  event.preventDefault()
  const box = canvas.value?.getBoundingClientRect()
  if (!box) return
  view.value = zoomAt(view.value, event.deltaY < 0 ? 1.1 : 1 / 1.1, event.clientX - box.left, event.clientY - box.top)
}

function select(placed: PlacedNode): void {
  if (!dragged.value) emit('select', placed)
}

function selectLane(episode: number): void {
  if (!dragged.value) emit('lane', episode)
}

function setMode(next: CanvasMode): void {
  mode.value = next
  nextTick(fit)
}

watch(
  () => props.graph.episodes.length,
  () => nextTick(fit),
)

onMounted(() => {
  nextTick(fit)
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
  window.addEventListener('resize', fit)
})

onUnmounted(() => {
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
  window.removeEventListener('resize', fit)
})

defineExpose({ fit, mode })
</script>

<template>
  <div class="pipeline">
    <div class="tools">
      <div class="toggle" role="group" :aria-label="t.series.canvas">
        <button
          type="button"
          data-mode="compact"
          :aria-pressed="mode === 'compact'"
          @click="setMode('compact')"
        >
          {{ t.series.compact }}
        </button>
        <button
          type="button"
          data-mode="detail"
          :aria-pressed="mode === 'detail'"
          @click="setMode('detail')"
        >
          {{ t.series.detailed }}
        </button>
      </div>
      <span class="grow" />
      <button type="button" class="btn btn--sm" :aria-label="t.series.zoomOut" @click="zoom(1 / 1.2)">
        −
      </button>
      <span class="meta zoom">{{ Math.round(view.k * 100) }} %</span>
      <button type="button" class="btn btn--sm" :aria-label="t.series.zoomIn" @click="zoom(1.2)">
        +
      </button>
      <button type="button" class="btn btn--sm" @click="fit">{{ t.series.fit }}</button>
    </div>

    <div
      ref="canvas"
      class="canvas"
      :class="{ 'canvas--dragging': dragged }"
      :style="{ height: mode === 'compact' ? '480px' : '640px' }"
      @pointerdown="onPointerDown"
      @wheel="onWheel"
    >
      <div
        class="layer"
        :style="{
          width: `${layout.width}px`,
          height: `${layout.height}px`,
          transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`,
        }"
      >
        <svg class="edges" :width="layout.width" :height="layout.height" aria-hidden="true">
          <defs>
            <marker id="pc-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0 0L10 5L0 10z" class="arrow" />
            </marker>
            <marker id="pc-arrow-new" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0 0L10 5L0 10z" class="arrow arrow--new" />
            </marker>
          </defs>
          <template v-for="(edge, index) in layout.edges" :key="index">
            <path
              v-if="edge.path"
              :d="edge.path"
              :class="`edge edge--${edge.kind}`"
              :marker-end="edge.kind === 'bus' ? undefined : edge.kind === 'plain' ? 'url(#pc-arrow)' : 'url(#pc-arrow-new)'"
            />
            <text v-if="edge.label" :x="edge.labelX" :y="edge.labelY" class="edge-label">
              {{ edge.label }}
            </text>
          </template>
        </svg>

        <template v-for="lane in layout.lanes" :key="lane.episode">
          <div
            class="lane"
            :class="{ 'lane--on': lane.episode === selectedEpisode }"
            :style="{ left: `${lane.x}px`, top: `${lane.y}px`, width: `${lane.width}px`, height: `${lane.height}px` }"
          />
          <button
            type="button"
            class="lane__label"
            :class="{ 'lane__label--on': lane.episode === selectedEpisode }"
            :data-lane="lane.episode"
            :style="{ left: `${lane.x + 10}px`, top: `${lane.y + 5}px` }"
            @click="selectLane(lane.episode)"
          >
            <b>{{ fill(t.series.laneLabel, { n: lane.episode, title: lane.title }) }}</b>
            <span v-if="waiting?.[lane.episode]"> · {{ waiting[lane.episode] }}</span>
          </button>
        </template>

        <div
          class="seedcard"
          :style="{
            left: `${layout.seeds.x}px`,
            top: `${layout.seeds.y}px`,
            width: `${geometry.width}px`,
            height: `${geometry.height}px`,
          }"
        >
          <span class="eyebrow">{{ t.series.seeds }}</span>
          <ul class="ports">
            <li
              v-for="key in mode === 'compact' ? layout.seeds.keys.filter((k) => SERIES_INPUTS.has(k)) : layout.seeds.keys"
              :key="key"
              class="port"
              :class="{ 'port--new': SERIES_INPUTS.has(key) }"
            >
              <span class="port__key">{{ key }}</span>
            </li>
          </ul>
          <span v-if="mode === 'compact'" class="meta">
            {{ fill(t.series.seedCount, { n: layout.seeds.keys.length }) }}
          </span>
        </div>

        <button
          v-for="placed in layout.nodes"
          :key="placed.key"
          type="button"
          class="gnode"
          :class="[
            `gnode--${tone[placed.node.status]}`,
            { 'gnode--on': placed.key === selectedKey, 'gnode--compact': mode === 'compact' },
          ]"
          :data-node="placed.key"
          :aria-pressed="placed.key === selectedKey"
          :style="{
            left: `${placed.x}px`,
            top: `${placed.y}px`,
            width: `${geometry.width}px`,
            height: `${geometry.height}px`,
          }"
          @click="select(placed)"
        >
          <header class="gnode__head">
            <span class="gnode__name">{{ placed.node.name }}</span>
            <span class="meta">v{{ placed.node.version }}</span>
          </header>
          <div class="gnode__row">
            <span class="badge" :class="`badge--${tone[placed.node.status]}`">
              <span v-if="placed.node.status === 'running'" class="pulse" aria-hidden="true" />
              {{ t.graph.status[placed.node.status] }}
            </span>
            <span class="num">{{ money(placed.node.cost_usd) }}</span>
          </div>
          <span v-if="note(placed)" class="gnode__note" :class="{ 'gnode__note--wait': placed.node.status !== 'running' }">
            {{ note(placed) }}
          </span>
          <span v-else-if="mode === 'compact'" class="meta gnode__facts-line">
            {{ tokens(placed.node) }} {{ t.run.tokens }} · {{ duration(placed.node) }}
          </span>
          <template v-if="mode === 'detail'">
            <ul class="ports">
              <li
                v-for="port in placed.node.consumes"
                :key="port.key"
                class="port"
                :class="{ 'port--new': SERIES_INPUTS.has(port.key) }"
              >
                <span class="port__arrow" aria-hidden="true">↳</span>
                <span class="port__key">{{ port.key }}</span>
              </li>
              <li class="port port--out">
                <span class="port__arrow" aria-hidden="true">↦</span>
                <span class="port__key">{{ placed.node.produces.key }}</span>
                <span class="port__model">{{ placed.node.produces.model }}</span>
              </li>
            </ul>
            <dl class="gnode__facts">
              <div>
                <dt class="meta">{{ t.run.tokens }}</dt>
                <dd class="num">{{ tokens(placed.node) }}</dd>
              </div>
              <div>
                <dt class="meta">{{ t.run.duration }}</dt>
                <dd class="num">{{ duration(placed.node) }}</dd>
              </div>
              <div v-if="placed.node.model_id">
                <dt class="meta">{{ t.run.model }}</dt>
                <dd class="truncate">{{ placed.node.model_id }}</dd>
              </div>
            </dl>
          </template>
          <span v-if="mode === 'compact' && findings(placed.node).length" class="findings">
            <span
              v-for="finding in findings(placed.node)"
              :key="finding.gate"
              class="badge"
              :class="finding.status === 'fail' ? 'badge--fail' : 'badge--warn'"
              :title="finding.name"
            >
              {{ finding.gate }}
            </span>
          </span>
        </button>
      </div>
      <span class="canvas__hint" aria-hidden="true">{{ t.series.canvasHint }}</span>
    </div>
    <p class="legend">
      <span class="port port--new"><span class="port__key">episode_brief</span></span>
      {{ t.series.legendNew }} · {{ t.series.legendEdges }}
    </p>
  </div>
</template>

<style scoped>
.pipeline {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  min-width: 0;
}

.tools {
  display: flex;
  gap: var(--s2);
  align-items: center;
  flex-wrap: wrap;
}

.grow {
  flex: 1;
}

.zoom {
  min-width: 4ch;
  text-align: center;
}

.toggle {
  display: inline-flex;
  border: 1px solid var(--rule-strong);
  border-radius: var(--r-md);
  overflow: hidden;
}

.toggle button {
  border: 0;
  background: var(--card);
  padding: 5px 12px;
  font-size: var(--t-sm);
  cursor: pointer;
  color: var(--ink-2);
}

.toggle button + button {
  border-left: 1px solid var(--rule-strong);
}

.toggle button[aria-pressed='true'] {
  background: var(--ink);
  color: var(--chrome);
}

.canvas {
  position: relative;
  overflow: hidden;
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  background-color: var(--chrome);
  background-image: radial-gradient(var(--rule-strong) 1px, transparent 1px);
  background-size: 18px 18px;
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.canvas--dragging {
  cursor: grabbing;
}

.layer {
  position: absolute;
  left: 0;
  top: 0;
  transform-origin: 0 0;
}

.edges {
  position: absolute;
  left: 0;
  top: 0;
  overflow: visible;
  pointer-events: none;
}

.edge {
  fill: none;
  stroke: var(--ink-4);
  stroke-width: 1.4;
}

.edge--brief,
.edge--earlier {
  stroke: var(--mark);
  stroke-width: 1.8;
}

.edge--bus {
  stroke: var(--mark);
  stroke-dasharray: 5 4;
}

.arrow {
  fill: var(--ink-4);
}

.arrow--new {
  fill: var(--mark);
}

.edge-label {
  fill: var(--mark-deep);
  font-family: var(--mono);
  font-size: 10px;
}

.lane {
  position: absolute;
  border: 1px dashed var(--rule-strong);
  border-radius: var(--r-lg);
  background: color-mix(in srgb, var(--card) 55%, transparent);
}

.lane--on {
  border: 1px solid var(--mark);
  background: color-mix(in srgb, var(--mark-soft) 60%, transparent);
}

.lane__label {
  position: absolute;
  border: 0;
  background: transparent;
  padding: 0;
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-3);
  cursor: pointer;
  white-space: nowrap;
}

.lane__label b {
  color: var(--ink-2);
  font-weight: 600;
}

.lane__label--on,
.lane__label--on b {
  color: var(--mark-deep);
}

.seedcard {
  position: absolute;
  border: 1px dashed var(--rule-strong);
  border-radius: var(--r-lg);
  background: var(--card);
  padding: 8px 10px;
  display: grid;
  gap: 4px;
  align-content: start;
  overflow: hidden;
}

.gnode {
  position: absolute;
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
  overflow: hidden;
  transition:
    border-color var(--fast),
    box-shadow var(--fast);
}

.gnode--compact {
  gap: 4px;
  padding: 8px 10px;
}

.gnode:hover {
  box-shadow: var(--shadow-md);
}

.gnode--pass {
  border-top-color: var(--pass);
}

.gnode--fail {
  border-top-color: var(--fail);
}

.gnode--mark {
  border-top-color: var(--mark);
}

.gnode--warn {
  border-top-color: var(--warn);
}

.gnode--idle {
  border-top-color: var(--rule-strong);
  opacity: 0.78;
}

.gnode--on {
  box-shadow:
    0 0 0 1px var(--mark),
    var(--shadow-md);
}

.gnode__head {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.gnode__name {
  font-family: var(--mono);
  font-size: var(--t-sm);
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gnode__row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--s2);
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-2);
}

.gnode__note {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--mark-deep);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.gnode__note--wait {
  color: var(--ink-3);
}

.gnode__facts-line {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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

.port--new .port__key {
  color: var(--mark-deep);
  background: var(--mark-soft);
  border-radius: 3px;
  padding: 0 3px;
}

.port__model {
  color: var(--ink-4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gnode__facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px var(--s2);
  margin: 0;
  padding-top: var(--s2);
  border-top: 1px solid var(--rule);
}

.gnode__facts dt {
  font-size: 0.5625rem;
}

.gnode__facts dd {
  margin: 0;
  font-size: var(--t-xs);
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
}

.pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  display: inline-block;
}

.canvas__hint {
  position: absolute;
  right: 10px;
  bottom: 8px;
  font-family: var(--mono);
  font-size: 10px;
  color: var(--ink-3);
  pointer-events: none;
  background: var(--chrome);
  padding: 2px 6px;
  border-radius: var(--r-sm);
}

.findings {
  display: flex;
  gap: 3px;
  flex-wrap: wrap;
}

.legend {
  font-size: var(--t-xs);
  color: var(--ink-3);
  display: flex;
  gap: var(--s2);
  flex-wrap: wrap;
  align-items: baseline;
}
</style>
