import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'
import type { EditEventIn, ReviewSummary, ScriptOut } from '@/api/types'

export const useReviewStore = defineStore('review', () => {
  const script = ref<ScriptOut | null>(null)
  const sessionId = ref<string | null>(null)
  const tags = ref<string[]>([])
  const busy = ref(false)

  async function open(runId: string): Promise<ScriptOut> {
    busy.value = true
    try {
      const started = await api.post<{ review_session_id: string }>(
        `/api/runs/${runId}/review/start`,
      )
      sessionId.value = started.review_session_id
      script.value = await api.get<ScriptOut>(`/api/runs/${runId}/script`)
      tags.value = await api.get<string[]>(`/api/runs/${runId}/review/tags`)
      return script.value
    } finally {
      busy.value = false
    }
  }

  /**
   * Record an event and take the resulting state from the server.
   *
   * Undo, tags and comments all fold over the whole event stream, so guessing
   * the new state here would be a second implementation of that fold and would
   * drift from the one the export is built on. One extra request is cheaper
   * than two answers that disagree.
   */
  async function record(runId: string, event: EditEventIn): Promise<void> {
    await api.post(`/api/runs/${runId}/review/events`, event)
    await refresh(runId)
  }

  async function refresh(runId: string): Promise<void> {
    script.value = await api.get<ScriptOut>(`/api/runs/${runId}/script`)
    tags.value = await api.get<string[]>(`/api/runs/${runId}/review/tags`)
  }

  function undo(runId: string, segmentId: string): Promise<void> {
    return record(runId, { target_type: 'segment', target_id: segmentId, action: 'undo' })
  }

  function tag(runId: string, segmentId: string, name: string): Promise<void> {
    return record(runId, {
      target_type: 'segment',
      target_id: segmentId,
      action: 'tag',
      text_after: name,
    })
  }

  function untag(runId: string, segmentId: string, name: string): Promise<void> {
    return record(runId, {
      target_type: 'segment',
      target_id: segmentId,
      action: 'untag',
      text_after: name,
    })
  }

  async function complete(
    runId: string,
    segments: Record<string, string>,
    note?: string,
  ): Promise<ReviewSummary> {
    return api.post<ReviewSummary>(`/api/runs/${runId}/review/complete`, { segments, note })
  }

  return { script, sessionId, tags, busy, open, record, refresh, undo, tag, untag, complete }
})
