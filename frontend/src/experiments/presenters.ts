/**
 * How a collected output reads outside its own experiment page: in the
 * Sammlung, every experiment's outputs sit in one list, and each must look as
 * it does on its own page. An experiment without an entry uses the shared
 * defaults (the `payload` as Text, the label as title, the run's settings).
 */
import type { ExperimentOutput } from '@/api/types'
import { type Badge, outputBadges } from '@/experiments/outputMeta'
import {
  collectedBadges,
  collectedPayload,
  collectedTitle,
} from '@/experiments/verbalized_sampling/collected'
import { t } from '@/i18n'

type Output = ExperimentOutput<unknown>

export interface OutputPresenter {
  payloadOf: (output: Output) => unknown
  titleOf: (output: Output) => string
  badgesOf: (output: Output) => Badge[]
  /** A rendered artifact for the Text view (outline, selection), when the output has one. */
  artifactOf: (output: Output) => { model: string; payload: unknown } | null
}

const fallback: OutputPresenter = {
  payloadOf: (output) => output.output?.payload ?? null,
  titleOf: (output) => output.label || t.experiments.unnamed,
  badgesOf: (output) => outputBadges(output.meta),
  artifactOf: () => null,
}

/** `revealed` is the VS page's set of revealed runs, so blind drafts stay blind. */
export function presenterFor(key: string, revealed: Set<string>): OutputPresenter {
  if (key === 'verbalized_sampling') {
    return {
      ...fallback,
      payloadOf: collectedPayload,
      titleOf: (output) => collectedTitle(output, revealed),
      badgesOf: (output) => collectedBadges(output, revealed),
    }
  }
  if (key === 'outline') {
    return {
      ...fallback,
      artifactOf: (output) =>
        output.output?.outline ? { model: 'Outline', payload: output.output.outline } : null,
    }
  }
  if (key === 'selection') {
    return {
      ...fallback,
      artifactOf: (output) =>
        output.output?.selection ? { model: 'Selection', payload: output.output.selection } : null,
    }
  }
  return fallback
}
