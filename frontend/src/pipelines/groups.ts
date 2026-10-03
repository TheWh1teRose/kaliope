import type { PipelineSummary } from '@/api/types'

/** Same node sets the backend uses to tell a flow's kind. */
const AUDIO_NODES = new Set(['audio_script', 'audio_approval', 'audio_render'])

/** Fixed scan order: normal episodes, then series plans, then audio, then anything else. */
export const PIPELINE_GROUP_ORDER = ['episode', 'series_plan', 'audio'] as const

export interface PipelineGroup {
  purpose: string
  items: PipelineSummary[]
}

export function pipelinePurpose(pipeline: PipelineSummary): string {
  if (pipeline.purpose) return pipeline.purpose
  if (pipeline.nodes.includes('series_plan')) return 'series_plan'
  if (pipeline.nodes.some((name) => AUDIO_NODES.has(name))) return 'audio'
  return 'episode'
}

/** Group by flow kind, alphabetical by name inside each group. */
export function groupPipelines(pipelines: PipelineSummary[]): PipelineGroup[] {
  const buckets = new Map<string, PipelineSummary[]>()
  for (const pipeline of pipelines) {
    const purpose = pipelinePurpose(pipeline)
    const items = buckets.get(purpose) ?? []
    items.push(pipeline)
    buckets.set(purpose, items)
  }
  for (const items of buckets.values()) {
    items.sort((a, b) => a.name.localeCompare(b.name, 'de'))
  }
  const known = PIPELINE_GROUP_ORDER.filter((purpose) => buckets.has(purpose))
  const rest = [...buckets.keys()]
    .filter((purpose) => !(PIPELINE_GROUP_ORDER as readonly string[]).includes(purpose))
    .sort((a, b) => a.localeCompare(b, 'de'))
  return [...known, ...rest].map((purpose) => ({
    purpose,
    items: buckets.get(purpose) ?? [],
  }))
}
