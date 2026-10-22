import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useRunsStore } from './runs'

const api = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@/api/client', async (original) => ({
  ...(await original<typeof import('@/api/client')>()),
  api,
}))

beforeEach(() => {
  setActivePinia(createPinia())
  api.get.mockReset()
  api.get.mockResolvedValue([])
})

describe('runs store', () => {
  it('loads the default run page, a document, or an explicit limit', async () => {
    const store = useRunsStore()
    await store.load()
    await store.load('doc 1')
    await store.load(undefined, 500)
    expect(api.get).toHaveBeenNthCalledWith(1, '/api/runs')
    expect(api.get).toHaveBeenNthCalledWith(2, '/api/runs?document_id=doc%201')
    expect(api.get).toHaveBeenNthCalledWith(3, '/api/runs?limit=500')
  })
})
