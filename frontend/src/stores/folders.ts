import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api } from '@/api/client'
import type { FolderOut } from '@/api/types'

/** The literal the API uses for "documents in no folder". */
export const ROOT = 'root'

/**
 * The folder tree. The server returns it already in display order with each
 * folder's depth and rolled-up counts, so nothing here rebuilds the hierarchy
 * — the ordering rule lives in one place.
 */
export const useFoldersStore = defineStore('folders', () => {
  const items = ref<FolderOut[]>([])
  const loading = ref(false)

  const byId = computed(() => new Map(items.value.map((folder) => [folder.id, folder])))

  async function load(): Promise<void> {
    loading.value = true
    try {
      items.value = await api.get<FolderOut[]>('/api/folders')
    } finally {
      loading.value = false
    }
  }

  async function create(name: string, parentId: string | null): Promise<FolderOut> {
    const created = await api.post<FolderOut>('/api/folders', { name, parent_id: parentId })
    await load()
    return created
  }

  async function rename(id: string, name: string): Promise<void> {
    await api.patch(`/api/folders/${id}`, { name })
    await load()
  }

  async function move(id: string, parentId: string | null): Promise<void> {
    await api.post(`/api/folders/${id}/move`, { parent_id: parentId })
    await load()
  }

  async function remove(id: string, cascade: boolean): Promise<void> {
    await api.delete(`/api/folders/${id}${cascade ? '?cascade=true' : ''}`)
    await load()
  }

  /** Descendant ids of a folder, itself excluded. */
  function descendants(id: string): string[] {
    const out: string[] = []
    const stack = [id]
    while (stack.length) {
      const current = stack.pop() as string
      for (const folder of items.value) {
        if (folder.parent_id === current) {
          out.push(folder.id)
          stack.push(folder.id)
        }
      }
    }
    return out
  }

  function pathOf(id: string | null): FolderOut[] {
    const trail: FolderOut[] = []
    let current = id ? byId.value.get(id) : undefined
    while (current) {
      trail.unshift(current)
      current = current.parent_id ? byId.value.get(current.parent_id) : undefined
    }
    return trail
  }

  return { items, loading, byId, load, create, rename, move, remove, descendants, pathOf }
})
