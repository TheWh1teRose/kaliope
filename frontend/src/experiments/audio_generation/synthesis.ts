/**
 * The experiment's setup and what it would speak: which segments of the
 * prepared script are chosen, with which edits, and what that costs and lasts.
 * Pure functions, so the page and its tests agree on every number.
 */
import type { VoiceCast } from '@/api/types'

/** One segment of the prepared script, as the source loader hands it over. */
export interface Line {
  segment_id: string
  speaker: string
  tagged: string
}

export interface Setup {
  artifact_hash: string
  voice_cast: VoiceCast
  /** Segments to speak; null speaks the whole prepared script. */
  segment_ids: string[] | null
  /** Replacement tagged text by segment id; the speaker stays the source's. */
  edits: Record<string, string>
}

/** About one minute of speech, the size of the run view's sample take. */
export const MINUTE_CHARACTERS = 1000
/** Speaking pace when the backend does not say: 14,000 characters make 15 minutes. */
export const DEFAULT_CHARACTERS_PER_SECOND = 15.5

const TAG = /\[[^[\]\n]{1,60}\]/g

export function emptySetup(): Setup {
  return {
    artifact_hash: '',
    voice_cast: { voices: [], model_id: 'eleven_v4', stability: 0.5, seed: null, language_code: null },
    segment_ids: null,
    edits: {},
  }
}

/** A saved setup from before segments could be chosen or edited. */
export function normalise(setup: Partial<Setup>): Setup {
  const fresh = emptySetup()
  return {
    ...fresh,
    ...setup,
    voice_cast: { ...fresh.voice_cast, ...setup.voice_cast },
    segment_ids: setup.segment_ids ?? null,
    edits: { ...(setup.edits ?? {}) },
  }
}

export function textOf(line: Line, setup: Setup): string {
  return setup.edits[line.segment_id] ?? line.tagged
}

export function isEdited(line: Line, setup: Setup): boolean {
  const edit = setup.edits[line.segment_id]
  return edit !== undefined && edit !== line.tagged
}

export function isChosen(line: Line, setup: Setup): boolean {
  return setup.segment_ids === null || setup.segment_ids.includes(line.segment_id)
}

export function chosenLines(lines: Line[], setup: Setup): Line[] {
  return lines.filter((line) => isChosen(line, setup))
}

/** Store a selection in script order; every segment chosen is stored as null. */
export function choose(lines: Line[], ids: Iterable<string>): string[] | null {
  const wanted = new Set(ids)
  const chosen = lines.filter((line) => wanted.has(line.segment_id)).map((line) => line.segment_id)
  return chosen.length === lines.length && lines.length > 0 ? null : chosen
}

/** Segments ``from`` to ``to`` (1-based, either order, clamped). */
export function rangeIds(lines: Line[], from: number, to: number): string[] {
  const low = Math.max(1, Math.min(from, to))
  const high = Math.min(lines.length, Math.max(from, to))
  return lines.slice(low - 1, high).map((line) => line.segment_id)
}

/** Whole segments from the start until about a minute of speech, as a sample take does. */
export function firstMinuteIds(lines: Line[], setup: Setup): string[] {
  const ids: string[] = []
  let total = 0
  for (const line of lines) {
    if (ids.length && total >= MINUTE_CHARACTERS) break
    ids.push(line.segment_id)
    total += textOf(line, setup).length
  }
  return ids
}

export function spokenText(text: string): string {
  return text.replace(TAG, ' ').replace(/\s{2,}/g, ' ').trim()
}

export interface Estimate {
  segments: number
  /** What ElevenLabs bills: the tagged text. */
  characters: number
  costUsd: number
  /** Tags are not heard, so the length counts only the spoken words. */
  seconds: number
}

export function estimate(
  lines: Line[],
  setup: Setup,
  usdPer1k: number,
  charactersPerSecond = DEFAULT_CHARACTERS_PER_SECOND,
): Estimate {
  const chosen = chosenLines(lines, setup)
  const texts = chosen.map((line) => textOf(line, setup))
  const characters = texts.reduce((sum, text) => sum + text.length, 0)
  const spoken = texts.reduce((sum, text) => sum + spokenText(text).length, 0)
  return {
    segments: chosen.length,
    characters,
    costUsd: (characters / 1000) * usdPer1k,
    seconds: spoken / charactersPerSecond,
  }
}

/** Speakers of the chosen segments that have no voice yet. */
export function missingVoices(lines: Line[], setup: Setup): string[] {
  const voiced = new Set(
    setup.voice_cast.voices.filter((v) => v.voice_id.trim()).map((v) => v.speaker.trim().toLowerCase()),
  )
  const speakers = new Set(chosenLines(lines, setup).map((line) => line.speaker))
  return [...speakers].filter((speaker) => !voiced.has(speaker.trim().toLowerCase()))
}

/** The setup as it is sent: edits equal to the source are dropped. */
export function payload(lines: Line[], setup: Setup): Setup {
  const edits = Object.fromEntries(
    lines.filter((line) => isEdited(line, setup)).map((line) => [line.segment_id, textOf(line, setup)]),
  )
  return { ...setup, segment_ids: choose(lines, chosenLines(lines, setup).map((l) => l.segment_id)), edits }
}

/** Text split into spoken words and tags, so tags can be shown apart. */
export function pieces(text: string): { text: string; tag: boolean }[] {
  const parts: { text: string; tag: boolean }[] = []
  let last = 0
  for (const match of text.matchAll(TAG)) {
    if (match.index > last) parts.push({ text: text.slice(last, match.index), tag: false })
    parts.push({ text: match[0], tag: true })
    last = match.index + match[0].length
  }
  if (last < text.length) parts.push({ text: text.slice(last), tag: false })
  return parts
}

export function clock(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

export function money(value: number): string {
  return `$${value.toFixed(value < 1 ? 3 : 2)}`
}
