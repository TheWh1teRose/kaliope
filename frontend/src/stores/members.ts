import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'
import type { Member } from '@/api/types'

/** Workspace members. Every member is an equal admin of the shared workspace. */
export const useMembersStore = defineStore('members', () => {
  const members = ref<Member[]>([])

  async function list(): Promise<Member[]> {
    members.value = await api.get<Member[]>('/api/users')
    return members.value
  }

  async function create(payload: { email: string; name: string; password: string }) {
    const member = await api.post<Member>('/api/users', payload)
    members.value = [...members.value, member]
    return member
  }

  async function setPassword(id: string, password: string): Promise<void> {
    await api.put(`/api/users/${encodeURIComponent(id)}/password`, { password })
  }

  async function remove(id: string): Promise<void> {
    await api.delete(`/api/users/${encodeURIComponent(id)}`)
    members.value = members.value.filter((member) => member.id !== id)
  }

  return { members, list, create, setPassword, remove }
})
