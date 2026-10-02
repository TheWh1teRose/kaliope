/**
 * Pure helpers for the Verbalized Sampling page: drafts of a run, the blind
 * order, badges, cost facts and the collection tally.
 *
 * Blind is the default. A run stays blind until the user reveals it; the set
 * of revealed runs lives in the browser, so a draft collected blind stays
 * blind in the collection until its run is revealed.
 */
import type { ExperimentOutput, VSSetup } from '@/api/types'
import type { Badge } from '@/experiments/outputMeta'

export type DraftSource = 'vs' | 'baseline'

export interface VSSegment {
  speaker: string
  text: string
  kind: 'claim' | 'pedagogy'
  citations: { block_id: string; quote: string }[]
}

export interface VSDraft {
  item: string
  source: DraftSource
  index: number
  probability: number | null
  segments: VSSegment[]
  words: number
  claims: number
  pedagogy: number
  citations: { cited: number; located: number; unknown_block: number; unlocated: number } | null
  flags: string[]
}

interface Side {
  drafts?: VSDraft[]
  warnings?: string[]
  error?: string | null
  raw?: string
}

export interface VSCost {
  vs_usd: number
  baseline_usd: number
  vs_calls: number
  baseline_calls: number
  tokens_in: number
  tokens_out: number
  latency_ms: number
  vs_per_draft_usd: number | null
  ratio_to_single_baseline: number | null
}

export interface VSOutput {
  variant: string
  k: number
  word_budget: number | null
  citation_check: boolean
  vs: Side
  baseline: Side
  cost: VSCost
}

export function asOutput(value: Record<string, unknown> | null | undefined): VSOutput | null {
  return value && typeof value === 'object' && 'vs' in value ? (value as unknown as VSOutput) : null
}

/** Every draft of a run, VS first, in the server's order. */
export function drafts(output: VSOutput | null): VSDraft[] {
  if (!output) return []
  return [...(output.vs?.drafts ?? []), ...(output.baseline?.drafts ?? [])]
}

/** A small seeded generator, so a run's blind order is the same on every load. */
function seeded(seed: string): () => number {
  let h = 2166136261
  for (let i = 0; i < seed.length; i++) h = Math.imul(h ^ seed.charCodeAt(i), 16777619)
  return () => {
    h = (h + 0x6d2b79f5) | 0
    let x = Math.imul(h ^ (h >>> 15), 1 | h)
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296
  }
}

/** Item ids shuffled with the run id as seed. */
export function blindOrder(runId: string, items: string[]): string[] {
  const out = [...items].sort()
  const next = seeded(runId)
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(next() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}

/** "A", "B", … for the n-th draft in blind order. */
export function letter(position: number): string {
  return String.fromCharCode(65 + position)
}

/** The blind letter of one item of a run. */
export function blindLetter(runId: string, output: VSOutput | null, item: string): string {
  const order = blindOrder(
    runId,
    drafts(output).map((d) => d.item),
  )
  const position = order.indexOf(item)
  return position < 0 ? '?' : letter(position)
}

export function probabilityText(value: number | null): string {
  return value === null ? '–' : value.toFixed(2).replace('.', ',')
}

/** Badges that never reveal the method: length, claims/pedagogy. */
export function draftBadges(draft: VSDraft, wordBudget: number | null): Badge[] {
  return [
    { text: `${draft.words} / ${wordBudget ?? '?'} Wörter` },
    { text: `${draft.claims} Aussagen · ${draft.pedagogy} Didaktik` },
  ]
}

/** "Belege 4/4"; null when the passages carry no ids. */
export function citationBadge(draft: VSDraft): { text: string; ok: boolean } | null {
  const check = draft.citations
  if (!check) return null
  return { text: `Belege ${check.located}/${check.cited}`, ok: check.located === check.cited }
}

function usd(value: number): string {
  return `$${value.toFixed(4)}`
}

/** Cost from the run's real traces, VS against the plain call. */
export function costFacts(cost: VSCost | null | undefined, drafts: number): string[] {
  if (!cost) return []
  const facts = [
    `${cost.tokens_in.toLocaleString('de-DE')} / ${cost.tokens_out.toLocaleString('de-DE')} Tokens`,
  ]
  let vs = `VS ${usd(cost.vs_usd)}`
  if (cost.vs_per_draft_usd !== null) vs += ` (${drafts} Fassungen, ${usd(cost.vs_per_draft_usd)}/Fassung)`
  facts.push(vs)
  if (cost.baseline_calls) facts.push(`ohne VS ${usd(cost.baseline_usd)}`)
  if (cost.ratio_to_single_baseline !== null) {
    facts.push(`Verhältnis ${String(cost.ratio_to_single_baseline).replace('.', ',')}×`)
  }
  if (cost.latency_ms > 0) facts.push(`${(cost.latency_ms / 1000).toFixed(1).replace('.', ',')} s`)
  return facts
}

/** The draft a collected output holds. */
export function collectedDraft(output: ExperimentOutput<unknown>): VSDraft | null {
  return drafts(asOutput(output.output)).find((d) => d.item === output.item) ?? null
}

export interface Tally {
  vs: number
  baseline: number
  blind: number
}

/** Collected drafts by method; drafts of unrevealed runs count as blind. */
export function tally(outputs: ExperimentOutput<unknown>[], revealed: Set<string>): Tally {
  const out: Tally = { vs: 0, baseline: 0, blind: 0 }
  for (const output of outputs) {
    if (!output.run_id || !revealed.has(output.run_id)) out.blind += 1
    else if (output.meta.vs_source === 'vs') out.vs += 1
    else if (output.meta.vs_source === 'baseline') out.baseline += 1
  }
  return out
}

// --------------------------------------------------------------- storage

const KEY = 'kalliope-exp:verbalized_sampling'

export function readRevealed(): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(`${KEY}:revealed`) ?? '[]')
    return new Set(Array.isArray(raw) ? raw.map(String) : [])
  } catch {
    return new Set()
  }
}

export function writeRevealed(revealed: Set<string>): void {
  try {
    localStorage.setItem(`${KEY}:revealed`, JSON.stringify([...revealed]))
  } catch {
    /* storage can be unavailable; runs then open blind again */
  }
}

export interface VSDraftState {
  setup: VSSetup
  origins: Record<string, string>
}

export function readSetupDraft(): VSDraftState | null {
  try {
    const draft = JSON.parse(localStorage.getItem(KEY) ?? 'null') as VSDraftState | null
    return draft && draft.setup && typeof draft.setup.vs_instruction === 'string' ? draft : null
  } catch {
    return null
  }
}

export function writeSetupDraft(draft: VSDraftState): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(draft))
  } catch {
    /* the draft is a convenience only */
  }
}
