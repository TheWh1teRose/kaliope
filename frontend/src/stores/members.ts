import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'
import type { Member } from '@/api/types'

/** Workspace members. Every member is an equal admin of the shared workspace. */
export const useMembersStore = defineStore('members', () => {
  const members = ref<Member[]>([])
  /** Bumped by every change, so a list fetched before one is not applied over it. */
  let changes = 0

  async function list(): Promise<Member[]> {
    const before = changes
    const rows = await api.get<Member[]>('/api/users')
    if (before !== changes) return list()
    members.value = rows
    return members.value
  }

  async function create(payload: { email: string; name: string; password: string }) {
    const member = await api.post<Member>('/api/users', payload)
    changes += 1
    members.value = [...members.value, member]
    return member
  }

  async function setPassword(id: string, password: string): Promise<void> {
    await api.put(`/api/users/${encodeURIComponent(id)}/password`, { password })
  }

  async function remove(id: string): Promise<void> {
    await api.delete(`/api/users/${encodeURIComponent(id)}`)
    changes += 1
    members.value = members.value.filter((member) => member.id !== id)
  }

  return { members, list, create, setPassword, remove }
})
