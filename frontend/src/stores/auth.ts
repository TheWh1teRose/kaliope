import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'
import type { User } from '@/api/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const checked = ref(false)

  async function refresh(): Promise<User | null> {
    try {
      user.value = await api.get<User>('/api/auth/me')
    } catch {
      user.value = null
    } finally {
      checked.value = true
    }
    return user.value
  }

  async function login(email: string, password: string): Promise<void> {
    user.value = await api.post<User>('/api/auth/login', { email, password })
    checked.value = true
  }

  async function logout(): Promise<void> {
    await api.post('/api/auth/logout')
    user.value = null
  }

  return { user, checked, refresh, login, logout }
})
