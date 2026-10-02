/**
 * How a collected VS draft reads, on the VS page and in the Sammlung: title,
 * badges and the draft's own segments. A draft of a run that is not revealed
 * yet stays blind in both places.
 */
import type { ExperimentOutput } from '@/api/types'
import type { Badge } from '@/experiments/outputMeta'
import {
  asOutput,
  blindLetter,
  collectedDraft,
  draftBadges,
  probabilityText,
} from '@/experiments/verbalized_sampling/vs'
import { t } from '@/i18n'

const vsLabels = t.experiments.vs

export function collectedBlind(output: ExperimentOutput<unknown>, revealed: Set<string>): boolean {
  return !output.run_id || !revealed.has(output.run_id)
}

export function collectedTitle(output: ExperimentOutput<unknown>, revealed: Set<string>): string {
  const own = output.label ? ` · ${output.label}` : ''
  if (collectedBlind(output, revealed)) {
    const letter = blindLetter(output.run_id ?? '', asOutput(output.output), output.item)
    return `${vsLabels.draft} ${letter} (${vsLabels.blindCollected})${own}`
  }
  const draft = collectedDraft(output)
  const name =
    draft?.source === 'vs'
      ? `${vsLabels.withVs} #${draft.index + 1}`
      : draft
        ? vsLabels.withoutVs
        : output.item
  return `${name}${own}`
}

export function collectedBadges(output: ExperimentOutput<unknown>, revealed: Set<string>): Badge[] {
  const draft = collectedDraft(output)
  const out: Badge[] = []
  if (draft && !collectedBlind(output, revealed)) {
    out.push(
      draft.source === 'vs' ? { text: vsLabels.withVs, mark: true } : { text: vsLabels.withoutVs },
    )
    if (draft.source === 'vs') out.push({ text: `p ${probabilityText(draft.probability)}` })
  }
  const model = output.meta.model
  if (typeof model === 'string') out.push({ text: model })
  if (draft) out.push(...draftBadges(draft, asOutput(output.output)?.word_budget ?? null))
  return out
}

export function collectedPayload(output: ExperimentOutput<unknown>): unknown {
  const draft = collectedDraft(output)
  return draft ? { segments: draft.segments } : null
}
