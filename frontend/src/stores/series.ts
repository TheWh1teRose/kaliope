import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'
import type {
  CreateSeriesPayload,
  DocumentBudget,
  SeriesEvent,
  SeriesGraphOut,
  SeriesOut,
} from '@/api/types'
import { openStream, type StreamHandle } from '@/stores/stream'

/** Series events after which the server closes the stream. */
export const SERIES_TERMINAL = new Set(['series.completed', 'series.failed', 'series.planned'])

const SERIES_EVENTS = [
  'series.planning',
  'series.planned',
  'series.outlining',
  'series.writing',
  'series.completed',
  'series.failed',
  'episode.writing',
  'run.queued',
  'run.started',
  'run.outlined',
  'run.completed',
  'run.failed',
  'node.started',
  'node.progress',
  'node.finished',
  'node.cached',
  'node.failed',
  'gates.started',
]

export const useSeriesStore = defineStore('series', () => {
  const events = ref<SeriesEvent[]>([])
  const reconnecting = ref(false)
  let stream: StreamHandle | null = null

  async function budget(documentId: string): Promise<DocumentBudget> {
    return api.get<DocumentBudget>(`/api/documents/${documentId}/budget`)
  }

  async function create(payload: CreateSeriesPayload): Promise<SeriesOut> {
    return api.post<SeriesOut>('/api/series', payload)
  }

  async function get(id: string): Promise<SeriesOut> {
    return api.get<SeriesOut>(`/api/series/${id}`)
  }

  async function list(documentId?: string): Promise<SeriesOut[]> {
    const query = documentId ? `?document_id=${encodeURIComponent(documentId)}` : ''
    return api.get<SeriesOut[]>(`/api/series${query}`)
  }

  async function graph(id: string): Promise<SeriesGraphOut> {
    return api.get<SeriesGraphOut>(`/api/series/${id}/graph`)
  }

  async function approve(id: string): Promise<SeriesOut> {
    return api.post<SeriesOut>(`/api/series/${id}/approve`)
  }

  async function replan(
    id: string,
    body: { episodes?: number | null; minutes_per_episode?: number; hint?: string },
  ): Promise<SeriesOut> {
    return api.post<SeriesOut>(`/api/series/${id}/replan`, body)
  }

  async function resume(id: string): Promise<SeriesOut> {
    return api.post<SeriesOut>(`/api/series/${id}/resume`)
  }

  /**
   * Follow a series live. `onEvent` sees every event, tagged with its episode;
   * `onTerminal` runs when the series stops or waits. A dropped connection is
   * reopened with a growing pause, and `onReconnect` lets the page reload what
   * it may have missed.
   */
  function watch(
    id: string,
    handlers: {
      onEvent?: (event: SeriesEvent) => void
      onTerminal?: () => void
      onReconnect?: () => void
    },
  ): void {
    stopWatching()
    events.value = []
    stream = openStream({
      url: `/api/series/${id}/events`,
      types: SERIES_EVENTS,
      terminal: SERIES_TERMINAL,
      onEvent: (event) => {
        const line = event as unknown as SeriesEvent
        events.value = [...events.value.slice(-199), line]
        handlers.onEvent?.(line)
      },
      onTerminal: () => handlers.onTerminal?.(),
      onReconnect: () => handlers.onReconnect?.(),
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

  return {
    events,
    reconnecting,
    budget,
    create,
    get,
    list,
    graph,
    approve,
    replan,
    resume,
    watch,
    stopWatching,
  }
})
