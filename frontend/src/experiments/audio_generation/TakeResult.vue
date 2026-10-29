<script setup lang="ts">
/**
 * One attempt's audio: progress while it is spoken, the joined file with a
 * player and a download when it is done, the error when it failed. The
 * spoken segments follow the player, and a click on one jumps there.
 */
import { computed, ref } from 'vue'

import { lineAt, takeActive } from '@/api/audio'
import type { AudioTakeOut, ExperimentRun } from '@/api/types'
import LoadingState from '@/components/LoadingState.vue'
import { fill, t } from '@/i18n'

import { clock, money, pieces, type Line, type Setup } from './synthesis'

const props = defineProps<{
  run: ExperimentRun<Setup>
  take: AudioTakeOut | null
  current?: boolean
}>()
const emit = defineEmits<{
  (e: 'stop', take: AudioTakeOut): void
  (e: 'resume', take: AudioTakeOut): void
}>()

const labels = t.experiments.audioGeneration
const player = ref<HTMLAudioElement | null>(null)
const now = ref(0)

const lines = computed(
  () => ((props.take?.audio_script as { lines?: Line[] } | null)?.lines ?? []) as Line[],
)
const playing = computed(() => (props.take?.mix ? lineAt(props.take.mix.lines, now.value) : null))
const done = computed(() => props.take?.plan.filter((c) => c.status === 'done' || c.status === 'cached').length ?? 0)
const total = computed(() => props.take?.plan.length ?? 0)
const output = computed(() => props.run.output ?? {})
const characters = computed(
  () => (output.value.characters as number | undefined) ?? lines.value.reduce((sum, l) => sum + l.tagged.length, 0),
)
const segments = computed(() => (output.value.segments as number | undefined) ?? lines.value.length)
const edited = computed(() => (output.value.edited as number | undefined) ?? 0)
const when = computed(() =>
  new Date(props.run.created_at).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }),
)
const stability = computed(() => {
  const value = props.run.setup.voice_cast.stability
  return value === 0 ? labels.creative : value === 1 ? labels.robust : labels.natural
})
const status = computed(() => {
  if (props.run.status === 'failed' || props.take?.status === 'failed') return { cls: 'badge--fail', text: t.audio.failed }
  if (props.take?.status === 'completed') return { cls: 'badge--pass', text: t.audio.done }
  if (props.take?.status === 'stopped') return { cls: 'badge--idle', text: t.audio.stopped }
  return { cls: 'badge--warn', text: labels.speaking }
})

function startOf(id: string): number | null {
  return props.take?.mix?.lines.find((line) => line.segment_id === id)?.start_s ?? null
}

function seek(id: string): void {
  const audio = player.value
  const start = startOf(id)
  if (!audio || start === null) return
  audio.currentTime = start
  now.value = start
  const started = audio.play() as Promise<void> | undefined
  if (started) void started.catch(() => undefined)
}
</script>

<template>
  <article class="out" :class="{ 'out--current': current }" :data-run="run.id">
    <div class="out__head">
      <span class="out__title grow truncate">{{ when }}</span>
      <span class="badge" :class="status.cls">{{ status.text }}</span>
    </div>
    <div class="out__badges">
      <span class="badge badge--idle">{{ run.setup.voice_cast.model_id }}</span>
      <span class="badge badge--idle">{{ stability }}</span>
      <span v-if="run.setup.voice_cast.seed !== null && run.setup.voice_cast.seed !== undefined" class="badge badge--idle">
        {{ labels.seed }} {{ run.setup.voice_cast.seed }}
      </span>
      <span v-if="edited" class="badge badge--warn">{{ fill(labels.editedCount, { n: edited }) }}</span>
    </div>
    <div class="out__facts">
      <span class="meta num">
        {{
          fill(labels.resultFacts, {
            segments,
            characters: characters.toLocaleString('de-DE'),
            cost: money(take?.total_cost_usd ?? run.total_cost_usd),
          })
        }}
      </span>
      <span v-if="take?.mix" class="meta num">{{ fill(labels.duration2, { duration: clock(take.mix.duration_s) }) }}</span>
    </div>

    <div class="out__body">
      <p v-if="run.status === 'failed'" class="failed" role="alert">{{ run.error }}</p>
      <LoadingState v-else-if="!take && (run.status === 'queued' || run.status === 'running')" :label="labels.queued" />
      <p v-else-if="!take" class="muted small">{{ labels.takeMissing }}</p>
      <template v-else>
        <div v-if="takeActive(take.status)" class="progress" role="status">
          <div class="progress__line">
            <span class="spinner" aria-hidden="true" />
            <span>{{ take.status === 'queued' ? labels.queued : labels.speaking }}</span>
            <span class="grow" />
            <span class="meta num">{{ fill(labels.progress, { done, total }) }}</span>
          </div>
          <div class="bar"><span :style="{ width: `${total ? (done / total) * 100 : 4}%` }" /></div>
        </div>
        <p v-if="take.status === 'failed'" class="failed" role="alert">{{ take.error }}</p>
        <p v-if="take.status === 'stopped'" class="muted small">{{ labels.stopped }}</p>

        <div v-if="take.mix" class="player">
          <audio
            ref="player"
            controls
            preload="metadata"
            :src="take.mix.url"
            @timeupdate="now = player?.currentTime ?? 0"
            @seeked="now = player?.currentTime ?? 0"
          />
          <a class="btn btn--sm" :href="take.mix.download_url" download data-download>{{ labels.download }}</a>
        </div>
        <template v-else-if="take.chunks.length && !takeActive(take.status)">
          <audio v-for="chunk in take.chunks" :key="chunk.index" class="chunk" controls preload="none" :src="chunk.url" />
        </template>

        <template v-if="current && lines.length">
          <p v-if="take.mix" class="meta hint">{{ labels.jumpHint }}</p>
          <ol class="spoken">
            <li
              v-for="line in lines"
              :key="line.segment_id"
              :class="{ 'spoken--on': playing === line.segment_id, 'spoken--seek': take.mix }"
              @click="seek(line.segment_id)"
            >
              <span class="spoken__who">{{ line.speaker }}</span>
              <span class="spoken__text">
                <template v-for="(piece, pi) in pieces(line.tagged)" :key="pi">
                  <span v-if="piece.tag" class="tag">{{ piece.text }}</span>
                  <template v-else>{{ piece.text }}</template>
                </template>
              </span>
            </li>
          </ol>
        </template>
      </template>
    </div>

    <div class="out__foot">
      <button v-if="take && takeActive(take.status)" class="btn btn--sm" @click="emit('stop', take)">
        {{ t.audio.stop }}
      </button>
      <button v-if="take?.resumable" class="btn btn--sm btn--mark" @click="emit('resume', take)">
        {{ labels.resume }}
      </button>
      <span class="grow" />
      <slot name="foot" />
    </div>
  </article>
</template>

<style scoped>
.out {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-sm);
}

.out--current {
  border-color: var(--mark);
  box-shadow:
    0 0 0 3px var(--mark-soft),
    var(--shadow-sm);
}

.out__head,
.out__badges,
.out__facts {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  padding: var(--s3) var(--s4) 0;
}

.out__badges,
.out__facts {
  padding-top: 6px;
}

.out__title {
  font-weight: 620;
}

.out__body {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  padding: var(--s3) var(--s4);
}

.out__foot {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  padding: var(--s2) var(--s4) var(--s3);
  border-top: 1px solid var(--rule);
}

.small {
  font-size: var(--t-sm);
}

.failed {
  margin: 0;
  padding: var(--s2) var(--s3);
  border: 1px solid var(--fail);
  border-radius: var(--r-md);
  background: var(--fail-soft);
  color: var(--fail);
  font-size: var(--t-sm);
}

.progress {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: var(--t-sm);
  color: var(--ink-2);
}

.progress__line {
  display: flex;
  align-items: center;
  gap: var(--s2);
}

.bar {
  height: 6px;
  border-radius: 999px;
  background: var(--sunk);
  overflow: hidden;
}

.bar span {
  display: block;
  height: 100%;
  background: var(--mark);
  transition: width 0.4s;
}

.player {
  display: flex;
  align-items: center;
  gap: var(--s3);
  padding: var(--s2) var(--s3);
  border-radius: var(--r-md);
  background: var(--sunk);
}

.player audio {
  flex: 1;
  min-width: 0;
  height: 36px;
}

.chunk {
  width: 100%;
}

.hint {
  margin: 0;
}

.spoken {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.spoken li {
  display: grid;
  grid-template-columns: 7rem minmax(0, 1fr);
  gap: var(--s3);
  padding: 6px var(--s2);
  border-radius: var(--r-md);
}

.spoken--seek {
  cursor: pointer;
}

.spoken--seek:hover {
  background: var(--chrome);
}

li.spoken--on {
  background: var(--mark-soft);
}

.spoken__who {
  font-size: var(--t-xs);
  font-weight: 620;
  color: var(--ink-2);
  padding-top: 3px;
}

.spoken__text {
  font-family: var(--serif);
  font-size: var(--t-md);
  line-height: 1.5;
}

.tag {
  padding: 0 4px;
  border-radius: 4px;
  background: var(--sunk);
  color: var(--ink-2);
  font-family: var(--mono);
  font-size: 0.78em;
}

.spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--rule-strong);
  border-top-color: var(--mark);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
