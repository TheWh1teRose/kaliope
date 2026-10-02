/**
 * The settings table of the compare view: what was sent for each output, and
 * what came back. A row whose values differ between the outputs is marked, so
 * the reason two answers differ is visible next to them.
 */
import type { ExperimentOutput } from '@/api/types'
import { sourceLabel } from '@/experiments/outputMeta'
import { probabilityText } from '@/experiments/verbalized_sampling/vs'
import { t } from '@/i18n'

type Output = ExperimentOutput<unknown>

export interface CompareRow {
  key: string
  label: string
  values: string[]
  /** Long text (a prompt): folded in the table. */
  long: boolean
  differs: boolean
}

export interface CompareTable {
  sent: CompareRow[]
  result: CompareRow[]
}

const NONE = '—'
const labels = t.collection
const rowLabels = labels.rows

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return NONE
  if (typeof value === 'number') return value.toLocaleString('de-DE')
  return String(value)
}

function settingsOf(output: Output): Record<string, unknown> {
  return record(record(output.setup).settings)
}

function source(output: Output, revealed: Set<string>): string {
  const side = output.meta.vs_source
  if (side !== 'vs' && side !== 'baseline') return labels.plain
  if (!output.run_id || !revealed.has(output.run_id)) return labels.blind
  if (side === 'baseline') return t.experiments.vs.withoutVs
  const index = typeof output.meta.vs_index === 'number' ? output.meta.vs_index + 1 : '?'
  const probability = typeof output.meta.probability === 'number' ? output.meta.probability : null
  return `${t.experiments.vs.withVs} #${index} · p ${probabilityText(probability)}`
}

function thinking(output: Output): string {
  const settings = settingsOf(output)
  const mode = settings.thinking
  if (mode === undefined || mode === null || mode === 'default') return labels.standard
  const budget = settings.thinking_budget
  return typeof budget === 'number' ? `${text(mode)} · ${budget.toLocaleString('de-DE')}` : text(mode)
}

function fields(output: Output): string {
  const values = record(record(output.setup).fields)
  const keys = Object.keys(values).sort()
  return keys.length ? keys.map((key) => `${key}: ${String(values[key])}`).join('\n\n') : NONE
}

function when(value: string): string {
  return new Date(value).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}

function row(
  key: string,
  label: string,
  outputs: Output[],
  value: (output: Output) => string,
  long = false,
): CompareRow {
  const values = outputs.map(value)
  return { key, label, values, long, differs: new Set(values).size > 1 }
}

/**
 * Rows for `outputs`, in column order. A row that is empty for every output is
 * left out. `revealed` keeps unrevealed VS drafts blind, as everywhere else.
 */
export function compareTable(
  outputs: Output[],
  experimentTitle: (output: Output) => string,
  revealed: Set<string>,
): CompareTable {
  const setup = (output: Output) => record(output.setup)
  const sent = [
    row('experiment', rowLabels.experiment, outputs, experimentTitle),
    row('source', rowLabels.source, outputs, (o) => source(o, revealed)),
    row('model', rowLabels.model, outputs, (o) => text(o.meta.model ?? settingsOf(o).model)),
    row('temperature', rowLabels.temperature, outputs, (o) =>
      settingsOf(o).temperature == null ? labels.standard : text(settingsOf(o).temperature),
    ),
    row('top_p', rowLabels.topP, outputs, (o) => text(settingsOf(o).top_p)),
    row('top_k', rowLabels.topK, outputs, (o) => text(settingsOf(o).top_k)),
    row('thinking', rowLabels.thinking, outputs, thinking),
    row('effort', rowLabels.effort, outputs, (o) => text(settingsOf(o).effort)),
    row('max_tokens', rowLabels.maxTokens, outputs, (o) => text(settingsOf(o).max_tokens)),
    row('structured', rowLabels.structured, outputs, (o) =>
      settingsOf(o).structured === undefined
        ? NONE
        : settingsOf(o).structured
          ? labels.on
          : labels.off,
    ),
    row('vs', rowLabels.vs, outputs, (o) =>
      o.meta.k == null ? NONE : `${text(o.meta.k)} / ${text(o.meta.variant)}`,
    ),
    row('beat', rowLabels.beat, outputs, (o) => sourceLabel(o.meta.source) ?? NONE),
    row(
      'system',
      rowLabels.system,
      outputs,
      (o) => text(setup(o).system_prompt ?? setup(o).base_prompt),
      true,
    ),
    row('instruction', rowLabels.instruction, outputs, (o) => text(setup(o).vs_instruction), true),
    row('template', rowLabels.template, outputs, (o) => text(setup(o).user_template), true),
    row('fields', rowLabels.fields, outputs, fields, true),
  ].filter((r) => r.values.some((value) => value !== NONE))

  const result = [
    row('tokens', rowLabels.tokens, outputs, (o) =>
      o.meta.tokens_in == null
        ? NONE
        : `${Number(o.meta.tokens_in).toLocaleString('de-DE')} / ${Number(
            o.meta.tokens_out ?? 0,
          ).toLocaleString('de-DE')}`,
    ),
    row('cost', rowLabels.cost, outputs, (o) =>
      typeof o.meta.cost_usd === 'number' ? `$${o.meta.cost_usd.toFixed(4)}` : NONE,
    ),
    row('latency', rowLabels.latency, outputs, (o) =>
      typeof o.meta.latency_ms === 'number' && o.meta.latency_ms > 0
        ? `${(o.meta.latency_ms / 1000).toFixed(1).replace('.', ',')} s`
        : NONE,
    ),
    row('folder', rowLabels.folder, outputs, (o) =>
      o.folder_path.length ? o.folder_path.join(' / ') : labels.root,
    ),
    row('collected', rowLabels.collected, outputs, (o) =>
      o.created_by ? `${when(o.created_at)} · ${o.created_by}` : when(o.created_at),
    ),
  ].filter((r) => r.values.some((value) => value !== NONE))

  return { sent, result }
}
