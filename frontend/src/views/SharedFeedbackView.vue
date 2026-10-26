<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError } from '@/api/client'
import {
  sharedDate,
  sharedDuration,
  sharingApi,
  type FeedbackLine,
  type FeedbackScript,
  type FeedbackVoice,
  type LineReaction,
} from '@/api/sharing'
import { fill, t } from '@/i18n'

/**
 * One shared stand read as a script: every reviewer voice sits beside the
 * line it marks, and a strip above shows where in the episode the voices are.
 * The snapshot is frozen, so line ordinals and marks always match.
 */
const props = defineProps<{ id: string }>()

const data = ref<FeedbackScript | null>(null)
const error = ref('')
const hidden = ref<Set<number>>(new Set())
const selected = ref(0)
const focused = ref<number | null>(null)
const player = ref<HTMLAudioElement | null>(null)
const clock = ref<number | null>(null)

const reactions: Record<LineReaction, { emoji: string; label: string; tone: string }> = {
  impressed: { emoji: '👍', label: t.sharing.impressed, tone: 'pass' },
  dislike: { emoji: '🤢', label: t.sharing.dislike, tone: 'warn' },
  horrible: { emoji: '🤮', label: t.sharing.horrible, tone: 'fail' },
}
const comment = { emoji: '💬', label: t.sharedFeedback.commentOnly, tone: 'idle' }
const look = (voice: FeedbackVoice) => (voice.reaction ? reactions[voice.reaction] : comment)

const responses = computed(() => data.value?.link.feedback.responses ?? [])
const shown = computed(() => responses.value.filter((r) => !hidden.value.has(r.index)))
const episode = computed(() => data.value?.episodes[selected.value] ?? null)
const timed = computed(
  () =>
    !!episode.value?.audio &&
    episode.value.lines.every((line) => line.start_s != null && line.end_s != null),
)
const current = computed(() => {
  const time = clock.value
  if (time == null || !episode.value) return null
  return (
    episode.value.lines.find(
      (line) => line.start_s != null && line.end_s != null && time >= line.start_s && time < line.end_s,
    )?.ordinal ?? null
  )
})

function name(response: number): string {
  const row = responses.value.find((r) => r.index === response)
  return row?.label || fill(t.sharing.responseLabel, { n: response })
}
function key(line: FeedbackLine): string {
  return `e${episode.value?.index ?? 1}-s${String(line.ordinal).padStart(2, '0')}`
}
function voices(line: FeedbackLine): FeedbackVoice[] {
  return line.voices.filter((voice) => !hidden.value.has(voice.response))
}
function tally(list: FeedbackVoice[]) {
  const counts = { impressed: 0, dislike: 0, horrible: 0, slop: 0, comments: 0 }
  for (const voice of list) {
    if (voice.reaction) counts[voice.reaction] += 1
    if (voice.slop) counts.slop += 1
    if (voice.comment) counts.comments += 1
  }
  return counts
}
/** Stacked colour parts for the heat gutter and the strip, in reaction order. */
function heat(list: FeedbackVoice[]): { tone: string; share: number }[] {
  const counts = tally(list)
  const bare = list.filter((voice) => !voice.reaction).length
  return [
    { tone: 'pass', share: counts.impressed },
    { tone: 'warn', share: counts.dislike },
    { tone: 'fail', share: counts.horrible },
    { tone: 'idle', share: bare },
  ].filter((part) => part.share > 0)
}
function strength(count: number): number {
  return Math.min(1, 0.35 + count * 0.16)
}
function width(line: FeedbackLine): number {
  return timed.value ? Math.max(0.1, line.end_s! - line.start_s!) : 1
}

const totals = computed(() => tally(data.value?.episodes.flatMap((e) => e.lines.flatMap(voices)) ?? []))
const average = computed(() => {
  const rated = shown.value.filter((r) => r.stars != null)
  if (!rated.length) return null
  return rated.reduce((sum, r) => sum + (r.stars ?? 0), 0) / rated.length
})
const ratedCount = computed(() => shown.value.filter((r) => r.stars != null).length)

function stars(value: number | null): string {
  if (value == null) return '–'
  const halves = Math.round(value * 2)
  return '★'.repeat(Math.floor(halves / 2)) + (halves % 2 ? '½' : '')
}
function toggle(response: number): void {
  const next = new Set(hidden.value)
  if (next.has(response)) next.delete(response)
  else next.add(response)
  hidden.value = next
}
function jump(line: FeedbackLine): void {
  focused.value = line.ordinal
  document.getElementById(`line-${line.ordinal}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}
function play(line: FeedbackLine): void {
  const audio = player.value
  if (!audio || line.start_s == null) return
  audio.currentTime = line.start_s
  void audio.play().catch(() => undefined)
}
function updateClock(): void {
  clock.value = player.value?.currentTime ?? null
}

watch(selected, () => {
  player.value?.pause()
  clock.value = null
  focused.value = null
})

onMounted(async () => {
  try {
    data.value = await sharingApi.feedbackScript(props.id)
  } catch (exc) {
    error.value = exc instanceof ApiError && exc.detail ? exc.detail : t.sharedFeedback.loadError
  }
})
</script>

<template>
  <div class="page">
    <RouterLink class="back" :to="{ name: 'feedbacks' }">
      {{ t.sharedFeedback.back }}
    </RouterLink>
    <p v-if="error" class="notice notice--fail" role="alert">{{ error }}</p>
    <p v-else-if="!data" class="muted">{{ t.sharedFeedback.loading }}</p>
    <template v-else>
      <header>
        <p class="eyebrow">{{ t.sharedFeedback.title }}</p>
        <h1 class="h-page">{{ data.title }}</h1>
        <p class="meta">
          <span class="badge" :class="data.link.status === 'active' ? 'badge--pass' : 'badge--idle'">
            {{ t.sharing.status[data.link.status] }}
          </span>
          {{
            fill(t.sharing.dates, {
              created: sharedDate(data.link.created_at),
              expires: sharedDate(data.link.expires_at),
            })
          }}
          ·
          <RouterLink
            class="source"
            :to="
              data.target_kind === 'series'
                ? { name: 'series', params: { id: data.target_id } }
                : { name: 'run', params: { id: data.target_id }, query: { tab: 'feedback' } }
            "
          >
            {{ data.target_kind === 'series' ? t.sharedFeedback.toSeries : t.sharedFeedback.toRun }}
          </RouterLink>
        </p>
      </header>

      <p v-if="!responses.length" class="muted">{{ t.sharedFeedback.none }}</p>
      <template v-else>
        <section class="sheet questionnaire" :aria-label="t.sharedFeedback.questionnaire">
          <div class="score">
            <p class="eyebrow">{{ t.sharedFeedback.questionnaire }}</p>
            <p class="average">
              {{ average == null ? '–' : average.toLocaleString('de-DE', { maximumFractionDigits: 1 }) }}
            </p>
            <p class="stars">{{ stars(average) }}</p>
            <p class="meta">
              {{ fill(t.sharedFeedback.rated, { n: ratedCount, total: shown.length }) }}
            </p>
            <p class="totals">
              <span class="count count--pass">👍 {{ totals.impressed }}</span>
              <span class="count count--warn">🤢 {{ totals.dislike }}</span>
              <span class="count count--fail">🤮 {{ totals.horrible }}</span>
              <span class="count count--slop">{{ t.sharing.slop }} {{ totals.slop }}</span>
            </p>
          </div>
          <ul class="answers">
            <li v-for="response in shown" :key="response.index" class="answer">
              <span class="who">{{ name(response.index) }}</span>
              <span class="stars">{{ stars(response.stars) }}</span>
              <span class="said">
                <template v-if="response.worked || response.did_not">
                  <span v-if="response.worked"
                    ><i>{{ t.sharing.worked }}</i> {{ response.worked }}</span
                  >
                  <span v-if="response.did_not"
                    ><i>{{ t.sharing.didNot }}</i> {{ response.did_not }}</span
                  >
                </template>
                <i v-else>{{ t.sharedFeedback.noSheet }}</i>
              </span>
            </li>
          </ul>
        </section>

        <div class="people" role="group" :aria-label="t.sharedFeedback.people">
          <span class="eyebrow">{{ t.sharedFeedback.people }}</span>
          <button
            v-for="response in responses"
            :key="response.index"
            type="button"
            class="person"
            :aria-pressed="!hidden.has(response.index)"
            @click="toggle(response.index)"
          >
            {{ name(response.index) }}
          </button>
          <span class="meta">{{ t.sharedFeedback.unverifiedNames }}</span>
        </div>
      </template>

      <div
        v-if="data.episodes.length > 1"
        class="episodes"
        role="group"
        :aria-label="t.sharedFeedback.episode"
      >
        <button
          v-for="(item, position) in data.episodes"
          :key="item.index"
          type="button"
          class="btn btn--sm"
          :aria-pressed="selected === position"
          @click="selected = position"
        >
          {{ item.index }} · {{ item.title }}
        </button>
      </div>

      <section v-if="episode" class="episode">
        <div class="overview">
          <div class="strip" role="group" :aria-label="t.sharedFeedback.overview">
            <button
              v-for="line in episode.lines"
              :key="line.ordinal"
              type="button"
              class="segment"
              :class="{ current: current === line.ordinal, focused: focused === line.ordinal }"
              :style="{ flexGrow: width(line) }"
              :aria-label="fill(t.sharedFeedback.lineVoices, { key: key(line), n: voices(line).length })"
              :title="fill(t.sharedFeedback.lineVoices, { key: key(line), n: voices(line).length })"
              @click="jump(line)"
            >
              <span
                v-if="voices(line).length"
                class="fill"
                :style="{ height: `${Math.min(100, 25 + voices(line).length * 18)}%` }"
              >
                <i
                  v-for="part in heat(voices(line))"
                  :key="part.tone"
                  :class="`tone--${part.tone}`"
                  :style="{ flexGrow: part.share }"
                />
              </span>
            </button>
          </div>
          <p class="meta">
            {{ timed ? t.sharedFeedback.overviewTimed : t.sharedFeedback.overviewUntimed }}
          </p>
          <audio
            v-if="episode.audio"
            ref="player"
            :key="episode.audio.url"
            class="player"
            controls
            preload="metadata"
            :src="episode.audio.url"
            :aria-label="t.sharedFeedback.audio"
            @timeupdate="updateClock"
            @seeked="updateClock"
          />
          <p v-else class="meta">{{ t.sharedFeedback.noAudio }}</p>
        </div>

        <article
          v-for="line in episode.lines"
          :id="`line-${line.ordinal}`"
          :key="line.ordinal"
          class="line"
          :class="{
            quiet: !voices(line).length,
            current: current === line.ordinal,
            focused: focused === line.ordinal,
          }"
          :aria-current="current === line.ordinal ? 'true' : undefined"
        >
          <span class="heat" aria-hidden="true">
            <i
              v-for="part in heat(voices(line))"
              :key="part.tone"
              :class="`tone--${part.tone}`"
              :style="{ flexGrow: part.share, opacity: strength(voices(line).length) }"
            />
          </span>
          <div class="body">
            <p class="head">
              <span class="speaker">{{ line.speaker }}</span>
              <span class="key">{{ key(line) }}</span>
              <button
                v-if="episode.audio && line.start_s != null"
                type="button"
                class="play"
                :aria-label="fill(t.sharedFeedback.playFrom, { time: sharedDuration(line.start_s) })"
                @click="play(line)"
              >
                ▶ {{ sharedDuration(line.start_s) }}
              </button>
              <template v-if="voices(line).length">
                <span v-if="tally(voices(line)).impressed" class="count count--pass" :title="t.sharing.impressed"
                  >👍 {{ tally(voices(line)).impressed }}</span
                >
                <span v-if="tally(voices(line)).dislike" class="count count--warn" :title="t.sharing.dislike"
                  >🤢 {{ tally(voices(line)).dislike }}</span
                >
                <span v-if="tally(voices(line)).horrible" class="count count--fail" :title="t.sharing.horrible"
                  >🤮 {{ tally(voices(line)).horrible }}</span
                >
                <span v-if="tally(voices(line)).slop" class="count count--slop" :title="t.sharing.slop"
                  >{{ t.sharing.slop }} {{ tally(voices(line)).slop }}</span
                >
                <span v-if="tally(voices(line)).comments" class="count" :title="t.sharedFeedback.comments"
                  >💬 {{ tally(voices(line)).comments }}</span
                >
              </template>
            </p>
            <p class="text">{{ line.text }}</p>
          </div>
          <div class="margin">
            <div
              v-for="voice in voices(line).filter((v) => v.comment)"
              :key="`note-${voice.response}`"
              class="note"
              :class="`note--${look(voice).tone}`"
            >
              <p class="note-head">
                <span :title="look(voice).label">{{ look(voice).emoji }}</span>
                <b>{{ name(voice.response) }}</b>
                <span v-if="voice.slop" class="slop">{{ t.sharing.slop }}</span>
              </p>
              <p class="note-text">{{ voice.comment }}</p>
            </div>
            <p v-if="voices(line).some((v) => !v.comment)" class="quick">
              <span
                v-for="voice in voices(line).filter((v) => !v.comment)"
                :key="`mark-${voice.response}`"
                class="pill"
                :class="`note--${look(voice).tone}`"
                :title="look(voice).label"
              >
                {{ look(voice).emoji }} {{ name(voice.response) }}
                <span v-if="voice.slop" class="slop">{{ t.sharing.slop }}</span>
              </span>
            </p>
          </div>
        </article>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page {
  padding: var(--s6);
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--s4);
}

.source {
  color: inherit;
}

.back {
  color: var(--ink-3);
  font-size: var(--t-xs);
  text-decoration: none;
}

.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--s2);
}

.questionnaire {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: var(--s5);
  align-items: start;
}

.score {
  display: grid;
  gap: 2px;
  justify-items: start;
}

.average {
  font-family: var(--serif);
  font-size: var(--t-2xl);
  line-height: 1;
}

.stars {
  color: var(--warn);
  white-space: nowrap;
}

.totals {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s1);
  margin-top: var(--s1);
}

.answers {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s2);
  min-width: 0;
}

.answer {
  display: grid;
  grid-template-columns: 160px 72px minmax(0, 1fr);
  gap: var(--s3);
  font-size: var(--t-sm);
  align-items: baseline;
}

.who {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.said {
  display: grid;
  gap: 2px;
  color: var(--ink-2);
  overflow-wrap: anywhere;
}

.said i {
  font-style: normal;
  color: var(--ink-3);
}

.people,
.episodes {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--s2);
}

.person {
  border: 1px solid var(--rule-strong);
  background: var(--card);
  color: var(--ink);
  border-radius: 999px;
  padding: 2px 12px;
  font-size: var(--t-sm);
  cursor: pointer;
}

.person[aria-pressed='false'] {
  opacity: 0.45;
  text-decoration: line-through;
}

.episodes .btn[aria-pressed='true'] {
  border-color: var(--ink);
  box-shadow: inset 0 0 0 1px var(--ink);
}

.overview {
  position: sticky;
  top: 0;
  z-index: 1;
  display: grid;
  gap: var(--s2);
  padding: var(--s3) 0;
  background: var(--press);
  border-bottom: 1px solid var(--rule);
}

.strip {
  display: flex;
  height: 48px;
  border: 1px solid var(--rule-strong);
  border-radius: var(--r-sm);
  overflow: hidden;
  background: var(--sunk);
}

.segment {
  flex: 1 1 0;
  min-width: 2px;
  padding: 0;
  border: 0;
  border-right: 1px solid var(--press);
  background: transparent;
  cursor: pointer;
  display: flex;
  flex-direction: column-reverse;
}

.segment:hover {
  background: var(--chrome);
}

.segment.current,
.segment.focused {
  box-shadow: inset 0 0 0 2px var(--ink);
}

.fill {
  display: flex;
  flex-direction: column;
  width: 100%;
}

.fill i,
.heat i {
  display: block;
  flex: 1 1 0;
}

.tone--pass {
  background: var(--pass);
}

.tone--warn {
  background: var(--warn);
}

.tone--fail {
  background: var(--fail);
}

.tone--idle {
  background: var(--ink-4);
}

.player {
  width: 100%;
}

.line {
  display: grid;
  grid-template-columns: 10px minmax(0, 1.3fr) minmax(0, 1fr);
  gap: var(--s3);
  padding: var(--s2) 0;
  border-bottom: 1px solid var(--rule);
  scroll-margin-top: 160px;
}

.line.current,
.line.focused {
  background: var(--mark-soft);
}

.heat {
  display: flex;
  flex-direction: column;
  border-radius: 3px;
  overflow: hidden;
  background: var(--sunk);
}

.head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}

.speaker {
  font-size: var(--t-xs);
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--ink-3);
}

.key {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.play {
  border: 0;
  background: none;
  padding: 0;
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--mark);
  cursor: pointer;
}

.text {
  font-family: var(--serif);
  line-height: 1.55;
}

.line.quiet .text {
  color: var(--ink-2);
}

.count {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 0 6px;
  border-radius: 999px;
  font-family: var(--mono);
  font-size: var(--t-xs);
  line-height: 18px;
  background: var(--sunk);
  color: var(--ink-2);
}

.count--pass {
  background: var(--pass-soft);
  color: var(--pass);
}

.count--warn {
  background: var(--warn-soft);
  color: var(--warn);
}

.count--fail {
  background: var(--fail-soft);
  color: var(--fail);
}

.count--slop {
  background: var(--idle-soft);
  border: 1px solid var(--rule-strong);
}

.margin {
  display: grid;
  gap: var(--s1);
  align-content: start;
  min-width: 0;
}

.note {
  padding: 6px var(--s2);
  border: 1px solid var(--rule);
  border-left-width: 3px;
  border-radius: var(--r-md);
  background: var(--card);
  font-size: var(--t-sm);
  line-height: 1.4;
}

.note--pass {
  border-left-color: var(--pass);
}

.note--warn {
  border-left-color: var(--warn);
}

.note--fail {
  border-left-color: var(--fail);
}

.note--idle {
  border-left-color: var(--ink-4);
}

.note-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.note-head b {
  color: var(--ink);
}

.note-text {
  overflow-wrap: anywhere;
}

.quick {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s1);
}

.pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px;
  border: 1px solid var(--rule);
  border-radius: 999px;
  background: var(--card);
  font-size: var(--t-xs);
}

.pill.note--pass {
  border-color: var(--pass);
}

.pill.note--warn {
  border-color: var(--warn);
}

.pill.note--fail {
  border-color: var(--fail);
}

.slop {
  padding: 0 6px;
  border: 1px dashed var(--ink-3);
  border-radius: 999px;
  font-size: var(--t-xs);
  color: var(--ink-2);
}

@media (max-width: 900px) {
  .page {
    padding: var(--s4);
  }

  .questionnaire {
    grid-template-columns: minmax(0, 1fr);
  }

  .answer {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .said {
    grid-column: 1 / -1;
  }

  .line {
    grid-template-columns: 8px minmax(0, 1fr);
  }

  .margin {
    grid-column: 2;
  }
}
</style>
