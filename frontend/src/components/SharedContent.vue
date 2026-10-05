<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  rememberedLabel,
  rememberLabel,
  sharedDate,
  sharedDuration,
  sharingApi,
  type FeedbackState,
  type LineReaction,
  type SharedCitation,
  type SharedSnapshot,
} from '@/api/sharing'
import CitedPage from '@/components/CitedPage.vue'
import ModalDialog from '@/components/ModalDialog.vue'

import { fill, t } from '@/i18n'

const props = defineProps<{
  snapshot: SharedSnapshot
  selected: number
  token?: string | null
}>()
const emit = defineEmits<{ (e: 'select', index: number): void }>()

const reactions: { id: LineReaction; emoji: string; label: string; tone: string }[] = [
  { id: 'impressed', emoji: '👍', label: t.sharing.impressed, tone: 'pass' },
  { id: 'dislike', emoji: '🤢', label: t.sharing.dislike, tone: 'warn' },
  { id: 'horrible', emoji: '🤮', label: t.sharing.horrible, tone: 'fail' },
]

interface LocalMark {
  reaction: LineReaction | null
  slop: boolean
  comment: string
}
const marks = ref<Record<string, LocalMark>>({})
const player = ref<HTMLAudioElement | null>(null)
const audioFailed = ref(false)
const saveError = ref('')
const sheetError = ref('')
const sheetOpen = ref(false)
const endedOffer = ref(false)
const sheetSent = ref(false)
const sheetBusy = ref(false)
const label = ref('')
const stars = ref<number | null>(null)
const worked = ref('')
const didNot = ref('')
const activePage = ref<number | null>(null)
const activeRects = ref<SharedCitation['rects']>([])
const activeCite = ref('')

const position = computed(() =>
  Math.max(
    0,
    props.snapshot.episodes.findIndex((e) => e.index === props.selected),
  ),
)
const episode = computed(() => props.snapshot.episodes[position.value])
const pages = computed(() => episode.value?.pages ?? [])
const sourcePage = computed(() => {
  if (!pages.value.length) return null
  return pages.value.find((page) => page.page === activePage.value) ?? pages.value[0]
})
const pagePos = computed(() =>
  pages.value.findIndex((page) => page.page === sourcePage.value?.page),
)
const lastAudio = computed(
  () => [...props.snapshot.episodes].reverse().find((item) => item.audio)?.index ?? null,
)
const writable = computed(() => Boolean(props.token))
const starsText = computed(() =>
  stars.value == null
    ? t.sharing.starsNone
    : fill(t.sharing.starsValue, { n: stars.value.toLocaleString('de-DE') }),
)

watch(episode, () => {
  player.value?.pause()
  audioFailed.value = false
  activePage.value = null
  activeRects.value = []
  activeCite.value = ''
})
watch(
  () => props.token,
  () => void loadFeedback(),
)
onMounted(() => void loadFeedback())
onUnmounted(() => player.value?.pause())

function lineKey(index: number): string {
  const current = episode.value
  if (!current) return String(index)
  const ordinal = current.segments[index]?.ordinal ?? index
  return `${current.index}:${ordinal}`
}
function ordinalOf(index: number): number {
  return episode.value?.segments[index]?.ordinal ?? index
}
function markAt(index: number): LocalMark {
  return (
    marks.value[lineKey(index)] ?? { reaction: null, slop: false, comment: '' }
  )
}
function applyState(data: FeedbackState): void {
  const next: Record<string, LocalMark> = {}
  for (const mark of data.marks) {
    next[`${mark.episode}:${mark.ordinal}`] = {
      reaction: mark.reaction,
      slop: mark.slop,
      comment: mark.comment ?? '',
    }
  }
  marks.value = next
  label.value = data.label ?? rememberedLabel()
  stars.value = data.stars
  worked.value = data.worked ?? ''
  didNot.value = data.did_not ?? ''
}
async function loadFeedback(): Promise<void> {
  if (!props.token) return
  try {
    applyState(await sharingApi.feedback(props.token))
  } catch {
    label.value = label.value || rememberedLabel()
  }
}
async function persist(index: number, next: LocalMark): Promise<void> {
  const episodeIndex = episode.value?.index
  if (!props.token || episodeIndex == null) return
  const id = lineKey(index)
  const previous = marks.value[id]
  marks.value = { ...marks.value, [id]: next }
  saveError.value = ''
  try {
    applyState(
      await sharingApi.saveMark(props.token, {
        episode: episodeIndex,
        ordinal: ordinalOf(index),
        reaction: next.reaction,
        slop: next.slop,
        comment: next.comment.trim() || null,
      }),
    )
  } catch {
    if (previous) marks.value = { ...marks.value, [id]: previous }
    else {
      const copy = { ...marks.value }
      delete copy[id]
      marks.value = copy
    }
    saveError.value = t.sharing.saveError
  }
}
function toggle(index: number, reaction: LineReaction): void {
  if (!writable.value) return
  const current = markAt(index)
  const next = current.reaction === reaction ? null : reaction
  void persist(index, {
    reaction: next,
    slop: next === 'dislike' || next === 'horrible' ? current.slop : false,
    comment: next ? current.comment : '',
  })
}
function toggleSlop(index: number): void {
  const current = markAt(index)
  if (current.reaction !== 'dislike' && current.reaction !== 'horrible') return
  void persist(index, { ...current, slop: !current.slop })
}
function saveComment(index: number, value: string): void {
  const current = markAt(index)
  if (!current.reaction || value === current.comment) return
  void persist(index, { ...current, comment: value })
}
function showCite(index: number, citeIndex: number, citation: SharedCitation): void {
  const page = citation.rects[0]?.page
  if (page == null) return
  activePage.value = page
  activeRects.value = citation.rects
  activeCite.value = `${lineKey(index)}:${citeIndex}`
}
function stepPage(delta: number): void {
  const next = pages.value[pagePos.value + delta]
  if (!next) return
  activePage.value = next.page
  activeRects.value = activeRects.value.filter((rect) => rect.page === next.page)
  activeCite.value = ''
}
function retry(): void {
  audioFailed.value = false
  player.value?.load()
}
function onEnded(): void {
  if (!writable.value || episode.value?.index !== lastAudio.value) return
  endedOffer.value = true
  sheetSent.value = false
  sheetOpen.value = true
}
function openSheet(): void {
  endedOffer.value = false
  sheetSent.value = false
  sheetOpen.value = true
}
function cycleStar(n: number): void {
  if (stars.value === n) stars.value = n - 0.5
  else if (stars.value === n - 0.5) stars.value = null
  else stars.value = n
}
function starFill(n: number): string {
  if (stars.value == null) return '0%'
  if (stars.value >= n) return '100%'
  if (stars.value + 0.001 >= n - 0.5 && stars.value < n) return '50%'
  return '0%'
}
async function sendSheet(): Promise<void> {
  if (!props.token || sheetBusy.value) return
  sheetBusy.value = true
  sheetError.value = ''
  try {
    const saved = await sharingApi.saveSheet(props.token, {
      label: label.value.trim() || null,
      stars: stars.value,
      worked: worked.value.trim() || null,
      did_not: didNot.value.trim() || null,
    })
    applyState(saved)
    rememberLabel(saved.label ?? '')
    sheetSent.value = true
  } catch {
    sheetError.value = t.sharing.sheetError
  } finally {
    sheetBusy.value = false
  }
}
</script>

<template>
  <div class="shared-content">
    <header class="head">
      <div class="spread wrap">
        <p class="eyebrow">{{ t.sharing.intro }}</p>
        <button class="btn btn--sm" type="button" @click="openSheet">
          {{ t.sharing.feedback }}
        </button>
      </div>
      <h1>{{ snapshot.title }}</h1>
      <p class="meta">
        {{
          fill(t.sharing.readerDates, {
            created: sharedDate(snapshot.created_at),
            expires: sharedDate(snapshot.expires_at),
          })
        }}
      </p>
    </header>
    <p v-if="!snapshot.episodes.length" class="empty">{{ t.sharing.empty }}</p>
    <div v-else class="layout" :class="{ single: snapshot.episodes.length === 1 }">
      <nav
        v-if="snapshot.episodes.length > 1"
        class="episodes"
        :aria-label="t.sharing.manage"
      >
        <p class="eyebrow">
          {{ fill(t.sharing.episodes, { n: snapshot.episodes.length }) }}
        </p>
        <button
          v-for="item in snapshot.episodes"
          :key="item.index"
          type="button"
          :aria-current="item.index === episode.index ? 'true' : undefined"
          @click="emit('select', item.index)"
        >
          <span class="num">{{ item.index }}</span
          ><span
            ><b>{{ item.title }}</b
            ><small>{{
              item.audio ? t.sharing.audioPresent : t.sharing.scriptOnly
            }}</small></span
          >
        </button>
      </nav>
      <article v-if="episode" class="reading">
        <div v-if="snapshot.episodes.length > 1" class="mobile-select field">
          <label for="shared-episode">{{ t.sharing.selectEpisode }}</label>
          <select
            id="shared-episode"
            class="select"
            :value="episode.index"
            @change="emit('select', Number(($event.target as HTMLSelectElement).value))"
          >
            <option v-for="item in snapshot.episodes" :key="item.index" :value="item.index">
              {{ item.index }} · {{ item.title
              }}{{ item.audio ? '' : ' · ' + t.sharing.scriptOnly }}
            </option>
          </select>
        </div>
        <p class="eyebrow">
          {{ fill(t.sharing.progress, { n: position + 1, total: snapshot.episodes.length }) }}
        </p>
        <h2 class="h-section">{{ episode.title }}</h2>
        <div v-if="episode.audio" class="audio">
          <div class="row wrap">
            <span class="badge badge--pass">{{ t.sharing.fullEpisode }}</span
            ><span class="meta num">{{ sharedDuration(episode.audio.duration_s) }}</span>
          </div>
          <audio
            :key="episode.index + ':' + episode.audio.url"
            ref="player"
            controls
            preload="metadata"
            :src="episode.audio.url"
            :aria-label="t.sharing.audioLabel"
            @error="audioFailed = true"
            @ended="onEnded"
          />
          <div v-if="audioFailed" class="notice notice--fail" role="alert">
            <p>{{ t.sharing.playbackError }}</p>
            <button class="btn btn--sm" type="button" @click="retry">
              {{ t.sharing.retryAudio }}
            </button>
          </div>
        </div>
        <p v-else class="notice" role="status">{{ t.sharing.noAudio }}</p>
        <p v-if="saveError" class="notice notice--fail" role="alert">{{ saveError }}</p>
        <p v-if="!writable" class="hint">{{ t.sharing.previewMarks }}</p>
        <div class="register">
          <div class="script">
            <p class="eyebrow script-label">{{ t.sharing.script }}</p>
            <p v-if="!episode.segments.length" class="empty">{{ t.sharing.empty }}</p>
            <section
              v-for="(segment, index) in episode.segments"
              :key="lineKey(index)"
              class="segment"
            >
              <h3>{{ segment.speaker }}</h3>
              <p class="prose">{{ segment.text }}</p>
              <div v-if="segment.citations?.length" class="cites">
                <button
                  v-for="(citation, citeIndex) in segment.citations"
                  :key="citeIndex"
                  type="button"
                  class="btn btn--sm"
                  :aria-pressed="activeCite === `${lineKey(index)}:${citeIndex}`"
                  @click="showCite(index, citeIndex, citation)"
                >
                  {{ fill(t.sharing.cite, { n: (citation.rects[0]?.page ?? 0) + 1 }) }}
                </button>
              </div>
              <div
                class="reacts"
                role="group"
                :aria-label="segment.speaker"
              >
                <button
                  v-for="item in reactions"
                  :key="item.id"
                  type="button"
                  class="btn btn--sm react"
                  :class="[`react--${item.tone}`, { 'react--on': markAt(index).reaction === item.id }]"
                  :aria-pressed="markAt(index).reaction === item.id"
                  :aria-label="item.label"
                  :disabled="!writable"
                  @click="toggle(index, item.id)"
                >
                  <span aria-hidden="true">{{ item.emoji }}</span>
                  <span class="sr-only">{{ item.label }}</span>
                </button>
              </div>
              <div
                v-if="markAt(index).reaction"
                class="extra"
              >
                <button
                  v-if="markAt(index).reaction !== 'impressed'"
                  type="button"
                  class="btn btn--sm"
                  :aria-pressed="markAt(index).slop"
                  :disabled="!writable"
                  @click="toggleSlop(index)"
                >
                  {{ t.sharing.slop }}
                </button>
                <label class="field">
                  <span class="meta">{{ t.sharing.comment }}</span>
                  <textarea
                    class="textarea"
                    rows="2"
                    maxlength="500"
                    :value="markAt(index).comment"
                    :disabled="!writable"
                    @change="saveComment(index, ($event.target as HTMLTextAreaElement).value)"
                  />
                </label>
              </div>
            </section>
          </div>
          <aside class="source">
            <div class="source-bar">
              <span class="eyebrow">{{ t.sharing.source }}</span>
              <span class="grow" />
              <button
                class="btn btn--sm"
                type="button"
                :disabled="pagePos <= 0"
                @click="stepPage(-1)"
              >
                ←
              </button>
              <span v-if="sourcePage" class="num">
                {{
                  fill(t.sharing.citedPage, {
                    n: sourcePage.page + 1,
                    pos: pagePos + 1,
                    total: pages.length,
                  })
                }}
              </span>
              <button
                class="btn btn--sm"
                type="button"
                :disabled="pagePos < 0 || pagePos >= pages.length - 1"
                @click="stepPage(1)"
              >
                →
              </button>
            </div>
            <CitedPage
              v-if="sourcePage"
              :url="sourcePage.url"
              :width="sourcePage.width"
              :height="sourcePage.height"
              :page="sourcePage.page"
              :highlight="activeRects"
            />
            <p v-else class="hint source-empty">{{ t.sharing.noSource }}</p>
            <p v-if="sourcePage && !activeRects.length" class="hint source-empty">
              {{ t.sharing.sourceHint }}
            </p>
          </aside>
        </div>
        <footer v-if="snapshot.episodes.length > 1" class="spread wrap">
          <button
            class="btn"
            type="button"
            :disabled="position === 0"
            @click="emit('select', snapshot.episodes[position - 1].index)"
          >
            {{ t.sharing.previous }}
          </button>
          <button
            class="btn"
            type="button"
            :disabled="position === snapshot.episodes.length - 1"
            @click="emit('select', snapshot.episodes[position + 1].index)"
          >
            {{ t.sharing.next }}
          </button>
        </footer>
      </article>
    </div>

    <ModalDialog
      :open="sheetOpen"
      :title="t.sharing.sheetTitle"
      :lead="endedOffer ? t.sharing.sheetEndedLead : t.sharing.sheetLead"
      @close="sheetOpen = false"
    >
      <div class="stack">
        <div class="field">
          <span id="stars-label">{{ t.sharing.stars }}</span>
          <div class="stars" role="group" aria-labelledby="stars-label" :aria-label="starsText">
            <button
              v-for="n in 5"
              :key="n"
              type="button"
              class="star"
              :aria-label="fill(t.sharing.starsValue, { n })"
              :disabled="!writable"
              @click="cycleStar(n)"
            >
              <span class="star-bg" aria-hidden="true">★</span>
              <span class="star-fg" aria-hidden="true" :style="{ width: starFill(n) }">★</span>
            </button>
            <span class="meta">{{ starsText }}</span>
          </div>
        </div>
        <div class="field">
          <label for="share-label">{{ t.sharing.label }}</label>
          <input
            id="share-label"
            v-model="label"
            class="input"
            maxlength="40"
            :disabled="!writable"
          />
          <p class="hint">{{ t.sharing.labelHint }}</p>
        </div>
        <div class="field">
          <label for="share-worked">{{ t.sharing.worked }}</label>
          <textarea
            id="share-worked"
            v-model="worked"
            class="textarea"
            rows="3"
            maxlength="2000"
            :disabled="!writable"
          />
        </div>
        <div class="field">
          <label for="share-did-not">{{ t.sharing.didNot }}</label>
          <textarea
            id="share-did-not"
            v-model="didNot"
            class="textarea"
            rows="3"
            maxlength="2000"
            :disabled="!writable"
          />
        </div>
        <p v-if="sheetSent" role="status">{{ t.sharing.sheetSent }}</p>
        <p v-if="sheetError" class="notice notice--fail" role="alert">{{ sheetError }}</p>
      </div>
      <template #actions>
        <button class="btn" type="button" @click="sheetOpen = false">{{ t.common.close }}</button>
        <button
          class="btn btn--primary"
          type="button"
          :disabled="!writable || sheetBusy"
          @click="sendSheet"
        >
          {{ t.sharing.sendSheet }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.shared-content {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  overflow: hidden;
}
.head {
  padding: var(--s6);
  border-bottom: 1px solid var(--rule);
}
h1 {
  font: 2.3rem/1.2 var(--serif);
  letter-spacing: -0.025em;
  margin: var(--s3) 0;
  overflow-wrap: anywhere;
}
.meta,
.hint {
  overflow-wrap: anywhere;
}
.hint {
  color: var(--ink-2);
  font-size: var(--t-sm);
  margin: var(--s3) 0;
}
.layout {
  display: grid;
  grid-template-columns: minmax(0, 0.65fr) minmax(0, 1.8fr);
}
.layout > * {
  min-width: 0;
}
.layout.single {
  grid-template-columns: minmax(0, 1fr);
}
.episodes {
  padding: var(--s4);
  background: var(--chrome);
  border-right: 1px solid var(--rule);
}
.episodes > p {
  margin: var(--s2) var(--s2) var(--s4);
}
.episodes button {
  width: 100%;
  display: flex;
  gap: var(--s3);
  text-align: left;
  padding: var(--s3);
  border: 1px solid transparent;
  border-radius: var(--r-md);
  background: transparent;
  cursor: pointer;
}
.episodes button[aria-current] {
  background: var(--mark-soft);
  border-color: var(--rule);
}
.episodes b {
  font-size: var(--t-sm);
  font-weight: 550;
  overflow-wrap: anywhere;
}
.episodes small {
  display: block;
  color: var(--ink-2);
  font: var(--t-xs) var(--mono);
  margin-top: 6px;
}
.reading {
  padding: var(--s6);
}
h2 {
  margin: var(--s2) 0 var(--s4);
  overflow-wrap: anywhere;
}
audio {
  display: block;
  width: 100%;
  margin: var(--s4) 0;
}
.notice {
  padding: var(--s4);
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  background: var(--chrome);
  font-size: var(--t-sm);
}
.notice--fail {
  color: var(--fail);
  border-color: var(--fail);
  background: var(--fail-soft);
}
.notice button {
  margin-top: var(--s3);
}
.register {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
  gap: 0;
  border-top: 1px solid var(--rule);
  margin-top: var(--s4);
}
.script,
.source {
  min-width: 0;
}
.script {
  padding-right: var(--s4);
}
.source {
  border-left: 1px solid var(--rule);
  background: var(--chrome);
}
.source-bar {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: var(--s3);
  border-bottom: 1px solid var(--rule);
}
.source-empty {
  padding: 0 var(--s4);
}
.script-label {
  margin: var(--s5) 0;
}
.segment {
  padding-bottom: var(--s5);
  margin-bottom: var(--s5);
  border-bottom: 1px solid var(--rule);
}
.segment h3 {
  font-size: var(--t-sm);
  margin-bottom: var(--s2);
}
.segment p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.cites,
.reacts,
.extra {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s2);
  margin-top: var(--s3);
}
.extra {
  display: grid;
}
.react--on.react--pass {
  background: var(--pass-soft);
  border-color: var(--pass);
}
.react--on.react--warn {
  background: var(--warn-soft);
  border-color: var(--warn);
}
.react--on.react--fail {
  background: var(--fail-soft);
  border-color: var(--fail);
}
.stars {
  display: flex;
  align-items: center;
  gap: var(--s2);
  margin-top: var(--s2);
}
.star {
  position: relative;
  width: 1.6rem;
  height: 1.6rem;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  font-size: 1.5rem;
  line-height: 1;
}
.star-bg,
.star-fg {
  position: absolute;
  inset: 0;
  overflow: hidden;
}
.star-bg {
  color: var(--rule-strong);
}
.star-fg {
  color: var(--mark);
  white-space: nowrap;
}
.empty {
  padding: var(--s5);
  color: var(--ink-2);
}
.mobile-select {
  display: none;
  margin-bottom: var(--s5);
}
.reading footer {
  margin-top: var(--s4);
}
@media (max-width: 760px) {
  .layout,
  .register {
    grid-template-columns: minmax(0, 1fr);
  }
  .episodes {
    display: none;
  }
  .mobile-select {
    display: flex;
  }
  .source {
    border-left: 0;
    border-top: 1px solid var(--rule);
    min-height: 50vh;
  }
  .script {
    padding-right: 0;
  }
  .head,
  .reading {
    padding: var(--s5);
  }
  h1 {
    font-size: 1.9rem;
  }
}
</style>
