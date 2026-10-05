<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
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
const commentDraft = ref<Record<string, string>>({})
const player = ref<HTMLAudioElement | null>(null)
const audioFailed = ref(false)
const saveError = ref('')
const hydratedToken = ref<string | null>(null)
const sheetError = ref('')
const sheetOpen = ref(false)
const endedOffer = ref(false)
const sheetSent = ref(false)
const sheetBusy = ref(false)
const label = ref('') // Retain legacy stored labels internally, without offering a name field.
const commentOpen = ref<Record<string, boolean>>({})
const clock = ref<number | null>(null)
const currentSegment = computed(() => clock.value == null ? -1 :
  (episode.value?.segments.findIndex(segment => segment.start_s != null && segment.end_s != null &&
    clock.value! >= segment.start_s && clock.value! < segment.end_s) ?? -1))
function updateClock(): void { clock.value = player.value?.currentTime ?? null }
const script = ref<HTMLElement | null>(null)
function findPlaying(): void {
  const container = script.value
  const line = container?.querySelector<HTMLElement>('[aria-current="true"]')
  if (container && line) container.scrollTop += line.getBoundingClientRect().top - container.getBoundingClientRect().top
}
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
const writable = computed(() => Boolean(props.token))
const lineWritable = computed(() => writable.value && hydratedToken.value === props.token)
const starsText = computed(() =>
  stars.value == null
    ? t.sharing.starsNone
    : fill(t.sharing.starsValue, { n: stars.value.toLocaleString('de-DE') }),
)

watch(episode, () => {
  flushComments()
  clock.value = null
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
onMounted(() => {
  void loadFeedback()
  window.addEventListener('beforeunload', leaving)
})
onUnmounted(() => {
  flushComments()
  window.removeEventListener('beforeunload', leaving)
  player.value?.pause()
})
const pendingComments = new Map<string, { timer: ReturnType<typeof setTimeout>; save: () => void }>()
let pendingWrites = 0
function flushComments(): void {
  for (const { timer, save } of pendingComments.values()) { clearTimeout(timer); save() }
  pendingComments.clear()
}
function leaving(event: BeforeUnloadEvent): void {
  flushComments()
  if (pendingWrites) { event.preventDefault(); event.returnValue = '' }
}

function lineKey(index: number): string {
  const current = episode.value
  if (!current) return String(index)
  const ordinal = current.segments[index]?.ordinal ?? index
  return `${current.index}:${ordinal}`
}
function commentAt(index: number): string {
  return commentDraft.value[lineKey(index)] ?? markAt(index).comment
}
function draftComment(index: number, value: string): void {
  const context = lineContext(index)
  commentDraft.value = { ...commentDraft.value, [context.id]: value }
  const pending = pendingComments.get(context.id)
  if (pending) clearTimeout(pending.timer)
  const save = () => {
    const current = marks.value[context.id]
    if (current?.reaction && current.comment !== value) void persist(index, { ...current, comment: value }, context)
  }
  const timer = setTimeout(() => { pendingComments.delete(context.id); save() }, 350)
  pendingComments.set(context.id, { timer, save })
}
function ordinalOf(index: number): number {
  return episode.value?.segments[index]?.ordinal ?? index
}
function markAt(index: number): LocalMark {
  return (
    marks.value[lineKey(index)] ?? { reaction: null, slop: false, comment: '' }
  )
}
let writes: Promise<unknown> = Promise.resolve()
const versions: Record<string, number> = {}
let confirmed: Record<string, LocalMark> = {}
function ordered<T>(operation: () => Promise<T>): Promise<T> {
  pendingWrites++
  const result = writes.then(operation).finally(() => { pendingWrites-- })
  writes = result.catch(() => undefined)
  return result
}
function draft() {
  return { label: label.value, stars: stars.value, worked: worked.value, didNot: didNot.value }
}
function applySheet(data: FeedbackState, before: ReturnType<typeof draft>): void {
  if (label.value === before.label) label.value = data.label ?? ''
  if (stars.value === before.stars) stars.value = data.stars
  if (worked.value === before.worked) worked.value = data.worked ?? ''
  if (didNot.value === before.didNot) didNot.value = data.did_not ?? ''
}
function stateMarks(data: FeedbackState): Record<string, LocalMark> {
  const next: Record<string, LocalMark> = {}
  for (const mark of data.marks) {
    next[`${mark.episode}:${mark.ordinal}`] = {
      reaction: mark.reaction,
      slop: mark.slop,
      comment: mark.comment ?? '',
    }
  }
  return next
}
async function loadFeedback(): Promise<void> {
  hydratedToken.value = null
  if (!props.token) return
  const token = props.token
  const before = draft()
  try {
    const data = await ordered(() => sharingApi.feedback(token))
    if (props.token !== token) return
    confirmed = stateMarks(data)
    marks.value = { ...confirmed }
    applySheet(data, before)
    hydratedToken.value = token
    saveError.value = ''
  } catch {
    if (props.token === token) saveError.value = t.sharing.feedbackLoadError
  }
}
function lineContext(index: number) {
  return { episodeIndex: episode.value?.index, id: lineKey(index), token: props.token, ordinal: ordinalOf(index) }
}
async function persist(index: number, next: LocalMark, context = lineContext(index)): Promise<void> {
  const { episodeIndex, id, token, ordinal } = context
  if (!token || hydratedToken.value !== token || props.token !== token || episodeIndex == null) return
  const version = (versions[id] ?? 0) + 1
  versions[id] = version
  const previousDraft = commentDraft.value[id]
  if (!next.reaction) {
    const drafts = { ...commentDraft.value }
    delete drafts[id]
    commentDraft.value = drafts
  }
  marks.value = { ...marks.value, [id]: next }
  saveError.value = ''
  try {
    const saved = await ordered(() => sharingApi.saveMark(token, {
        episode: episodeIndex,
        ordinal,
        reaction: next.reaction,
        slop: next.slop,
        comment: next.comment.trim() || null,
      }))
    if (props.token !== token) return
    const stored = stateMarks(saved)[id]
    if (stored) confirmed[id] = stored
    else delete confirmed[id]
    if (versions[id] !== version) return
    const copy = { ...marks.value }
    if (stored) copy[id] = stored
    else delete copy[id]
    marks.value = copy
  } catch {
    if (props.token !== token || versions[id] !== version) return
    if (previousDraft !== undefined && commentDraft.value[id] === undefined) {
      commentDraft.value = { ...commentDraft.value, [id]: previousDraft }
    }
    const previous = confirmed[id]
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
  if (!lineWritable.value) return
  flushComments()
  const current = markAt(index)
  const next = current.reaction === reaction ? null : reaction
  void persist(index, {
    reaction: next,
    slop: next === 'dislike' || next === 'horrible' ? current.slop : false,
    comment: next ? commentAt(index) : '',
  })
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
}
function retry(): void {
  audioFailed.value = false
  player.value?.load()
}
function onEnded(): void {
  if (!writable.value) return
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
  if (!lineWritable.value || !props.token || sheetBusy.value) return
  flushComments()
  sheetBusy.value = true
  sheetError.value = ''
  const token = props.token
  const before = draft()
  const payload = {
      label: before.label.trim() || null,
      stars: before.stars,
      worked: before.worked.trim() || null,
      did_not: before.didNot.trim() || null,
  }
  try {
    const saved = await ordered(() => sharingApi.saveSheet(token, payload))
    if (props.token !== token) return
    applySheet(saved, before)
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
            @ended="updateClock(); onEnded()"
            @timeupdate="updateClock"
            @play="updateClock"
            @pause="updateClock"
            @seeking="updateClock"
            @seeked="updateClock"
            @loadedmetadata="updateClock"
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
        <button v-if="currentSegment >= 0" class="btn btn--sm" type="button" @click="findPlaying">{{ t.sharing.findPlaying }}</button>
        <div class="register">
          <div ref="script" class="script" tabindex="0" :aria-label="t.sharing.script">
            <p class="eyebrow script-label">{{ t.sharing.script }}</p>
            <p v-if="!episode.segments.length" class="empty">{{ t.sharing.empty }}</p>
            <section
              v-for="(segment, index) in episode.segments"
              :key="lineKey(index)"
              class="segment"
              :class="{ 'segment--playing': currentSegment === index }"
              :aria-current="currentSegment === index ? 'true' : undefined"
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
                  :disabled="!lineWritable"
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
                <button type="button" class="btn btn--sm comment-toggle"
                  :aria-expanded="!!commentOpen[lineKey(index)]"
                  @click="commentOpen[lineKey(index)] = !commentOpen[lineKey(index)]">
                  {{ t.sharing.comment }}
                </button>
                <label v-if="commentOpen[lineKey(index)]" class="field">
                  <span class="meta">{{ t.sharing.comment }}</span>
                  <textarea
                    class="textarea"
                    rows="2"
                    maxlength="500"
                    :value="commentAt(index)"
                    @input="draftComment(index, ($event.target as HTMLTextAreaElement).value)"
                    :disabled="!lineWritable"
                    @change="flushComments"
                    @blur="flushComments"
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
          :disabled="!lineWritable || sheetBusy"
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
  grid-template-columns: minmax(160px, 220px) minmax(0, 1fr);
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
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr);
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
  max-height: 65vh;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.segment--playing {
  background: var(--mark-soft);
  box-shadow: inset 3px 0 var(--mark);
  padding-left: var(--s3);
}
.source {
  max-height: 75vh;
  overflow-y: auto;
}
.source-bar {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--chrome);
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
