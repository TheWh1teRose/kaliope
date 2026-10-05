<script setup lang="ts">
/**
 * One frozen source page. The overlay uses the page's own point coordinates,
 * the same registration mark as the Redaktion canvas, without zone plates.
 */
import { computed } from 'vue'

const props = defineProps<{
  url: string
  width: number
  height: number
  highlight: { page: number; bbox: [number, number, number, number] }[]
  page: number
}>()

const marks = computed(() => props.highlight.filter((rect) => rect.page === props.page))
const crosshair = computed(() => {
  const first = marks.value[0]
  if (!first) return null
  const [x0, y0, x1, y1] = first.bbox
  return { x: (x0 + x1) / 2, y: (y0 + y1) / 2 }
})
</script>

<template>
  <div class="canvas">
    <div class="frame">
      <img :src="url" alt="" />
      <svg
        class="overlay"
        :viewBox="`0 0 ${width || 595} ${height || 842}`"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <g v-if="marks.length">
          <rect
            v-for="(rect, index) in marks"
            :key="index"
            class="mark"
            :x="rect.bbox[0] - 1.5"
            :y="rect.bbox[1] - 1.5"
            :width="Math.max(rect.bbox[2] - rect.bbox[0] + 3, 2)"
            :height="Math.max(rect.bbox[3] - rect.bbox[1] + 3, 2)"
          />
          <g v-if="crosshair">
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
.frame {
  position: relative;
  width: 100%;
  max-width: 720px;
  background: var(--page);
  border: 1px solid var(--rule-strong);
  border-radius: 3px;
  box-shadow: var(--shadow-md);
  overflow: hidden;
}
img {
  display: block;
  width: 100%;
  height: auto;
}
.overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.mark {
  fill: var(--mark);
  fill-opacity: 0.16;
  stroke: var(--mark);
  stroke-width: 1.4;
}
line {
  stroke: var(--mark);
  stroke-width: 0.5;
  stroke-dasharray: 4 3;
  opacity: 0.5;
}
circle {
  fill: none;
  stroke: var(--mark);
  stroke-width: 0.8;
}
</style>
