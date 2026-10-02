/**
 * Calls for the Experimentieren section, shared by every experiment page.
 *
 * A run is queued on the server and executed on the worker pool; `waitForRun`
 * polls it until it finishes, so the answer is stored even if the page closes.
 */
import { api } from '@/api/client'
import type {
  ExperimentDetail,
  ExperimentOutput,
  ExperimentRun,
  ExperimentSource,
  ExperimentSourceMeta,
  ExperimentSummary,
  OutputPage,
  OutputStatus,
} from '@/api/types'

const BASE = '/api/experiments'
const POLL_MS = 1500

export function listExperiments(): Promise<ExperimentSummary[]> {
  return api.get<ExperimentSummary[]>(BASE)
}

export function getExperiment<Setup>(key: string): Promise<ExperimentDetail<Setup>> {
  return api.get<ExperimentDetail<Setup>>(`${BASE}/${key}`)
}

export function loadSource(
  key: string,
  runId: string,
  beatId?: string | null,
): Promise<ExperimentSource> {
  return api.post<ExperimentSource>(`${BASE}/${key}/source`, {
    run_id: runId,
    beat_id: beatId ?? null,
  })
}

export function startRun<Setup>(
  key: string,
  setup: Setup,
  source: ExperimentSourceMeta | null,
): Promise<ExperimentRun<Setup>> {
  return api.post<ExperimentRun<Setup>>(`${BASE}/${key}/runs`, {
    setup,
    source: source ? { run_id: source.run_id, beat_id: source.beat_id ?? null } : null,
    source_meta: source,
  })
}

export function getRun<Setup>(id: string): Promise<ExperimentRun<Setup>> {
  return api.get<ExperimentRun<Setup>>(`${BASE}/runs/${id}`)
}

export function listRuns<Setup>(key: string, limit = 20): Promise<ExperimentRun<Setup>[]> {
  return api.get<ExperimentRun<Setup>[]>(`${BASE}/${key}/runs?limit=${limit}`)
}

export function isFinished(run: { status: string }): boolean {
  return run.status === 'completed' || run.status === 'failed'
}

/** Poll a run until it completes or fails. `stop` ends the wait early. */
export async function waitForRun<Setup>(
  id: string,
  stop: () => boolean = () => false,
): Promise<ExperimentRun<Setup> | null> {
  for (;;) {
    const run = await getRun<Setup>(id)
    if (isFinished(run)) return run
    if (stop()) return null
    await new Promise((resolve) => setTimeout(resolve, POLL_MS))
  }
}

/** Collect one item; `folderId` is the page's "Sammeln in" folder. */
export function collect<Setup>(
  runId: string,
  item = 'main',
  label: string | null = null,
  folderId: string | null = null,
): Promise<ExperimentOutput<Setup>> {
  return api.post<ExperimentOutput<Setup>>(`${BASE}/runs/${runId}/save`, {
    item,
    label,
    folder_id: folderId,
  })
}

export interface OutputQuery {
  /** Absent: every output; `root`: those in no folder; else a folder id. */
  folderId?: string | null
  includeSub?: boolean
  experiment?: string
  q?: string
  /** `none` means outputs without a status. */
  status?: OutputStatus | 'none'
  /** Only these outputs (the compare view). */
  ids?: string[]
  sort?: 'new' | 'old'
  offset?: number
  limit?: number
}

/** The Sammlung: collected outputs of every experiment. */
export function listAllOutputs(query: OutputQuery = {}): Promise<OutputPage> {
  const params = new URLSearchParams()
  if (query.folderId) params.set('folder_id', query.folderId)
  if (query.includeSub === false) params.set('include_sub', 'false')
  if (query.experiment) params.set('experiment', query.experiment)
  if (query.q) params.set('q', query.q)
  if (query.status) params.set('status', query.status)
  if (query.ids) params.set('ids', query.ids.join(','))
  if (query.sort) params.set('sort', query.sort)
  if (query.offset) params.set('offset', String(query.offset))
  if (query.limit) params.set('limit', String(query.limit))
  const suffix = params.toString()
  return api.get<OutputPage>(`${BASE}/outputs${suffix ? `?${suffix}` : ''}`)
}

/** File outputs into a folder; `null` moves them to "Ohne Ordner". */
export function moveOutputs(ids: string[], folderId: string | null): Promise<void> {
  return api.post<void>(`${BASE}/outputs/move`, { ids, folder_id: folderId })
}

export function listOutputs<Setup>(key: string): Promise<ExperimentOutput<Setup>[]> {
  return api.get<ExperimentOutput<Setup>[]>(`${BASE}/${key}/outputs`)
}

export function deleteOutput(id: string): Promise<void> {
  return api.delete<void>(`${BASE}/outputs/${id}`)
}

/** Rename an output or record a decision; only the fields given change. */
export function updateOutput(
  id: string,
  patch: { label?: string | null; status?: OutputStatus | null; note?: string | null },
): Promise<ExperimentOutput<unknown>> {
  return api.patch<ExperimentOutput<unknown>>(`${BASE}/outputs/${id}`, patch)
}
