import { defineStore } from 'pinia'
import { computed, ref, type Ref } from 'vue'

import { api } from '@/api/client'
import type { FolderOut, OutputFolderOut } from '@/api/types'

/** The literal the API uses for "items in no folder". */
export const ROOT = 'root'

/** What every folder tree row has, whatever it counts. */
export interface FolderRow {
  id: string
  name: string
  parent_id: string | null
  depth: number
  path: string[]
}

/**
 * A folder tree store. The server returns the tree already in display order
 * with each folder's depth and rolled-up counts, so nothing here rebuilds the
 * hierarchy — the ordering rule lives in one place. Dokumente and the
 * Sammlung each get one, against their own endpoint.
 */
export function makeFolderStore<Folder extends FolderRow>(id: string, base: string) {
  return defineStore(id, () => {
    const items = ref([]) as Ref<Folder[]>
    const loading = ref(false)

    const byId = computed(
      () => new Map<string, Folder>(items.value.map((folder) => [folder.id, folder])),
    )

    async function load(): Promise<void> {
      loading.value = true
      try {
        items.value = await api.get<Folder[]>(base)
      } finally {
        loading.value = false
      }
    }

    async function create(name: string, parentId: string | null): Promise<Folder> {
      const created = await api.post<Folder>(base, { name, parent_id: parentId })
      await load()
      return created
    }

    async function rename(folderId: string, name: string): Promise<void> {
      await api.patch(`${base}/${folderId}`, { name })
      await load()
    }

    async function move(folderId: string, parentId: string | null): Promise<void> {
      await api.post(`${base}/${folderId}/move`, { parent_id: parentId })
      await load()
    }

    async function remove(folderId: string, cascade: boolean): Promise<void> {
      await api.delete(`${base}/${folderId}${cascade ? '?cascade=true' : ''}`)
      await load()
    }

    /** Descendant ids of a folder, itself excluded. */
    function descendants(folderId: string): string[] {
      const out: string[] = []
      const stack = [folderId]
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

    function pathOf(folderId: string | null): Folder[] {
      const trail: Folder[] = []
      let current = folderId ? byId.value.get(folderId) : undefined
      while (current) {
        trail.unshift(current)
        current = current.parent_id ? byId.value.get(current.parent_id) : undefined
      }
      return trail
    }

    return { items, loading, byId, load, create, rename, move, remove, descendants, pathOf }
  })
}

/** Dokumente's folders. */
export const useFoldersStore = makeFolderStore<FolderOut>('folders', '/api/folders')

/** The Sammlung's folders: collected experiment outputs. */
export const useOutputFoldersStore = makeFolderStore<OutputFolderOut>(
  'output-folders',
  '/api/experiment-folders',
)
