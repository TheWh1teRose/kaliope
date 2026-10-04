<script setup lang="ts">
/**
 * Audio for a whole series: one take per episode, approved together.
 *
 * Each episode is an ordinary run, so its audio is that run's take; this sheet
 * starts a take for every finished episode, sums what the waiting ones cost,
 * approves them in one go and plays every finished episode. Voices come from
 * the format's default, the same for every episode.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { audioApi, takeActive } from '@/api/audio'
import { ApiError } from '@/api/client'
import type { SeriesAudioOut } from '@/api/types'
import { fill, t } from '@/i18n'

const props = defineProps<{ seriesId: string }>()

const POLL_MS = 2000

const data = ref<SeriesAudioOut | null>(null)
const busy = ref(false)
const error = ref('')
let timer: number | undefined

const active = computed(() =>
  (data.value?.episodes ?? []).some((e) => e.take && takeActive(e.take.status)),
)

function money(value: number): string {
  return `$${value.toFixed(2)}`
}

function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

function badge(status: string | undefined): string {
  if (status === 'completed') return 'badge--pass'
  if (status === 'failed') return 'badge--fail'
  if (status === 'paused' || status === 'running' || status === 'queued') return 'badge--warn'
  return 'badge--idle'
}

async function reload(): Promise<void> {
  try {
    data.value = await audioApi.series(props.seriesId)
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  }
}

async function act(call: () => Promise<SeriesAudioOut>): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    data.value = await call()
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

watch(data, () => {
  window.clearTimeout(timer)
  if (active.value) timer = window.setTimeout(() => void reload(), POLL_MS)
})

onMounted(reload)
onUnmounted(() => window.clearTimeout(timer))
</script>

<template>
  <div v-if="data && data.episodes.length" class="sheet series-audio">
    <div class="spread">
      <h2 class="h-section">{{ t.audio.seriesTitle }}</h2>
      <span class="meta">{{ data.format_id ?? '' }}</span>
    </div>
    <p class="muted hint">{{ t.audio.seriesLead }}</p>
    <p v-if="!data.configured" class="notice notice--warn" role="status">
      <b>{{ t.audio.notConfigured }}</b> {{ data.message }}
    </p>
    <p v-if="error" class="notice notice--fail" role="alert">{{ error }}</p>

    <div class="row wrap">
      <button class="btn" :disabled="busy" @click="act(() => audioApi.startSeries(seriesId))">
        {{ t.audio.prepareAll }}
      </button>
      <button
        v-if="data.waiting"
        class="btn btn--mark"
        :disabled="busy || !data.configured"
        @click="act(() => audioApi.approveSeries(seriesId))"
      >
        {{ fill(t.audio.approveAll, { n: data.waiting, price: money(data.estimate_usd) }) }}
      </button>
      <span v-if="data.waiting" class="meta">{{ t.audio.approveHint }}</span>
    </div>

    <ol class="episodes">
      <li v-for="episode in data.episodes" :key="episode.run_id">
        <div class="episode__head">
          <b>{{ episode.name || fill(t.audio.episodeName, { n: episode.index }) }}</b>
          <span class="badge" :class="badge(episode.take?.status)">
            {{ episode.take ? episode.take.status : t.audio.noAudio }}
          </span>
          <span v-if="episode.take?.approval" class="meta num">
            ≈ {{ money(episode.take.approval.estimate_usd) }}
          </span>
          <span v-if="episode.take?.approval?.missing_voices.length" class="meta">
            {{ fill(t.audio.missingVoices, { names: episode.take.approval.missing_voices.join(', ') }) }}
          </span>
          <span class="grow" />
          <RouterLink class="btn btn--sm" :to="{ name: 'run', params: { id: episode.run_id } }">
            {{ t.audio.openEpisode }}
          </RouterLink>
        </div>
        <div v-if="episode.take?.mix" class="episode__player">
          <audio controls preload="none" :src="episode.take.mix.url" />
          <span class="meta num">{{ clock(episode.take.mix.duration_s) }}</span>
          <a class="btn btn--sm" :href="episode.take.mix.download_url" download>
            {{ t.audio.download }}
          </a>
        </div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.series-audio {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

.hint {
  margin: 0;
  font-size: var(--t-sm);
}

.notice {
  margin: 0;
  padding: var(--s3) var(--s4);
  border-radius: var(--r-md);
  font-size: var(--t-sm);
}

.notice--warn {
  background: var(--warn-soft);
  color: var(--warn);
}

.notice--fail {
  background: var(--fail-soft);
  color: var(--fail);
}

.episodes {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.episodes li {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  padding: var(--s3);
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
}

.episode__head,
.episode__player {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s2);
  align-items: center;
  min-width: 0;
}

.episode__player audio {
  flex: 1;
  min-width: 240px;
}
</style>
