import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'
import type {
  BenchCatalogueOut,
  BenchLoadOut,
  BenchRunIn,
  BenchRunOut,
  FeedbackOut,
  FlowValidation,
  Note,
} from '@/api/types'

import type { ProgressLine } from '@/stores/runs'

const TERMINAL = new Set(['run.completed', 'run.failed', 'run.paused'])

export const useBenchStore = defineStore('bench', () => {
  const catalogue = ref<BenchCatalogueOut | null>(null)
  const history = ref<BenchRunOut[]>([])
  const current = ref<BenchRunOut | null>(null)
  const progress = ref<ProgressLine[]>([])
  let source: EventSource | null = null

  async function loadCatalogue(): Promise<BenchCatalogueOut> {
    if (!catalogue.value) catalogue.value = await api.get<BenchCatalogueOut>('/api/bench/catalogue')
    return catalogue.value
  }

  async function validate(nodes: { node: string; config: Record<string, unknown> }[], seedKeys: string[]) {
    return api.post<FlowValidation>('/api/bench/validate', { nodes, seed_keys: seedKeys })
  }

  async function load(payload: {
    document_id?: string | null
    run_id?: string | null
    bench_run_id?: string | null
    format_id?: string | null
    keys?: string[]
    include_payload?: boolean
  }): Promise<BenchLoadOut> {
    return api.post<BenchLoadOut>('/api/bench/load', payload)
  }

  async function artifact(hash: string): Promise<unknown> {
    return api.get<unknown>(`/api/bench/artifacts/${hash}`)
  }

  async function create(payload: BenchRunIn): Promise<BenchRunOut> {
    current.value = await api.post<BenchRunOut>('/api/bench/runs', payload)
    return current.value
  }

  async function get(id: string): Promise<BenchRunOut> {
    current.value = await api.get<BenchRunOut>(`/api/bench/runs/${id}`)
    return current.value
  }

  async function list(): Promise<BenchRunOut[]> {
    history.value = await api.get<BenchRunOut[]>('/api/bench/runs')
    return history.value
  }

  function watch(id: string, onTerminal?: () => void): void {
    stopWatching()
    progress.value = []
    source = new EventSource(`/api/bench/runs/${id}/events`)
    const handle = (event: MessageEvent) => {
      try {
        const line = JSON.parse(event.data) as ProgressLine
        progress.value = [...progress.value, line]
        if (TERMINAL.has(line.type)) {
          stopWatching()
          onTerminal?.()
        }
      } catch {
        /* ignore a malformed frame */
      }
    }
    for (const type of [
      'run.queued',
      'run.started',
      'node.started',
      'node.progress',
      'node.finished',
      'node.cached',
      'node.failed',
      'run.completed',
      'run.failed',
      'run.paused',
    ]) {
      source.addEventListener(type, handle as EventListener)
    }
    source.onerror = () => {
      if (source?.readyState === EventSource.CLOSED) source = null
    }
  }

  function stopWatching(): void {
    source?.close()
    source = null
  }

  async function feedback(id: string): Promise<FeedbackOut> {
    return api.get<FeedbackOut>(`/api/bench/runs/${id}/feedback`)
  }

  async function saveFeedback(id: string, notes: Note[]): Promise<FeedbackOut> {
    return api.put<FeedbackOut>(`/api/bench/runs/${id}/feedback`, { notes })
  }

  async function submitFeedback(id: string, notes: Note[]): Promise<FeedbackOut> {
    return api.post<FeedbackOut>(`/api/bench/runs/${id}/feedback/submit`, { notes })
  }

  return {
    catalogue,
    history,
    current,
    progress,
    loadCatalogue,
    validate,
    load,
    artifact,
    create,
    get,
    list,
    watch,
    stopWatching,
    feedback,
    saveFeedback,
    submitFeedback,
  }
})
