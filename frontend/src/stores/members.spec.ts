import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Member } from '@/api/types'

import { useMembersStore } from './members'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }))

vi.mock('@/api/client', async (original) => ({
  ...(await original<typeof import('@/api/client')>()),
  api,
}))

function member(id: string): Member {
  return { id, email: `${id}@k.test`, name: id, created_at: '2026-01-01T00:00:00Z', is_self: false }
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.get.mockReset()
  api.post.mockReset()
})

describe('members store', () => {
  it('does not let a list that started before a create drop the new member', async () => {
    let releaseFirst: (rows: Member[]) => void = () => undefined
    api.get
      .mockImplementationOnce(() => new Promise<Member[]>((resolve) => (releaseFirst = resolve)))
      .mockResolvedValueOnce([member('a'), member('new')])
    api.post.mockResolvedValue(member('new'))

    const store = useMembersStore()
    const listing = store.list()
    await store.create({ email: 'new@k.test', name: 'new', password: 'long-enough-pw' })
    releaseFirst([member('a')])
    await listing

    expect(store.members.map((m) => m.id)).toEqual(['a', 'new'])
    expect(api.get).toHaveBeenCalledTimes(2)
  })
})
