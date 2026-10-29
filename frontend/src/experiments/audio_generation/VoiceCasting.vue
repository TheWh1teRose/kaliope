<script setup lang="ts">
/**
 * Who speaks with which voice. Every voice of the account can be heard before
 * it is chosen: ElevenLabs' own preview when the voice has one, otherwise a
 * short sentence spoken once on request. One sample plays at a time, and
 * nothing plays by itself.
 */
import { computed, onUnmounted, ref, watch } from 'vue'

import { audioApi } from '@/api/audio'
import type { SpeechVoice, VoiceCast } from '@/api/types'
import LoadingState from '@/components/LoadingState.vue'
import { fill, t } from '@/i18n'

import PlayButton from './PlayButton.vue'
import { money } from './synthesis'
import { useSamplePlayer } from './samplePlayer'

const props = defineProps<{
  speakers: string[]
  /** Speakers of the chosen segments; only they need a voice. */
  needed: string[]
  voices: SpeechVoice[]
  cast: VoiceCast
  loading?: boolean
  /** USD per 1,000 characters of the chosen model, for a generated sample. */
  rate: number
  /** How long the sentence of a generated sample is. */
  sampleCharacters: number
}>()
const emit = defineEmits<{
  (e: 'assign', speaker: string, voiceId: string): void
  (e: 'error', value: unknown): void
}>()

const labels = t.experiments.audioGeneration
const player = useSamplePlayer()
const query = ref('')
const generated = ref<Record<string, string>>({})
const making = ref<string | null>(null)
let playRequest = 0

function invalidatePlayback(): void {
  playRequest++
  making.value = null
  player.stop()
}

onUnmounted(invalidatePlayback)
watch(() => props.cast.model_id, invalidatePlayback, { flush: 'sync' })

const shown = computed(() => {
  const words = query.value.trim().toLowerCase().split(/\s+/).filter(Boolean)
  return props.voices.filter((voice) => {
    const hay = [voice.name, voice.category, voice.language, voice.description]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return words.every((word) => hay.includes(word))
  })
})

function voiceOf(speaker: string): string {
  const wanted = speaker.trim().toLowerCase()
  return props.cast.voices.find((v) => v.speaker.trim().toLowerCase() === wanted)?.voice_id ?? ''
}

function voiceById(id: string): SpeechVoice | undefined {
  return props.voices.find((voice) => voice.voice_id === id)
}

function sampleKey(voice: SpeechVoice): string {
  return `${voice.voice_id}:${props.cast.model_id}`
}

function sampleUrl(voice: SpeechVoice): string | null {
  return voice.preview_url ?? generated.value[sampleKey(voice)] ?? null
}

function facts(voice: SpeechVoice): string {
  return [voice.category, voice.language].filter(Boolean).join(' · ')
}

async function play(voice: SpeechVoice): Promise<void> {
  const request = ++playRequest
  const model = props.cast.model_id
  const key = sampleKey(voice)
  making.value = null
  const url = sampleUrl(voice)
  if (url) {
    player.toggle(voice.voice_id, url)
    return
  }
  player.stop()
  making.value = voice.voice_id
  try {
    const made = await audioApi.sample(voice.voice_id, model)
    if (request !== playRequest) return
    generated.value = { ...generated.value, [key]: made.url }
    player.toggle(voice.voice_id, made.url)
  } catch (exc) {
    if (request !== playRequest) return
    player.fail(voice.voice_id)
    emit('error', exc)
  } finally {
    if (request === playRequest) making.value = null
  }
}

function buttonLabel(voice: SpeechVoice): string {
  const state = player.stateOf(voice.voice_id)
  return fill(state === 'playing' || state === 'loading' ? labels.pause : labels.play, { name: voice.name })
}
</script>

<template>
  <div class="stack">
    <section class="param">
      <div class="param__head">
        <span class="param__label">{{ labels.cast }}</span>
      </div>
      <p class="hint">{{ labels.castLead }}</p>
      <div class="cast">
        <div v-for="speaker in speakers" :key="speaker" class="cast__row" :data-speaker="speaker">
          <span class="cast__who">
            {{ speaker }}
            <span v-if="!needed.includes(speaker)" class="meta">· {{ labels.notInSelection }}</span>
          </span>
          <select
            class="select"
            :value="voiceOf(speaker)"
            :aria-label="`${t.audio.voices}: ${speaker}`"
            :class="{ 'select--missing': needed.includes(speaker) && !voiceOf(speaker) }"
            @change="emit('assign', speaker, ($event.target as HTMLSelectElement).value)"
          >
            <option value="">{{ labels.voice }}</option>
            <option v-for="voice in voices" :key="voice.voice_id" :value="voice.voice_id">
              {{ voice.name }}{{ voice.language ? ` · ${voice.language}` : '' }}
            </option>
          </select>
          <PlayButton
            v-if="voiceById(voiceOf(speaker))"
            :state="player.stateOf(voiceOf(speaker))"
            :label="buttonLabel(voiceById(voiceOf(speaker))!)"
            :busy="making === voiceOf(speaker)"
            @click="play(voiceById(voiceOf(speaker))!)"
          />
          <span v-else class="play play--empty" aria-hidden="true" />
        </div>
      </div>
    </section>

    <section class="param">
      <div class="param__head">
        <span class="param__label">{{ labels.library }}</span>
        <span class="meta num">{{ voices.length }}</span>
        <span class="grow" />
        <input
          v-model="query"
          class="input filter"
          type="search"
          :placeholder="labels.filter"
          :aria-label="labels.filter"
        />
      </div>
      <p class="hint">{{ labels.libraryLead }}</p>
      <LoadingState v-if="loading" :label="labels.voicesLoading" />
      <p v-else-if="!voices.length" class="muted small">{{ labels.noVoices }}</p>
      <p v-else-if="!shown.length" class="muted small">{{ labels.noMatch }}</p>
      <ul class="library">
        <li
          v-for="voice in shown"
          :key="voice.voice_id"
          class="voice"
          :class="{ 'voice--on': player.current.value === voice.voice_id }"
          :data-voice="voice.voice_id"
        >
          <PlayButton
            :state="player.stateOf(voice.voice_id)"
            :label="buttonLabel(voice)"
            :busy="making === voice.voice_id"
            :title="sampleUrl(voice) ? undefined : labels.makeSampleHint"
            @click="play(voice)"
          />
          <div class="voice__body">
            <div class="voice__name">
              <b>{{ voice.name }}</b>
              <span v-if="facts(voice)" class="meta">{{ facts(voice) }}</span>
            </div>
            <p v-if="voice.description" class="voice__desc truncate" :title="voice.description">
              {{ voice.description }}
            </p>
            <p v-if="player.stateOf(voice.voice_id) === 'failed'" class="voice__fail" role="status">
              {{ labels.sampleFailed }}
            </p>
            <p v-else-if="!sampleUrl(voice)" class="meta">
              {{ fill(labels.makeSample, { cost: money((sampleCharacters / 1000) * rate) }) }}
            </p>
          </div>
          <div class="voice__assign">
            <button
              v-for="speaker in speakers"
              :key="speaker"
              class="chip"
              :aria-pressed="voiceOf(speaker) === voice.voice_id"
              @click="emit('assign', speaker, voiceOf(speaker) === voice.voice_id ? '' : voice.voice_id)"
            >
              {{ fill(labels.assign, { speaker }) }}<template v-if="voiceOf(speaker) === voice.voice_id"> ✓</template>
            </button>
          </div>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.param {
  padding: var(--s3) var(--s4);
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
}

.param__head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.param__label {
  font-weight: 600;
  font-size: var(--t-sm);
}

.hint {
  margin: 4px 0 var(--s3);
  font-size: var(--t-sm);
  color: var(--ink-3);
}

.small {
  font-size: var(--t-sm);
}

.cast {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.cast__row {
  display: grid;
  grid-template-columns: minmax(0, 11rem) minmax(0, 1fr) 34px;
  align-items: center;
  gap: var(--s3);
}

.cast__who {
  font-weight: 600;
  font-size: var(--t-sm);
}

.select--missing {
  border-color: var(--warn);
  box-shadow: 0 0 0 3px var(--warn-soft);
}

.filter {
  width: 14rem;
  padding: 4px 10px;
  font-size: var(--t-sm);
}

.library {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  overflow: hidden;
}

.voice {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--s3);
  padding: 10px var(--s3);
  background: var(--card);
}

.voice + .voice {
  border-top: 1px solid var(--rule);
}

.voice--on {
  background: var(--mark-soft);
}

.voice__body {
  min-width: 0;
}

.voice__name {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
  flex-wrap: wrap;
  font-size: var(--t-sm);
}

.voice__desc {
  margin: 2px 0 0;
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.voice__fail {
  margin: 2px 0 0;
  font-size: var(--t-xs);
  color: var(--fail);
}

.voice__assign {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.chip {
  padding: 3px 9px;
  border: 1px solid var(--rule-strong);
  border-radius: 999px;
  background: var(--card);
  color: var(--ink-2);
  font-size: var(--t-xs);
  cursor: pointer;
}

.chip:hover {
  border-color: var(--mark);
  color: var(--mark-deep);
}

.chip[aria-pressed='true'] {
  border-color: var(--mark);
  background: var(--mark);
  color: #fff;
}

.play--empty {
  width: 34px;
  height: 34px;
  border: 1px dashed var(--rule-strong);
  border-radius: 50%;
}
</style>
