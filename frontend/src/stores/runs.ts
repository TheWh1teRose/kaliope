import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'
import { openStream, type StreamHandle } from '@/stores/stream'
import type {
  AudienceSpec,
  GateDetail,
  GateReport,
  NodeIOOut,
  FeedbackOut,
  Note,
  RunGraphOut,
  RunOut,
  ScriptOut,
  StopOut,
} from '@/api/types'

export interface ProgressLine {
  type: string
  at: string
  node?: string
  message?: string
  error?: string
  cost_usd?: number
}

/** Terminal event types; the stream closes itself after any of them. */
export const TERMINAL = new Set(['run.completed', 'run.failed', 'run.paused', 'run.stopped'])

const RUN_EVENTS = [
  'run.queued',
  'run.started',
  'run.outlined',
  'node.started',
  'node.progress',
  'node.finished',
  'node.cached',
  'node.failed',
  'gates.started',
  'run.completed',
  'run.failed',
  'run.paused',
  'run.stopped',
]

export const useRunsStore = defineStore('runs', () => {
  const items = ref<RunOut[]>([])
  const current = ref<RunOut | null>(null)
  const progress = ref<ProgressLine[]>([])
  const reconnecting = ref(false)
  let stream: StreamHandle | null = null

  async function load(documentId?: string): Promise<void> {
    const query = documentId ? `?document_id=${encodeURIComponent(documentId)}` : ''
    items.value = await api.get<RunOut[]>(`/api/runs${query}`)
  }

  async function get(id: string): Promise<RunOut> {
    current.value = await api.get<RunOut>(`/api/runs/${id}`)
    return current.value
  }

  async function stop(id: string): Promise<StopOut> {
    return api.post<StopOut>(`/api/runs/${id}/stop`)
  }

  async function create(payload: {
    document_id: string
    flow_id: string
    format_id: string
    target_minutes?: number
    audience_spec?: AudienceSpec
    name?: string
  }): Promise<RunOut> {
    return api.post<RunOut>('/api/runs', payload)
  }

  /**
   * Follow a run live. `onEvent` sees every event as it arrives, so the page
   * can update the node it concerns; `onTerminal` runs when the run stops. A
   * dropped connection is reopened, and `onTerminal` also runs then so the page
   * reloads whatever it missed.
   */
  function watch(
    runId: string,
    onTerminal?: () => void,
    onEvent?: (line: ProgressLine) => void,
  ): void {
    stopWatching()
    progress.value = []
    reconnecting.value = false
    stream = openStream({
      url: `/api/runs/${runId}/events`,
      types: RUN_EVENTS,
      terminal: TERMINAL,
      onEvent: (event) => {
        const line = event as unknown as ProgressLine
        progress.value = [...progress.value, line]
        onEvent?.(line)
      },
      onTerminal: () => onTerminal?.(),
      onReconnect: () => onTerminal?.(),
      onState: (state) => {
        reconnecting.value = state === 'reconnecting'
      },
    })
  }

  function stopWatching(): void {
    stream?.close()
    stream = null
    reconnecting.value = false
  }

  async function script(runId: string): Promise<ScriptOut> {
    return api.get<ScriptOut>(`/api/runs/${runId}/script`)
  }

  async function gates(runId: string): Promise<GateReport[]> {
    return api.get<GateReport[]>(`/api/runs/${runId}/gates`)
  }

  /** Each gate's rule and method joined with what it measured in this run. */
  async function gateDetail(runId: string): Promise<GateDetail[]> {
    return api.get<GateDetail[]>(`/api/runs/${runId}/gates/detail`)
  }

  /** The flow as it ran: node status, wiring, cost and gate findings. */
  async function graph(runId: string): Promise<RunGraphOut> {
    return api.get<RunGraphOut>(`/api/runs/${runId}/graph`)
  }

  /** One node's actual inputs and output. */
  async function nodeIo(runId: string, node: string): Promise<NodeIOOut> {
    return api.get<NodeIOOut>(`/api/runs/${runId}/nodes/${encodeURIComponent(node)}/io`)
  }

  async function feedback(runId: string): Promise<FeedbackOut> {
    return api.get<FeedbackOut>(`/api/runs/${runId}/feedback`)
  }

  async function saveFeedback(runId: string, notes: Note[]): Promise<FeedbackOut> {
    return api.put<FeedbackOut>(`/api/runs/${runId}/feedback`, { notes })
  }

  async function submitFeedback(runId: string, notes: Note[]): Promise<FeedbackOut> {
    return api.post<FeedbackOut>(`/api/runs/${runId}/feedback/submit`, { notes })
  }

  return {
    items,
    current,
    progress,
    reconnecting,
    load,
    get,
    stop,
    create,
    watch,
    stopWatching,
    script,
    gates,
    gateDetail,
    graph,
    nodeIo,
    feedback,
    saveFeedback,
    submitFeedback,
  }
})
