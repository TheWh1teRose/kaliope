/**
 * Badges, facts and warnings for an output card, from a run or a collected
 * output's `meta`. Shared so every experiment shows them the same way.
 */
import type { ExperimentCall, ExperimentRun } from '@/api/types'

export interface Badge {
  text: string
  mark?: boolean
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function formatFacts(parts: {
  tokensIn?: number | null
  tokensOut?: number | null
  costUsd?: number | null
  latencyMs?: number | null
  source?: string | null
}): string[] {
  const facts: string[] = []
  if (parts.tokensIn != null && parts.tokensOut != null) {
    facts.push(
      `${parts.tokensIn.toLocaleString('de-DE')} / ${parts.tokensOut.toLocaleString('de-DE')} Tokens`,
    )
  }
  if (parts.costUsd != null) facts.push(`$${parts.costUsd.toFixed(4)}`)
  if (parts.latencyMs != null && parts.latencyMs > 0) {
    facts.push(`${(parts.latencyMs / 1000).toFixed(1).replace('.', ',')} s`)
  }
  if (parts.source) facts.push(parts.source)
  return facts
}

export function sourceLabel(source: unknown): string | null {
  if (!source || typeof source !== 'object') return null
  const s = source as Record<string, unknown>
  const title = typeof s.document_title === 'string' ? s.document_title : null
  const beat = typeof s.beat_title === 'string' ? s.beat_title : null
  return [title, beat].filter(Boolean).join(' · ') || null
}

export function settingBadges(parts: {
  model?: unknown
  temperature?: unknown
  thinking?: unknown
  effort?: unknown
}): Badge[] {
  const badges: Badge[] = []
  if (typeof parts.model === 'string') badges.push({ text: parts.model })
  if (num(parts.temperature) !== null) badges.push({ text: `T ${parts.temperature}` })
  if (typeof parts.thinking === 'string' && parts.thinking !== 'default') {
    badges.push({ text: `Denken ${parts.thinking}` })
  }
  if (typeof parts.effort === 'string') badges.push({ text: `Aufwand ${parts.effort}` })
  return badges
}

export function outputBadges(meta: Record<string, unknown>): Badge[] {
  return settingBadges(meta)
}

export function outputFacts(meta: Record<string, unknown>): string[] {
  return formatFacts({
    tokensIn: num(meta.tokens_in),
    tokensOut: num(meta.tokens_out),
    costUsd: num(meta.cost_usd),
    latencyMs: num(meta.latency_ms),
    source: sourceLabel(meta.source),
  })
}

export function outputWarnings(meta: Record<string, unknown>): string[] {
  return Array.isArray(meta.warnings) ? meta.warnings.map(String) : []
}

export function runBadges(run: ExperimentRun<unknown>): Badge[] {
  const call: ExperimentCall | undefined = run.calls[0]
  return call ? settingBadges(call) : []
}

export function runFacts(run: ExperimentRun<unknown>): string[] {
  return formatFacts({
    tokensIn: run.tokens_in,
    tokensOut: run.tokens_out,
    costUsd: run.total_cost_usd,
    latencyMs: run.calls.reduce((sum, call) => sum + call.latency_ms, 0),
    source: sourceLabel(run.source),
  })
}
