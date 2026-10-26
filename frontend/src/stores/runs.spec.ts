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
  it('keeps the newer run list when an older load finishes last', async () => {
    const store = useRunsStore()
    let releaseOld: (rows: { id: string; status: string }[]) => void = () => {}
    api.get.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseOld = resolve
        }),
    )
    const older = store.load()
    const stopped = [{ id: 'r-live', status: 'stopped' }]
    api.get.mockResolvedValueOnce(stopped)
    await store.load(undefined, 500)
    expect(store.items).toEqual(stopped)

    releaseOld([{ id: 'r-live', status: 'running' }])
    await older
    expect(store.items).toEqual(stopped)
  })

  it('keeps the previous run list when a newer load fails and an older one finishes last', async () => {
    const store = useRunsStore()
    const current = [{ id: 'r-live', status: 'stopped' }]
    api.get.mockResolvedValueOnce(current)
    await store.load()
    let releaseOld: (rows: { id: string; status: string }[]) => void = () => {}
    api.get.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseOld = resolve
        }),
    )
    const older = store.load()
    api.get.mockRejectedValueOnce(new Error('down'))
    await expect(store.load()).rejects.toThrow('down')
    expect(store.items).toEqual(current)

    releaseOld([{ id: 'r-live', status: 'running' }])
    await older
    expect(store.items).toEqual(current)
  })

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
