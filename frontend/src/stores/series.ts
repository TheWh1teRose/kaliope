import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'
import type {
  CreateSeriesPayload,
  DocumentBudget,
  SeriesEvent,
  SeriesGraphOut,
  SeriesOut,
  StopOut,
} from '@/api/types'
import { openStream, type StreamHandle } from '@/stores/stream'

/** Series events after which the server closes the stream. */
export const SERIES_TERMINAL = new Set([
  'series.completed',
  'series.failed',
  'series.planned',
  'series.stopped',
])

const SERIES_EVENTS = [
  'series.planning',
  'series.planned',
  'series.outlining',
  'series.writing',
  'series.completed',
  'series.failed',
  'series.stopped',
  'episode.writing',
  'run.queued',
  'run.started',
  'run.outlined',
  'run.completed',
  'run.failed',
  'run.stopped',
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

  /** `folderId` keeps one run folder's series (`'root'`: those in none). */
  async function list(documentId?: string, folderId?: string): Promise<SeriesOut[]> {
    const params = [
      documentId ? `document_id=${encodeURIComponent(documentId)}` : '',
      folderId ? `folder_id=${encodeURIComponent(folderId)}` : '',
    ].filter((part) => part !== '')
    const query = params.length ? `?${params.join('&')}` : ''
    return api.get<SeriesOut[]>(`/api/series${query}`)
  }

  /** Change only the display name; the episodes are retitled from it. Blank clears it. */
  async function rename(id: string, name: string): Promise<SeriesOut> {
    return api.patch<SeriesOut>(`/api/series/${id}`, { name })
  }

  /** File a series, with its planner and every episode, into a run folder; `null` for none. */
  async function move(id: string, folderId: string | null): Promise<SeriesOut> {
    return api.post<SeriesOut>(`/api/series/${id}/move`, { folder_id: folderId })
  }

  /** The series in the trash, latest trashed first. */
  async function trashed(): Promise<SeriesOut[]> {
    return api.get<SeriesOut[]>('/api/series?trashed=true')
  }

  /** Move a series with all its episodes to the trash. Nothing of it is deleted. */
  async function trash(id: string): Promise<SeriesOut> {
    return api.post<SeriesOut>(`/api/series/${id}/trash`)
  }

  async function restore(id: string): Promise<SeriesOut> {
    return api.post<SeriesOut>(`/api/series/${id}/restore`)
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

  async function stop(id: string): Promise<StopOut> {
    return api.post<StopOut>(`/api/series/${id}/stop`)
  }

  /**
   * Follow a series live. `onEvent` sees every event, tagged with its episode;
   * `onTerminal` runs when the series stops or waits. A dropped connection is
   * reopened with a growing pause, and `onReconnect` lets the page reload what
   * it may have missed. `onIdle` runs when nothing drives the series any more;
   * without it, `onTerminal` does.
   */
  function watch(
    id: string,
    handlers: {
      onEvent?: (event: SeriesEvent) => void
      onTerminal?: () => void
      onReconnect?: () => void
      onIdle?: () => void
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
      onIdle: handlers.onIdle,
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
    rename,
    move,
    trashed,
    trash,
    restore,
    graph,
    approve,
    replan,
    resume,
    stop,
    watch,
    stopWatching,
  }
})
