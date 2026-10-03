<script setup lang="ts">
/**
 * "Serie · Folge 2 von 3 ‹ ›" above an episode's review: where the episode
 * stands in its series, with the neighbouring episodes one click away.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import type { SeriesOut } from '@/api/types'
import { fill, t } from '@/i18n'
import { useSeriesStore } from '@/stores/series'

const props = defineProps<{ seriesId: string; episode: number }>()

const store = useSeriesStore()
const series = ref<SeriesOut | null>(null)
const DONE = new Set(['completed', 'in_review', 'reviewed'])

function neighbour(index: number): string | null {
  const row = series.value?.episodes.find((e) => e.index === index)
  return row && row.run_id && DONE.has(row.status) ? row.run_id : null
}

const previous = computed(() => neighbour(props.episode - 1))
const next = computed(() => neighbour(props.episode + 1))

onMounted(async () => {
  series.value = await store.get(props.seriesId).catch(() => null)
})
</script>

<template>
  <span v-if="series" class="strip">
    <!-- Plain links: the review page loads its run once, so a neighbour needs a fresh page. -->
    <a v-if="previous" class="btn btn--sm" :href="`/runs/${previous}/review`" :aria-label="t.series.previousEpisode">‹</a>
    <RouterLink class="badge badge--mark strip__label" :to="{ name: 'series', params: { id: seriesId } }">
      {{ fill(t.series.reviewStrip, { n: episode, total: series.episodes.length }) }}
    </RouterLink>
    <a v-if="next" class="btn btn--sm" :href="`/runs/${next}/review`" :aria-label="t.series.nextEpisode">›</a>
  </span>
</template>

<style scoped>
.strip {
  display: inline-flex;
  align-items: center;
  gap: var(--s1);
}

.strip .btn,
.strip__label {
  text-decoration: none;
}
</style>
