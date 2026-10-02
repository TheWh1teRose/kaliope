/**
 * Pure helpers for prompt-shaped experiment setups: placeholders, field
 * origins and the per-experiment draft kept in the browser.
 */
import type { PromptSetup } from '@/api/types'
import type { FieldOrigin } from '@/components/experiments/FieldCard.vue'

const PLACEHOLDER = /\{\{\s*([a-z0-9_]+)\s*\}\}/g
export const FIELD_NAME = /^[a-z][a-z0-9_]{0,63}$/

export type PlaceholderState = 'ok' | 'empty' | 'missing'

/** Every placeholder in the template, in order of first use, with its state. */
export function placeholders(
  template: string,
  fields: Record<string, string>,
): { name: string; state: PlaceholderState }[] {
  const seen: string[] = []
  for (const match of template.matchAll(PLACEHOLDER)) {
    if (!seen.includes(match[1])) seen.push(match[1])
  }
  return seen.map((name) => ({
    name,
    state: !(name in fields) ? 'missing' : fields[name].trim() ? 'ok' : 'empty',
  }))
}

export function missingPlaceholders(setup: PromptSetup): string[] {
  return placeholders(setup.user_template, setup.fields)
    .filter((p) => p.state === 'missing')
    .map((p) => p.name)
}

export interface PromptDraft {
  setup: PromptSetup
  origins: Record<string, FieldOrigin>
  source: Record<string, unknown> | null
}

function storageKey(key: string): string {
  return `kalliope-exp:${key}`
}

export function readDraft(key: string): PromptDraft | null {
  try {
    const raw = localStorage.getItem(storageKey(key))
    if (!raw) return null
    const draft = JSON.parse(raw) as PromptDraft
    return draft && draft.setup && typeof draft.setup.user_template === 'string' ? draft : null
  } catch {
    return null
  }
}

export function writeDraft(key: string, draft: PromptDraft): void {
  try {
    localStorage.setItem(storageKey(key), JSON.stringify(draft))
  } catch {
    /* storage can be unavailable; the draft is a convenience only */
  }
}

/** Origins for a fresh set of fields: loaded ones keep their source, extra ones are custom. */
export function originsFor(
  fields: Record<string, string>,
  known: string[],
  loaded: FieldOrigin,
): Record<string, FieldOrigin> {
  return Object.fromEntries(
    Object.keys(fields).map((name) => [name, known.includes(name) ? loaded : 'custom']),
  )
}

/**
 * Replace the loaded fields (from a run or the sample) and keep the custom
 * ones, so a hand-added field survives loading another beat.
 */
export function refill(
  fields: Record<string, string>,
  origins: Record<string, FieldOrigin>,
  loaded: Record<string, string>,
  origin: FieldOrigin,
): { fields: Record<string, string>; origins: Record<string, FieldOrigin> } {
  const custom = Object.fromEntries(
    Object.entries(fields).filter(([name]) => origins[name] === 'custom'),
  )
  return {
    fields: { ...loaded, ...custom },
    origins: {
      ...originsFor(loaded, Object.keys(loaded), origin),
      ...Object.fromEntries(Object.keys(custom).map((name) => [name, 'custom' as const])),
    },
  }
}
