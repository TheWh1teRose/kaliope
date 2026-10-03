/**
 * Which artifact models have a text view, and whether a payload matches.
 *
 * The model name comes from the node's output class. Shape is checked only
 * after that name matches, so a JSON object that happens to contain `speaker`
 * is never treated as a script.
 */
import { ref } from 'vue'

export type RendererName = 'outline' | 'beat' | 'script' | 'selection' | 'audio' | 'series'

export interface BeatView {
  id: string
  title: string
  block_ids: string[]
  word_budget: number
  goal_id: string | null
  summary: string | null
}

export interface GoalView {
  id: string
  text: string
  /** The chosen passages that serve this goal, in the answer's order. */
  block_ids: string[]
}

export interface SelectionView {
  goals: GoalView[]
  /** Chosen passages that serve no known goal. */
  unassigned: string[]
  rationale: string
}

export interface SeriesGoalView {
  text: string
  bloom: string | null
}

export interface SeriesEpisodeView {
  title: string
  role: string
  goals: SeriesGoalView[]
  block_ids: string[]
}

export interface SeriesPlanView {
  title: string
  throughLine: string
  episodes: SeriesEpisodeView[]
}

export interface AnchorView {
  block_id: string
  char_start: number
  char_end: number
}

export interface SegmentView {
  id: string
  speaker: string
  text: string
  kind: 'claim' | 'pedagogy'
  anchors: AnchorView[]
  beat_id: string
}

export interface SpokenFormView {
  original: string
  spoken: string
}

export interface AudioLineView {
  segment_id: string
  speaker: string
  kind: string | null
  text: string
  tagged: string
  spoken_forms: SpokenFormView[]
  tags: string[]
  guard: 'pass' | 'fallback'
  problem: string | null
}

/** A stretch of a tagged line: a tag in square brackets, or the words between. */
export interface TagPart {
  text: string
  tag: boolean
}

const RENDERERS: Record<string, RendererName> = {
  AudioScript: 'audio',
  Outline: 'outline',
  Beat: 'beat',
  Script: 'script',
  Selection: 'selection',
  SeriesPlan: 'series',
}

const STORAGE_KEY = 'kalliope-artifact-view'

export type ArtifactMode = 'text' | 'json'

/** Shared by every card. The last click is what the next card opens with. */
export const artifactMode = ref<ArtifactMode>('text')

export function pickRenderer(model: string): RendererName | null {
  return RENDERERS[model] ?? null
}

export function loadArtifactMode(): void {
  artifactMode.value = localStorage.getItem(STORAGE_KEY) === 'json' ? 'json' : 'text'
}

export function setArtifactMode(next: ArtifactMode): void {
  artifactMode.value = next
  localStorage.setItem(STORAGE_KEY, next)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readAnchor(value: unknown): AnchorView | null {
  if (!isRecord(value)) return null
  if (typeof value.block_id !== 'string') return null
  if (typeof value.char_start !== 'number' || typeof value.char_end !== 'number') return null
  return { block_id: value.block_id, char_start: value.char_start, char_end: value.char_end }
}

export function readBeat(value: unknown): BeatView | null {
  if (!isRecord(value)) return null
  if (typeof value.id !== 'string' || typeof value.title !== 'string') return null
  if (typeof value.word_budget !== 'number' || !Number.isFinite(value.word_budget)) return null
  if (!Array.isArray(value.block_ids) || value.block_ids.some((id) => typeof id !== 'string')) {
    return null
  }
  if (value.goal_id != null && typeof value.goal_id !== 'string') return null
  if (value.summary != null && typeof value.summary !== 'string') return null
  return {
    id: value.id,
    title: value.title,
    block_ids: value.block_ids as string[],
    word_budget: value.word_budget,
    goal_id: typeof value.goal_id === 'string' ? value.goal_id : null,
    summary: typeof value.summary === 'string' ? value.summary : null,
  }
}

/** `null` when the payload is not an outline. An empty `beats` list is valid. */
export function readOutline(value: unknown): BeatView[] | null {
  if (!isRecord(value) || !Array.isArray(value.beats)) return null
  const beats: BeatView[] = []
  for (const item of value.beats) {
    const beat = readBeat(item)
    if (!beat) return null
    beats.push(beat)
  }
  return beats
}

export function readScript(value: unknown): SegmentView[] | null {
  if (!isRecord(value) || !Array.isArray(value.segments)) return null
  const segments: SegmentView[] = []
  for (const item of value.segments) {
    if (!isRecord(item)) return null
    if (typeof item.id !== 'string' || typeof item.speaker !== 'string') return null
    if (typeof item.text !== 'string' || typeof item.beat_id !== 'string') return null
    if (item.kind !== 'claim' && item.kind !== 'pedagogy') return null
    if (item.anchors != null && !Array.isArray(item.anchors)) return null
    const anchors: AnchorView[] = []
    for (const anchor of (item.anchors as unknown[] | undefined) ?? []) {
      const read = readAnchor(anchor)
      if (!read) return null
      anchors.push(read)
    }
    segments.push({
      id: item.id,
      speaker: item.speaker,
      text: item.text,
      kind: item.kind,
      anchors,
      beat_id: item.beat_id,
    })
  }
  return segments
}

/** `null` when the payload is not a series plan: episodes with a title and passage ids. */
export function readSeriesPlan(value: unknown): SeriesPlanView | null {
  if (!isRecord(value) || !Array.isArray(value.episodes)) return null
  const episodes: SeriesEpisodeView[] = []
  for (const item of value.episodes) {
    if (!isRecord(item) || typeof item.title !== 'string' || !item.title) return null
    if (!Array.isArray(item.block_ids) || item.block_ids.some((id) => typeof id !== 'string')) {
      return null
    }
    const goals: SeriesGoalView[] = []
    if (item.goals != null && !Array.isArray(item.goals)) return null
    for (const goal of (item.goals as unknown[] | undefined) ?? []) {
      if (!isRecord(goal) || typeof goal.text !== 'string' || !goal.text) return null
      goals.push({
        text: goal.text,
        bloom: typeof goal.bloom_level === 'string' ? goal.bloom_level : null,
      })
    }
    episodes.push({
      title: item.title,
      role: typeof item.role === 'string' ? item.role : '',
      goals,
      block_ids: item.block_ids as string[],
    })
  }
  return {
    title: typeof value.title === 'string' ? value.title : '',
    throughLine: typeof value.through_line === 'string' ? value.through_line : '',
    episodes,
  }
}

/** `null` when the payload is not a selection: learning goals and the chosen block ids. */
export function readSelection(value: unknown): SelectionView | null {
  if (!isRecord(value)) return null
  if (!Array.isArray(value.learning_goals) || !Array.isArray(value.selected_blocks)) return null
  const goals: GoalView[] = []
  for (const item of value.learning_goals) {
    if (!isRecord(item) || typeof item.id !== 'string' || typeof item.text !== 'string') {
      return null
    }
    goals.push({ id: item.id, text: item.text, block_ids: [] })
  }
  const byId = new Map(goals.map((goal) => [goal.id, goal] as const))
  const unassigned: string[] = []
  for (const item of value.selected_blocks) {
    if (!isRecord(item) || typeof item.block_id !== 'string') return null
    if (item.goal_ids != null && !Array.isArray(item.goal_ids)) return null
    const served = ((item.goal_ids as unknown[] | undefined) ?? []).filter(
      (id): id is string => typeof id === 'string' && byId.has(id),
    )
    for (const id of served) byId.get(id)?.block_ids.push(item.block_id)
    if (!served.length) unassigned.push(item.block_id)
  }
  const rationale = typeof value.rationale === 'string' ? value.rationale : ''
  return { goals, unassigned, rationale }
}

/** `null` when the payload is not an audio script: lines with their tagged text and guard. */
export function readAudioScript(value: unknown): AudioLineView[] | null {
  if (!isRecord(value) || !Array.isArray(value.lines)) return null
  const lines: AudioLineView[] = []
  for (const item of value.lines) {
    if (!isRecord(item)) return null
    if (typeof item.segment_id !== 'string' || typeof item.speaker !== 'string') return null
    if (typeof item.text !== 'string' || typeof item.tagged !== 'string') return null
    if (item.guard !== 'pass' && item.guard !== 'fallback') return null
    const forms: SpokenFormView[] = []
    for (const form of Array.isArray(item.spoken_forms) ? item.spoken_forms : []) {
      if (!isRecord(form) || typeof form.original !== 'string' || typeof form.spoken !== 'string') {
        return null
      }
      forms.push({ original: form.original, spoken: form.spoken })
    }
    const tags = Array.isArray(item.tags) ? item.tags : []
    lines.push({
      segment_id: item.segment_id,
      speaker: item.speaker,
      kind: typeof item.kind === 'string' ? item.kind : null,
      text: item.text,
      tagged: item.tagged,
      spoken_forms: forms,
      tags: tags.filter((tag): tag is string => typeof tag === 'string'),
      guard: item.guard,
      problem: typeof item.problem === 'string' ? item.problem : null,
    })
  }
  return lines
}

/** Split a tagged line into tags and the words between them, in order. */
export function tagParts(tagged: string): TagPart[] {
  const parts: TagPart[] = []
  let last = 0
  for (const match of tagged.matchAll(/\[[^[\]\n]{1,60}\]/g)) {
    const at = match.index ?? 0
    if (at > last) parts.push({ text: tagged.slice(last, at), tag: false })
    parts.push({ text: match[0], tag: true })
    last = at + match[0].length
  }
  if (last < tagged.length) parts.push({ text: tagged.slice(last), tag: false })
  return parts
}

export function parsePreview(preview: string | null | undefined): unknown {
  if (preview == null || preview === '') return undefined
  try {
    return JSON.parse(preview) as unknown
  } catch {
    return undefined
  }
}
