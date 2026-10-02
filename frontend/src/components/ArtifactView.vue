<script setup lang="ts">
/**
 * Read-only text view for an outline, a beat, or a script, with the raw JSON
 * one click away. The model name picks the renderer. Anything else, and any
 * payload that does not match, stays JSON.
 */
import { computed, ref } from 'vue'

import { t } from '@/i18n'

import {
  artifactMode,
  loadArtifactMode,
  parsePreview,
  pickRenderer,
  readBeat,
  readOutline,
  readScript,
  setArtifactMode,
  type BeatView,
  type SegmentView,
} from './artifactView'

const props = defineProps<{
  model: string
  preview?: string | null
  payload?: unknown
  truncated?: boolean
  /** JSON mode keeps an editable body, supplied by the parent slot. */
  editable?: boolean
}>()

loadArtifactMode()

const copied = ref(false)
const kind = computed(() => pickRenderer(props.model))

const parsed = computed(() => {
  if (props.truncated) return undefined
  if (typeof props.preview === 'string' && props.preview !== '') return parsePreview(props.preview)
  if (props.payload !== undefined && props.payload !== null) return props.payload
  return undefined
})

const beats = computed<BeatView[] | null>(() => {
  if (props.truncated || parsed.value === undefined) return null
  if (kind.value === 'outline') return readOutline(parsed.value)
  if (kind.value === 'beat') {
    const beat = readBeat(parsed.value)
    return beat ? [beat] : null
  }
  return null
})

const segments = computed<SegmentView[] | null>(() => {
  if (props.truncated || kind.value !== 'script' || parsed.value === undefined) return null
  return readScript(parsed.value)
})

const rendered = computed(() => beats.value !== null || segments.value !== null)
const showText = computed(() => artifactMode.value === 'text' && rendered.value)
const mismatch = computed(() => {
  if (kind.value === null || artifactMode.value !== 'text' || rendered.value) return false
  if (props.truncated) return true
  if (typeof props.preview === 'string' && props.preview !== '') return true
  return props.payload != null
})

const jsonText = computed(() => {
  if (typeof props.preview === 'string') return props.preview
  if (props.payload === undefined || props.payload === null) return ''
  return JSON.stringify(props.payload, null, 2)
})

const speakers = computed(() => {
  const index = new Map<string, number>()
  for (const segment of segments.value ?? []) {
    if (!index.has(segment.speaker)) index.set(segment.speaker, index.size)
  }
  return index
})

function speakerColor(name: string): string {
  return `var(--sp-${(speakers.value.get(name) ?? 0) % 3})`
}

function choose(next: 'text' | 'json'): void {
  copied.value = false
  setArtifactMode(next)
}

async function copyJson(): Promise<void> {
  try {
    await navigator.clipboard.writeText(jsonText.value)
    copied.value = true
  } catch {
    copied.value = false
  }
}
</script>

<template>
  <div class="artifact">
    <div class="artifact__bar">
      <p v-if="!kind" class="hint" role="status">{{ t.artifact.noTextView }}</p>
      <div v-else class="seg" role="group" :aria-label="t.artifact.view">
        <button type="button" :aria-pressed="artifactMode === 'text'" @click="choose('text')">
          {{ t.artifact.text }}
        </button>
        <button type="button" :aria-pressed="artifactMode === 'json'" @click="choose('json')">
          {{ t.artifact.json }}
        </button>
      </div>
    </div>

    <p v-if="mismatch" class="mismatch" role="status">{{ t.artifact.mismatch }}</p>

    <div v-if="showText && beats" class="beats">
      <article v-for="(beat, index) in beats" :key="beat.id" class="beat">
        <div class="beat__head">
          <span v-if="kind === 'outline'" class="beat__n num">{{ index + 1 }}</span>
          <span v-else class="meta">{{ beat.id }}</span>
          <span class="beat__title">{{ beat.title }}</span>
          <span class="grow" />
          <span class="num meta">{{ beat.word_budget }} {{ t.artifact.words }}</span>
        </div>
        <p v-if="beat.summary" class="beat__sum prose">{{ beat.summary }}</p>
        <div class="chips">
          <span class="meta">{{ t.artifact.passages }}</span>
          <span v-for="id in beat.block_ids" :key="id" class="chip">{{ id }}</span>
          <span v-if="beat.goal_id" class="meta">{{ t.artifact.goal }} {{ beat.goal_id }}</span>
        </div>
      </article>
    </div>

    <div v-else-if="showText && segments" class="dialog">
      <article
        v-for="segment in segments"
        :key="segment.id"
        class="script-seg"
        :style="{ '--speaker': speakerColor(segment.speaker) }"
      >
        <header class="script-seg__head">
          <span class="script-seg__speaker">{{ segment.speaker }}</span>
          <span class="badge" :class="segment.kind === 'claim' ? 'badge--mark' : 'badge--idle'">
            {{ segment.kind === 'claim' ? t.review.claim : t.review.pedagogy }}
          </span>
        </header>
        <p class="script-seg__text prose">{{ segment.text }}</p>
        <footer v-if="segment.anchors.length || segment.kind === 'claim'" class="script-seg__foot">
          <span
            v-for="(anchor, index) in segment.anchors"
            :key="index"
            class="cite"
          >
            <sup class="cite__n num">{{ index + 1 }}</sup>
            <span>{{ anchor.block_id }}</span>
            <span class="num">{{ anchor.char_start }}–{{ anchor.char_end }}</span>
          </span>
          <span v-if="segment.kind === 'claim' && !segment.anchors.length" class="meta">
            {{ t.review.noCitations }}
          </span>
        </footer>
      </article>
    </div>

    <div v-else class="jsonpane">
      <slot v-if="editable" name="json" />
      <pre v-else class="json scroll">{{ jsonText }}</pre>
      <div class="jsonbar">
        <button type="button" class="btn btn--sm" @click="copyJson">{{ t.artifact.copyJson }}</button>
        <span class="meta" role="status">{{ copied ? t.common.copied : '' }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.artifact {
  --sp-0: #2d5bff;
  --sp-1: #0d9488;
  --sp-2: #9a3f6a;
  display: grid;
  gap: var(--s2);
  min-width: 0;
}

.artifact__bar {
  display: flex;
  justify-content: flex-end;
}

.hint,
.mismatch {
  margin: 0;
  padding: var(--s2) var(--s3);
  border-radius: var(--r-md);
  background: var(--warn-soft);
  color: var(--warn);
  font-size: var(--t-xs);
}

.artifact__bar .hint {
  margin-right: auto;
}

.seg {
  display: inline-flex;
  border: 1px solid var(--rule-strong);
  border-radius: var(--r-md);
  overflow: hidden;
}

.seg button {
  border: 0;
  background: var(--card);
  padding: 3px 10px;
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.06em;
  color: var(--ink-2);
  cursor: pointer;
}

.seg button + button {
  border-left: 1px solid var(--rule-strong);
}

.seg button[aria-pressed='true'] {
  background: var(--ink);
  color: var(--chrome);
}

.beats {
  display: grid;
  gap: var(--s2);
}

.beat {
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  padding: var(--s3);
  display: grid;
  gap: 4px;
}

.beat__head {
  display: flex;
  align-items: baseline;
  gap: var(--s2);
  flex-wrap: wrap;
}

.beat__n {
  color: var(--ink-3);
  min-width: 1.2rem;
}

.beat__title {
  font-weight: 620;
}

.beat__sum {
  margin: 0;
  font-size: 1.02rem;
  line-height: 1.55;
}

.chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}

.chip {
  font-family: var(--mono);
  font-size: var(--t-xs);
  padding: 1px 6px;
  border-radius: var(--r-sm);
  background: var(--sunk);
  color: var(--ink-2);
}

.dialog {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

.script-seg {
  border: 1px solid var(--rule);
  border-left: 3px solid var(--speaker);
  border-radius: 0 var(--r-lg) var(--r-lg) 0;
  background: var(--card);
  padding: var(--s3) var(--s4);
  display: grid;
  gap: var(--s3);
}

.script-seg__head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.script-seg__speaker {
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--speaker);
}

.script-seg__text {
  margin: 0;
  font-size: 1.05rem;
  line-height: 1.6;
}

.script-seg__foot {
  display: flex;
  gap: var(--s2);
  flex-wrap: wrap;
  align-items: center;
  padding-top: var(--s2);
  border-top: 1px solid var(--rule);
}

.cite {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  padding: 2px 7px;
  border: 1px solid var(--rule-strong);
  border-radius: 999px;
  background: var(--card);
  font-family: var(--mono);
  font-size: 0.625rem;
  color: var(--ink-2);
}

.json {
  margin: 0;
  padding: var(--s3);
  max-height: 340px;
  border-radius: var(--r-md);
  background: var(--sunk);
  font-family: var(--mono);
  font-size: var(--t-xs);
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.jsonpane {
  display: grid;
  gap: var(--s2);
  min-width: 0;
}

.jsonbar {
  display: flex;
  align-items: center;
  gap: var(--s2);
}
</style>
