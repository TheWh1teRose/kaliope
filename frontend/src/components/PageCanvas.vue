<script setup lang="ts">
/**
 * A rendered PDF page with two overlays: zone plates, and the registration
 * crosshair that marks the anchored rectangle.
 *
 * The overlay is an SVG whose viewBox is the page's own coordinate system in
 * PDF points, so every rectangle is placed in source coordinates and the
 * browser does the scaling. No pixel maths, and the highlight stays correct at
 * any zoom.
 */
import { computed, ref, watch } from 'vue'

import type { AnchorRect, BBox, BlockOut, ZoneSpec } from '@/api/types'

const props = withDefaults(
  defineProps<{
    documentId: string
    page: number
    pageSize: [number, number]
    blocks?: BlockOut[]
    zones?: ZoneSpec[]
    highlight?: AnchorRect[]
    selectedBlockId?: string | null
    showPlates?: boolean
    zoom?: number
  }>(),
  { blocks: () => [], zones: () => [], highlight: () => [], showPlates: true, zoom: 1 },
)

const emit = defineEmits<{ (e: 'select-block', id: string): void }>()

const loaded = ref(false)
const width = computed(() => props.pageSize?.[0] || 595)
const height = computed(() => props.pageSize?.[1] || 842)

const plates = computed(() =>
  props.blocks
    .filter((b) => b.page === props.page)
    .flatMap((b) =>
      b.bboxes
        .filter(([p]) => p === props.page)
        .map(([, bbox]) => ({ block: b, bbox: bbox as BBox })),
    ),
)

const marks = computed(() => props.highlight.filter((r) => r.page === props.page))

/** Centre of the first highlighted rect, where the crosshair parks. */
const crosshair = computed(() => {
  const first = marks.value[0]
  if (!first) return null
  const [x0, y0, x1, y1] = first.bbox
  return { x: (x0 + x1) / 2, y: (y0 + y1) / 2 }
})

function zoneColor(zone: string): string {
  return `var(--zone-${zone}, var(--zone-other))`
}

function isNarratable(zone: string): boolean {
  return props.zones.find((z) => z.zone === zone)?.narratable ?? false
}

watch(
  () => [props.documentId, props.page],
  () => {
    loaded.value = false
  },
)
</script>

<template>
  <div class="canvas" :style="{ '--zoom': String(zoom) }">
    <div class="canvas__frame">
      <img
        class="canvas__img"
        :src="`/api/documents/${documentId}/pages/${page}/image`"
        :alt="`Seite ${page + 1}`"
        decoding="async"
        @load="loaded = true"
      />
      <svg
        class="canvas__overlay"
        :viewBox="`0 0 ${width} ${height}`"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <g v-if="showPlates">
          <rect
            v-for="(plate, index) in plates"
            :key="`${plate.block.id}-${index}`"
            class="plate"
            :class="{
              'plate--selected': plate.block.id === selectedBlockId,
              'plate--uncertain': plate.block.zone_uncertain,
              'plate--mute': !isNarratable(plate.block.zone),
            }"
            :x="plate.bbox[0]"
            :y="plate.bbox[1]"
            :width="Math.max(plate.bbox[2] - plate.bbox[0], 1)"
            :height="Math.max(plate.bbox[3] - plate.bbox[1], 1)"
            :style="{ '--plate': zoneColor(plate.block.zone) }"
            @click="emit('select-block', plate.block.id)"
          />
        </g>

        <g v-if="marks.length" class="register">
          <rect
            v-for="(rect, index) in marks"
            :key="index"
            class="register__rect"
            :x="rect.bbox[0] - 1.5"
            :y="rect.bbox[1] - 1.5"
            :width="Math.max(rect.bbox[2] - rect.bbox[0] + 3, 2)"
            :height="Math.max(rect.bbox[3] - rect.bbox[1] + 3, 2)"
          />
          <g v-if="crosshair" class="register__cross">
            <line :x1="0" :y1="crosshair.y" :x2="width" :y2="crosshair.y" />
            <line :x1="crosshair.x" :y1="0" :x2="crosshair.x" :y2="height" />
            <circle :cx="crosshair.x" :cy="crosshair.y" r="9" />
          </g>
        </g>
      </svg>
    </div>
  </div>
</template>

<style scoped>
.canvas {
  display: flex;
  justify-content: center;
  padding: var(--s4);
}

.canvas__frame {
  position: relative;
  width: calc(100% * var(--zoom, 1));
  max-width: calc(920px * var(--zoom, 1));
  background: var(--page);
  border: 1px solid var(--rule-strong);
  border-radius: 3px;
  box-shadow: var(--shadow-md);
  overflow: hidden;
}

.canvas__img {
  display: block;
  width: 100%;
  height: auto;
}

.canvas__overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.plate {
  fill: var(--plate);
  fill-opacity: 0.1;
  stroke: var(--plate);
  stroke-opacity: 0.4;
  stroke-width: 0.6;
  cursor: pointer;
  transition:
    fill-opacity var(--fast),
    stroke-opacity var(--fast);
}

.plate:hover {
  fill-opacity: 0.24;
  stroke-opacity: 0.9;
}

.plate--mute {
  fill-opacity: 0.16;
  stroke-dasharray: 3 2;
}

.plate--uncertain {
  stroke-dasharray: 1.5 1.5;
  stroke-width: 1.1;
}

.plate--selected {
  fill-opacity: 0.3;
  stroke-opacity: 1;
  stroke-width: 1.6;
}

/* The signature: a pre-press registration mark that parks on the citation. */
.register__rect {
  fill: var(--mark);
  fill-opacity: 0.16;
  stroke: var(--mark);
  stroke-width: 1.4;
  animation: seat var(--slow) both;
}

.register__cross line {
  stroke: var(--mark);
  stroke-width: 0.5;
  stroke-dasharray: 4 3;
  opacity: 0.5;
  animation: sweep 420ms cubic-bezier(0.16, 1, 0.3, 1) both;
}

.register__cross circle {
  fill: none;
  stroke: var(--mark);
  stroke-width: 0.8;
  opacity: 0.75;
}

@keyframes seat {
  from {
    fill-opacity: 0.45;
    stroke-opacity: 0;
  }
  to {
    fill-opacity: 0.16;
    stroke-opacity: 1;
  }
}

@keyframes sweep {
  from {
    opacity: 0;
  }
  to {
    opacity: 0.5;
  }
}
</style>
