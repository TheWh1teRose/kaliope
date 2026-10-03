import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { CreateSeriesPayload } from '@/api/types'

import NewRunView from './NewRunView.vue'

const createSeries = vi.fn(async (_payload: CreateSeriesPayload) => ({ id: 's1' }))
const createRun = vi.fn(async () => ({ id: 'r1' }))

vi.mock('@/stores/catalogue', () => ({
  useCatalogueStore: () => ({
    flows: [
      { id: 'baseline_v0', version: '1.0', description: '', nodes: ['ingest', 'script'], gates: [], purpose: 'episode' },
      { id: 'objectives_v0', version: '1.0', description: '', nodes: ['human_feedback'], gates: [], purpose: 'episode' },
      { id: 'series_plan_v0', version: '1.0', description: '', nodes: ['series_plan'], gates: [], purpose: 'series_plan' },
    ],
    formats: [{ id: 'two_host_dialogue', name: 'Zwei', target_minutes: 15, speakers: [] }],
  }),
}))
vi.mock('@/stores/documents', () => ({
  useDocumentsStore: () => ({ get: vi.fn(async () => ({ id: 'd1', title: 'Klima' })) }),
}))
vi.mock('@/stores/runs', () => ({ useRunsStore: () => ({ create: createRun }) }))
vi.mock('@/stores/series', () => ({
  useSeriesStore: () => ({
    create: createSeries,
    budget: vi.fn(async () => ({
      document_id: 'd1',
      narratable_words: 15600,
      words_per_minute: 135,
      min_compression: 2.5,
      max_supportable_minutes: 46.2,
    })),
  }),
}))

function router() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/documents/:id', name: 'document', component: { template: '<div />' } },
      { path: '/series/:id', name: 'series', component: { template: '<div />' } },
      { path: '/runs/:id', name: 'run', component: { template: '<div />' } },
    ],
  })
}

describe('start screen', () => {
  beforeEach(() => {
    createSeries.mockClear()
    createRun.mockClear()
  })

  it('suggests a count from the budget and lets the reviewer override it', async () => {
    const wrapper = mount(NewRunView, { props: { id: 'd1' }, global: { plugins: [router()] } })
    await flushPromises()
    await wrapper.get('[data-scope="series"]').trigger('click')

    expect(wrapper.get('[data-count-out]').text()).toBe('3')
    expect(wrapper.get('[data-supports]').text()).toContain('3 Folgen à 15 min')
    expect(wrapper.text()).toContain('Dokument')
    expect(wrapper.text()).not.toContain('Heft')
    // A flow that pauses for notes cannot run in a series yet.
    expect(wrapper.findAll('#flow option').map((o) => o.text())).toEqual(['baseline_v0 v1.0'])

    await wrapper.get('[data-step="1"]').trigger('click')
    await wrapper.get('[data-step="1"]').trigger('click')
    expect(wrapper.get('[data-count-out]').text()).toBe('5')
    expect(wrapper.get('[data-supports]').text()).toContain('gekürzt')

    await wrapper.get('[data-step="-1"]').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(createSeries).toHaveBeenCalledWith(
      expect.objectContaining({
        document_id: 'd1',
        flow_id: 'baseline_v0',
        plan_flow_id: 'series_plan_v0',
        episodes: 4,
        minutes_per_episode: 15,
      }),
    )
    expect(createSeries.mock.calls[0][0]).not.toHaveProperty('review_plan')
    expect(createRun).not.toHaveBeenCalled()
  })

  it('sends no count when the suggestion stays', async () => {
    const wrapper = mount(NewRunView, { props: { id: 'd1' }, global: { plugins: [router()] } })
    await flushPromises()
    await wrapper.get('[data-scope="series"]').trigger('click')
    await wrapper.get('[data-step="1"]').trigger('click')
    await wrapper.get('[data-count="auto"]').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(createSeries).toHaveBeenCalledWith(expect.objectContaining({ episodes: null }))
  })

  it('still starts a single run', async () => {
    const wrapper = mount(NewRunView, { props: { id: 'd1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(wrapper.findAll('#flow option').map((o) => o.text())).toEqual([
      'baseline_v0 v1.0',
      'objectives_v0 v1.0',
    ])
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(createRun).toHaveBeenCalledWith(expect.objectContaining({ flow_id: 'baseline_v0', target_minutes: 15 }))
    expect(createSeries).not.toHaveBeenCalled()
  })
})
