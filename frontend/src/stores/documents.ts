import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'
import type { DocumentSummary, StructureOut } from '@/api/types'

/** How often the list re-checks documents that are still being parsed. */
const PARSE_POLL_MS = 2000

export const useDocumentsStore = defineStore('documents', () => {
  const items = ref<DocumentSummary[]>([])
  const loading = ref(false)
  let timer: number | undefined

  async function load(): Promise<void> {
    loading.value = true
    try {
      items.value = await api.get<DocumentSummary[]>('/api/documents')
    } finally {
      loading.value = false
    }
    schedulePoll()
  }

  function schedulePoll(): void {
    window.clearTimeout(timer)
    const pending = items.value.some((d) => d.parse_status === 'pending' || d.parse_status === 'parsing')
    if (!pending) return
    timer = window.setTimeout(() => {
      void load()
    }, PARSE_POLL_MS)
  }

  function stopPolling(): void {
    window.clearTimeout(timer)
  }

  async function get(id: string): Promise<DocumentSummary> {
    return api.get<DocumentSummary>(`/api/documents/${id}`)
  }

  async function upload(file: File, folderId?: string | null): Promise<DocumentSummary> {
    const query = folderId ? `?folder_id=${encodeURIComponent(folderId)}` : ''
    const created = await api.upload<DocumentSummary>(`/api/documents${query}`, file)
    await load()
    return created
  }

  async function move(id: string, folderId: string | null): Promise<void> {
    await api.post(`/api/documents/${id}/move`, { folder_id: folderId })
    const document = items.value.find((d) => d.id === id)
    if (document) document.folder_id = folderId
  }

  async function reparse(id: string): Promise<void> {
    await api.post(`/api/documents/${id}/reparse`)
    await load()
  }

  async function structure(id: string): Promise<StructureOut> {
    return api.get<StructureOut>(`/api/documents/${id}/structure`)
  }

  async function relabel(
    documentId: string,
    blockId: string,
    zone: string,
    note?: string,
  ): Promise<void> {
    await api.patch(`/api/documents/${documentId}/blocks/${blockId}/zone`, {
      zone,
      reason_code: 'ZONE_WRONG',
      note: note ?? null,
    })
  }

  return { items, loading, load, stopPolling, get, upload, move, reparse, structure, relabel }
})
