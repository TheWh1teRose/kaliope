import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { routes } from './index'

function freshRouter() {
  // The app router adds an auth guard; the route table alone is what moves.
  return createRouter({ history: createMemoryHistory(), routes })
}

describe('experimentation routes', () => {
  it('keeps the workbench under its old route name at its new path', () => {
    expect(freshRouter().resolve({ name: 'bench' }).fullPath).toBe('/experiments/bench')
  })

  it('redirects the old workbench address and keeps its query', async () => {
    const router = freshRouter()
    await router.push('/bench?run=r1&document=d1')
    expect(router.currentRoute.value.name).toBe('bench')
    expect(router.currentRoute.value.query).toEqual({ run: 'r1', document: 'd1' })
  })

  it('opens one experiment by key', () => {
    const route = freshRouter().resolve('/experiments/direct_style')
    expect(route.name).toBe('experiment')
    expect(route.params.key).toBe('direct_style')
  })

  it('serves the experiment collection', () => {
    expect(freshRouter().resolve('/experiments').name).toBe('experiments')
  })
})

describe('quality checks route', () => {
  it('redirects the old catalogue address onto the pipelines tab', async () => {
    const router = freshRouter()
    await router.push('/gates?from=bookmark')
    expect(router.currentRoute.value.name).toBe('pipelines')
    expect(router.currentRoute.value.query).toEqual({ from: 'bookmark', tab: 'gates' })
  })

  it('keeps a deep link to the quality checks tab', () => {
    expect(freshRouter().resolve({ name: 'pipelines', query: { tab: 'gates' } }).fullPath).toBe(
      '/pipelines?tab=gates',
    )
  })
})
