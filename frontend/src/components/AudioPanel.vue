<script setup lang="ts">
/**
 * The run's audio: a one-minute sample or the whole episode.
 *
 * Preparing a take tags the script and prices it; the take then waits here
 * with the tagged lines, the voices per speaker and the price. Credits are
 * spent only after "Freigeben". While it is spoken the chunk list shows each
 * request; a take that failed continues without paying again for finished
 * chunks. The finished take plays as one file, and every line of the script
 * can be clicked to jump there; the line being heard is highlighted. Without an
 * ElevenLabs key the panel says how to set it up instead of failing.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import { audioApi, lineAt, takeActive, type AudioScope } from '@/api/audio'
import { ApiError } from '@/api/client'
import type { AudioStatus, AudioTakeOut, RunAudioOut, SpeechVoice, VoiceCast } from '@/api/types'
import ArtifactView from '@/components/ArtifactView.vue'
import ModalDialog from '@/components/ModalDialog.vue'
import { tagParts } from '@/components/artifactView'
import { fill, t } from '@/i18n'

const props = defineProps<{ runId: string; formatId: string | null }>()

const POLL_MS = 1500

const data = ref<RunAudioOut | null>(null)
const status = ref<AudioStatus | null>(null)
const voices = ref<SpeechVoice[] | null>(null)
const voicesUnavailable = ref(false)
const draft = ref<VoiceCast | null>(null)
const saveDefault = ref(true)
const busy = ref(false)
const error = ref('')
const confirmStop = ref(false)
const player = ref<HTMLAudioElement | null>(null)
const now = ref(0)
let timer: number | undefined

interface SpokenLine {
  segment_id: string
  speaker: string
  tagged: string
  start_s: number
}

const take = computed<AudioTakeOut | null>(() => data.value?.takes[0] ?? null)
const stoppedHint = computed(() => {
  const who = take.value?.stopped_by
  return who ? fill(t.audio.stoppedHintBy, { who }) : t.audio.stoppedHint
})
const active = computed(() => (take.value ? takeActive(take.value.status) : false))
const configured = computed(() => data.value?.configured ?? false)
const missing = computed(() => {
  const current = take.value
  if (!current || !draft.value) return []
  return current.speakers.filter((speaker) => !voiceOf(speaker).trim())
})
const canApprove = computed(
  () => configured.value && !busy.value && !!draft.value && missing.value.length === 0,
)

/** The tagged lines this take speaks: the sample, not the whole script. */
const sampleScript = computed(() => {
  const current = take.value
  const script = current?.audio_script as { lines?: { segment_id: string }[] } | null
  if (!current || !script?.lines) return null
  if (current.chunks.length) {
    const spoken = new Set(
      current.chunks.flatMap((chunk) => chunk.segment_ids.map((id) => id.split('#')[0])),
    )
    return { ...script, lines: script.lines.filter((line) => spoken.has(line.segment_id)) }
  }
  const count = current.approval?.lines ?? script.lines.length
  return { ...script, lines: script.lines.slice(0, count) }
})

/** The script's lines with where each is heard in the joined file. */
const spokenLines = computed<SpokenLine[]>(() => {
  const mix = take.value?.mix
  const script = take.value?.audio_script as {
    lines?: { segment_id: string; speaker: string; tagged: string }[]
  } | null
  if (!mix || !script?.lines) return []
  const starts = new Map(mix.lines.map((line) => [line.segment_id, line.start_s] as const))
  return script.lines
    .filter((line) => starts.has(line.segment_id))
    .map((line) => ({ ...line, start_s: starts.get(line.segment_id) ?? 0 }))
    .sort((a, b) => a.start_s - b.start_s)
})
const currentLine = computed(() => (take.value?.mix ? lineAt(take.value.mix.lines, now.value) : null))

function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

function onTime(): void {
  now.value = player.value?.currentTime ?? 0
}

function seek(line: SpokenLine): void {
  const audio = player.value
  if (!audio) return
  audio.currentTime = line.start_s
  now.value = line.start_s
  // Some browsers (and jsdom) return nothing from play(); only a promise can reject.
  const playing = audio.play() as Promise<void> | undefined
  if (playing) void playing.catch(() => undefined)
}

function badgeFor(status: string): string {
  if (status === 'done' || status === 'cached') return 'badge--pass'
  if (status === 'running') return 'badge--warn'
  if (status === 'failed') return 'badge--fail'
  return 'badge--idle'
}

function money(value: number): string {
  return `$${value.toFixed(2)}`
}

function fail(exc: unknown): void {
  error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
}

function voiceOf(speaker: string): string {
  return draft.value?.voices.find((v) => v.speaker === speaker)?.voice_id ?? ''
}

function setVoice(speaker: string, voiceId: string): void {
  if (!draft.value) return
  const name = voices.value?.find((v) => v.voice_id === voiceId)?.name ?? null
  const others = draft.value.voices.filter((v) => v.speaker !== speaker)
  draft.value = {
    ...draft.value,
    voices: [...others, { speaker, voice_id: voiceId, voice_name: name }],
  }
}

function preview(voiceId: string): void {
  const url = voices.value?.find((v) => v.voice_id === voiceId)?.preview_url
  if (url) void new Audio(url).play().catch(() => undefined)
}

async function reload(): Promise<void> {
  try {
    data.value = await audioApi.takes(props.runId)
  } catch (exc) {
    fail(exc)
  }
}

async function loadVoices(): Promise<void> {
  if (voices.value || !configured.value) return
  try {
    voices.value = await audioApi.voices()
  } catch {
    voicesUnavailable.value = true
  }
}

async function start(scope: AudioScope): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    await audioApi.start(props.runId, scope)
    await reload()
  } catch (exc) {
    fail(exc)
  } finally {
    busy.value = false
  }
}

async function stopTake(): Promise<void> {
  const current = take.value
  if (!current || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await audioApi.stop(current.id)
    confirmStop.value = false
    await reload()
  } catch (exc) {
    fail(exc)
  } finally {
    busy.value = false
  }
}

async function approve(): Promise<void> {
  const current = take.value
  if (!current || !draft.value || !canApprove.value) return
  busy.value = true
  error.value = ''
  try {
    if (saveDefault.value && props.formatId) {
      await audioApi.saveFormatVoices(props.formatId, draft.value)
    }
    await audioApi.approve(current.id, draft.value)
    await reload()
  } catch (exc) {
    fail(exc)
  } finally {
    busy.value = false
  }
}

async function resume(): Promise<void> {
  const current = take.value
  if (!current) return
  busy.value = true
  error.value = ''
  try {
    await audioApi.resume(current.id)
    await reload()
  } catch (exc) {
    fail(exc)
  } finally {
    busy.value = false
  }
}

function schedule(): void {
  window.clearTimeout(timer)
  if (active.value) timer = window.setTimeout(() => void reload(), POLL_MS)
}

watch(data, schedule)

// A take that comes to wait for approval starts from its own voices.
watch(
  () => (take.value?.status === 'paused' ? take.value.id : null),
  (waiting) => {
    if (!waiting || !take.value) return
    const cast = take.value.voice_cast
    draft.value = { ...cast, voices: [...cast.voices] }
    void loadVoices()
  },
)

onMounted(async () => {
  await reload()
  try {
    status.value = await audioApi.status()
  } catch {
    status.value = null
  }
})

onUnmounted(() => window.clearTimeout(timer))
</script>

<template>
  <section class="sheet audio">
    <div class="spread">
      <h2 class="h-section">{{ t.audio.title }}</h2>
      <span class="meta">{{ take?.flow_id ?? status?.flows[0]?.id ?? '' }}</span>
    </div>
    <p class="muted hint">{{ t.audio.lead }}</p>

    <p v-if="data && !configured" class="notice notice--warn" role="status">
      <b>{{ t.audio.notConfigured }}</b> {{ data.message }}
    </p>
    <p v-if="error" class="notice notice--fail" role="alert">{{ error }}</p>

    <div v-if="data && !take" class="row wrap">
      <button class="btn btn--mark" :disabled="busy" @click="start('sample')">
        {{ t.audio.prepare }}
      </button>
      <button class="btn" :disabled="busy" @click="start('full')">
        {{ t.audio.prepareFull }}
      </button>
      <span class="meta">{{ t.audio.prepareHint }}</span>
    </div>

    <template v-else-if="take">
      <p v-if="active" class="running">
        <span class="spinner" />{{ take.audio_script ? t.audio.speaking : t.audio.tagging }}
      </p>
      <div v-if="active || take.status === 'paused'" class="row wrap">
        <button class="btn btn--sm" type="button" data-stop @click="confirmStop = true">
          {{ t.audio.stop }}
        </button>
      </div>

      <div v-if="take.status === 'stopped'" class="notice" data-stopped>
        <span class="badge badge--idle">{{ t.audio.stopped }}</span>
        <p>{{ stoppedHint }}</p>
      </div>

      <div v-else-if="take.status === 'paused' && take.approval" class="approval">
        <div class="row wrap">
          <span class="badge badge--warn">{{ t.audio.waiting }}</span>
          <span class="badge badge--idle">
            {{ take.scope === 'full' ? t.audio.scopeFull : t.audio.scopeSample }}
          </span>
        </div>
        <dl class="facts">
          <div>
            <dt>{{ t.audio.lines }}</dt>
            <dd class="num">{{ take.approval.lines }}</dd>
          </div>
          <div>
            <dt>{{ t.audio.characters }}</dt>
            <dd class="num">{{ take.approval.characters.toLocaleString('de-DE') }}</dd>
          </div>
          <div>
            <dt>{{ t.audio.requests }}</dt>
            <dd class="num">{{ take.approval.requests }}</dd>
          </div>
          <div>
            <dt>{{ t.audio.price }}</dt>
            <dd class="num">≈ {{ money(take.approval.estimate_usd) }}</dd>
          </div>
        </dl>
        <p v-if="take.approval.cached_requests" class="meta">
          {{
            fill(t.audio.cached, {
              n: take.approval.cached_requests,
              total: take.approval.requests,
            })
          }}
        </p>

        <div v-if="draft" class="voices">
          <p class="eyebrow">{{ t.audio.voices }}</p>
          <p v-if="voicesUnavailable" class="meta">{{ t.audio.voicesUnavailable }}</p>
          <div v-for="speaker in take.speakers" :key="speaker" class="voice">
            <span class="voice__speaker">{{ speaker }}</span>
            <select
              v-if="voices && voices.length"
              class="select"
              :value="voiceOf(speaker)"
              :aria-label="`${t.audio.voices}: ${speaker}`"
              @change="setVoice(speaker, ($event.target as HTMLSelectElement).value)"
            >
              <option value="">{{ t.audio.voicePick }}</option>
              <option v-for="voice in voices" :key="voice.voice_id" :value="voice.voice_id">
                {{ voice.name }}{{ voice.language ? ` · ${voice.language}` : '' }}
              </option>
            </select>
            <input
              v-else
              class="input"
              :value="voiceOf(speaker)"
              :placeholder="t.audio.voiceId"
              :aria-label="`${t.audio.voiceId}: ${speaker}`"
              @input="setVoice(speaker, ($event.target as HTMLInputElement).value)"
            />
            <button
              v-if="voices?.find((v) => v.voice_id === voiceOf(speaker))?.preview_url"
              class="btn btn--sm"
              type="button"
              @click="preview(voiceOf(speaker))"
            >
              ▶ {{ t.audio.preview }}
            </button>
          </div>
          <label class="field">
            <span class="eyebrow">{{ t.audio.model }}</span>
            <select v-model="draft.model_id" class="select">
              <option v-for="model in status?.models ?? ['eleven_v4', 'eleven_v3']" :key="model">
                {{ model }}
              </option>
            </select>
          </label>
          <label v-if="formatId" class="check">
            <input v-model="saveDefault" type="checkbox" />
            <span>{{ t.audio.saveDefault }}</span>
          </label>
        </div>

        <p v-if="missing.length" class="notice notice--warn">
          {{ fill(t.audio.missingVoices, { names: missing.join(', ') }) }}
        </p>
        <div class="row wrap">
          <button class="btn btn--mark" :disabled="!canApprove" @click="approve">
            {{ fill(t.audio.approve, { price: money(take.approval.estimate_usd) }) }}
          </button>
          <span class="meta">{{ t.audio.approveHint }}</span>
        </div>
      </div>

      <div v-else-if="take.status === 'completed' && take.mix" class="player">
        <div class="row wrap">
          <span class="badge badge--pass">{{ t.audio.done }}</span>
          <span class="badge badge--idle">
            {{ take.scope === 'full' ? t.audio.scopeFull : t.audio.scopeSample }}
          </span>
          <span class="meta num">
            {{
              fill(t.audio.episodeLength, {
                duration: clock(take.mix.duration_s),
                lines: spokenLines.length,
                cost: money(take.total_cost_usd),
              })
            }}
          </span>
          <span class="grow" />
          <a class="btn btn--sm" :href="take.mix.download_url" download>{{ t.audio.download }}</a>
        </div>
        <audio
          ref="player"
          class="full"
          controls
          preload="metadata"
          :src="take.mix.url"
          @timeupdate="onTime"
          @seeked="onTime"
        />
        <p class="meta">{{ t.audio.jumpHint }}</p>
        <ol class="lines">
          <li
            v-for="line in spokenLines"
            :key="line.segment_id"
            :class="{ current: currentLine === line.segment_id }"
            :aria-current="currentLine === line.segment_id ? 'true' : undefined"
          >
            <button type="button" class="line" @click="seek(line)">
              <span class="line__time num">{{ clock(line.start_s) }}</span>
              <span class="line__speaker">{{ line.speaker }}</span>
              <span class="line__text prose">
                <template v-for="(part, index) in tagParts(line.tagged)" :key="index">
                  <span v-if="part.tag" class="line__tag">{{ part.text }}</span>
                  <template v-else>{{ part.text }}</template>
                </template>
              </span>
            </button>
          </li>
        </ol>
      </div>

      <div v-else-if="take.status === 'completed'" class="player">
        <span class="badge badge--pass">{{ t.audio.done }}</span>
        <div v-for="chunk in take.chunks" :key="chunk.index" class="chunk">
          <span class="meta">{{ fill(t.audio.chunk, { n: chunk.index + 1 }) }}</span>
          <audio controls preload="none" :src="chunk.url" />
          <span class="meta num">
            {{
              fill(t.audio.facts, {
                seconds: chunk.duration_s.toFixed(1),
                characters: chunk.characters.toLocaleString('de-DE'),
                cost: money(chunk.cost_usd),
              })
            }}
          </span>
        </div>
      </div>

      <div v-else-if="take.status === 'failed'" class="failed">
        <p class="notice notice--fail"><b>{{ t.audio.failed }}:</b> {{ take.error }}</p>
        <div v-if="take.resumable" class="row wrap">
          <button class="btn btn--mark" :disabled="busy || !configured" @click="resume">
            {{ t.audio.resume }}
          </button>
          <span class="meta">{{ t.audio.resumeHint }}</span>
        </div>
      </div>

      <details
        v-if="take.plan.length && !(take.status === 'completed' && take.mix)"
        class="plan"
        :open="take.status !== 'completed'"
      >
        <summary class="eyebrow">{{ t.audio.plan }} · {{ take.plan.length }}</summary>
        <ol class="chunks">
          <li v-for="chunk in take.plan" :key="chunk.index" :class="`chunk--${chunk.status}`">
            <span class="num">{{ chunk.index + 1 }}</span>
            <span class="meta num">{{ chunk.characters.toLocaleString('de-DE') }}</span>
            <span class="badge" :class="badgeFor(chunk.status)">
              {{ t.audio.chunkStatus[chunk.status] }}
            </span>
          </li>
        </ol>
      </details>

      <details
        v-if="sampleScript && !(take.status === 'completed' && take.mix)"
        class="script"
        :open="take.status === 'paused'"
      >
        <summary class="eyebrow">{{ t.audio.script }}</summary>
        <ArtifactView model="AudioScript" :payload="sampleScript" mode="text" />
      </details>

      <div
        v-if="take.status === 'completed' || take.status === 'failed' || take.status === 'stopped'"
        class="row wrap"
      >
        <button class="btn btn--sm" :disabled="busy" @click="start('sample')">
          {{ t.audio.again }}
        </button>
        <button class="btn btn--sm" :disabled="busy" @click="start('full')">
          {{ t.audio.prepareFull }}
        </button>
        <span class="meta num">{{ money(take.total_cost_usd) }}</span>
      </div>
    </template>

    <ModalDialog
      :open="confirmStop"
      :title="t.audio.stopTitle"
      :lead="t.audio.stopLead"
      @close="confirmStop = false"
    >
      <template #actions>
        <button class="btn btn--ghost" type="button" @click="confirmStop = false">
          {{ t.common.cancel }}
        </button>
        <button class="btn btn--mark" type="button" data-action="confirm-stop" :disabled="busy" @click="stopTake">
          {{ t.audio.stopConfirm }}
        </button>
      </template>
    </ModalDialog>
  </section>
</template>

<style scoped>
.audio {
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

.running {
  display: flex;
  align-items: center;
  gap: var(--s2);
  margin: 0;
  font-size: var(--t-sm);
  color: var(--ink-2);
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

.approval,
.player,
.voices {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  align-items: flex-start;
}

.facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  width: 100%;
  margin: 0;
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  overflow: hidden;
}

@media (max-width: 720px) {
  .facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.facts div {
  padding: var(--s2) var(--s3);
  border-right: 1px solid var(--rule);
  min-width: 0;
}

.facts div:last-child {
  border-right: 0;
}

.facts dt {
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.facts dd {
  margin: 0;
  font-family: var(--mono);
  font-size: var(--t-lg);
  font-weight: 600;
}

.voices {
  width: 100%;
}

.voice {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr) auto;
  gap: var(--s2);
  align-items: center;
  width: 100%;
}

@media (max-width: 720px) {
  .voice {
    grid-template-columns: minmax(0, 1fr);
  }
}

.voice__speaker {
  font-weight: 600;
  font-size: var(--t-sm);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 200px;
}

.check {
  display: flex;
  gap: var(--s2);
  align-items: center;
  font-size: var(--t-sm);
  color: var(--ink-2);
}

.check input {
  accent-color: var(--mark);
}

.chunk {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s3);
  align-items: center;
  width: 100%;
}

.chunk audio {
  flex: 1;
  min-width: 240px;
}

.full {
  width: 100%;
}

.lines {
  list-style: none;
  margin: 0;
  padding: 0;
  width: 100%;
  max-height: 460px;
  overflow-y: auto;
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
}

.lines li + li {
  border-top: 1px solid var(--rule);
}

.lines li.current {
  background: var(--mark-soft);
}

.line {
  display: grid;
  grid-template-columns: 44px 90px minmax(0, 1fr);
  gap: var(--s2);
  width: 100%;
  padding: var(--s2) var(--s3);
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  font: inherit;
}

.line:hover {
  background: var(--chrome);
}

.line__time {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-3);
  padding-top: 3px;
}

.line__speaker {
  font-size: var(--t-sm);
  font-weight: 600;
  color: var(--ink-2);
}

.line__text {
  min-width: 0;
  overflow-wrap: anywhere;
}

.line__tag {
  padding: 1px 5px;
  border-radius: var(--r-sm);
  background: var(--mark-soft);
  color: var(--mark-deep);
  font-family: var(--mono);
  font-size: 0.78em;
  white-space: nowrap;
}

@media (max-width: 720px) {
  .line {
    grid-template-columns: 44px minmax(0, 1fr);
  }

  .line__text {
    grid-column: 1 / -1;
  }
}

.failed {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.chunks {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: var(--s2);
}

.chunks li {
  display: flex;
  gap: var(--s2);
  align-items: center;
  padding: 4px var(--s2);
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  font-size: var(--t-sm);
}

.plan summary,
.script summary {
  cursor: pointer;
  margin-bottom: var(--s2);
}
</style>
