<script setup lang="ts">
/**
 * The Redaktion correspondence: script on the left, source page on the right.
 * Clicking a citation parks the crosshair on the anchored rectangle.
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import type { AnchorRect, StructureOut } from '@/api/types'
import PageCanvas from '@/components/PageCanvas.vue'
import { t } from '@/i18n'
import { useCatalogueStore } from '@/stores/catalogue'

const props = defineProps<{
  documentId: string | null
  structure: StructureOut | null
  highlight: AnchorRect[]
  activeSegmentId?: string | null
}>()

const catalogue = useCatalogueStore()

const workspace = ref<HTMLElement | null>(null)
const page = ref(0)
const zoom = ref(1)
const tieY = ref<number | null>(null)

const pageSize = computed<[number, number]>(
  () => props.structure?.page_sizes?.[page.value] ?? [595, 842],
)

watch(
  () => props.highlight,
  (rects) => {
    if (rects.length) page.value = rects[0].page
    void nextTick(updateTie)
  },
)

watch(
  () => props.activeSegmentId,
  () => void nextTick(updateTie),
)

function updateTie(): void {
  if (!props.activeSegmentId || !workspace.value) {
    tieY.value = null
    return
  }
  const card = workspace.value.querySelector<HTMLElement>(
    `[data-segment="${CSS.escape(props.activeSegmentId)}"]`,
  )
  if (!card) {
    tieY.value = null
    return
  }
  const box = card.getBoundingClientRect()
  const frame = workspace.value.getBoundingClientRect()
  const y = box.top + box.height / 2 - frame.top
  tieY.value = y > 0 && y < frame.height ? y : null
}

onMounted(() => window.addEventListener('resize', updateTie))
onUnmounted(() => window.removeEventListener('resize', updateTie))

defineExpose({ showPage: (n: number) => (page.value = n), updateTie })
</script>

<template>
  <div ref="workspace" class="panes">
    <section class="pane pane--script scroll" @scroll="updateTie">
      <slot />
    </section>

    <svg v-if="tieY !== null" class="tie" aria-hidden="true">
      <line :x1="0" :y1="tieY" :x2="'100%'" :y2="tieY" />
      <circle :cx="4" :cy="tieY" r="3" />
    </svg>

    <section class="pane pane--source">
      <div class="pane__bar">
        <span class="eyebrow">{{ t.review.source }}</span>
        <span class="grow" />
        <button class="btn btn--sm" :disabled="page === 0" @click="page = page - 1">←</button>
        <span class="num">{{ page + 1 }} / {{ structure?.page_count ?? 1 }}</span>
        <button
          class="btn btn--sm"
          :disabled="!structure || page >= structure.page_count - 1"
          @click="page = page + 1"
        >
          →
        </button>
        <button class="btn btn--sm btn--ghost" @click="zoom = zoom === 1 ? 1.6 : 1">
          {{ zoom === 1 ? t.review.zoom : t.review.fit }}
        </button>
      </div>
      <div class="pane__body scroll">
        <PageCanvas
          v-if="structure && documentId"
          :document-id="documentId"
          :page="page"
          :page-size="pageSize"
          :blocks="structure.blocks"
          :zones="catalogue.zones"
          :highlight="highlight"
          :show-plates="false"
          :zoom="zoom"
        />
        <p v-if="!documentId" class="hintbox muted">{{ t.notes.noSource }}</p>
        <p v-else-if="!highlight.length" class="hintbox muted">{{ t.review.sourceHint }}</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.panes {
  position: relative;
  display: grid;
  grid-template-columns: minmax(420px, 1fr) minmax(420px, 1fr);
  gap: 0;
  flex: 1;
  min-height: 0;
}

.pane {
  min-width: 0;
  min-height: 0;
}

.pane--script {
  padding: var(--s4) var(--s5) var(--s8);
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  border-right: 1px solid var(--rule);
}

.pane--source {
  display: flex;
  flex-direction: column;
  background: var(--chrome);
}

.pane__bar {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: var(--s2) var(--s3);
  border-bottom: 1px solid var(--rule);
  font-size: var(--t-sm);
}

.pane__body {
  flex: 1;
  min-height: 0;
  position: relative;
}

.tie {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 2;
  overflow: visible;
}

.tie line {
  stroke: var(--mark);
  stroke-width: 1;
  stroke-dasharray: 5 4;
  opacity: 0.5;
}

.tie circle {
  fill: var(--mark);
}

.hintbox {
  position: absolute;
  inset: auto 0 40% 0;
  text-align: center;
  font-size: var(--t-sm);
  pointer-events: none;
}

@media (max-width: 1000px) {
  .panes {
    grid-template-columns: 1fr;
  }
  .tie {
    display: none;
  }
  .pane--source {
    min-height: 60vh;
  }
}
</style>
