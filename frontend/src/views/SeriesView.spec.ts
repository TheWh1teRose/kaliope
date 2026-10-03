import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SeriesOut } from '@/api/types'
import { seriesGraph } from '@/series/fixtures'

import SeriesView from './SeriesView.vue'

function series(status: SeriesOut['status']): SeriesOut {
  return {
    id: 's1',
    document_id: 'd1',
    document_title: 'Klima',
    flow_id: 'baseline_v0',
    flow_version: '1.0',
    plan_flow_id: 'series_plan_v0',
    status,
    error: null,
    created_at: '2026-10-03T10:00:00Z',
    started_at: null,
    finished_at: null,
    request: { episodes: null, minutes_per_episode: 15, hint: null, review_plan: true, approved: false },
    plan: {
      title: 'Klimawandel verstehen',
      through_line: 'Vom Mechanismus zum Handeln.',
      terms: [{ term: 'Kipppunkt', gloss: '', first_episode: 2 }],
      episodes: [1, 2, 3].map((index) => ({
        id: `ep0${index}`,
        index,
        title: ['Eins', 'Zwei', 'Drei'][index - 1],
        role: 'Kern',
        summary: null,
        block_ids: ['b1', 'b2'],
        recap_block_ids: [],
        goals: [{ id: `g${index}`, text: `Ziel ${index}`, source: 'generated' }],
        objective_refs: [],
        target_minutes: 15,
        supportable_minutes: 16,
        recap: null,
        preview: null,
      })),
      unassigned: [],
      budget: {
        max_supportable_minutes: 46.2,
        minutes_per_episode: 15,
        requested_episodes: null,
        verdict: 'ok',
        explanation: 'Das Dokument trägt 46 Minuten.',
      },
    },
    checks: {},
    plan_run_id: 'p1',
    plan_run_status: 'completed',
    plan_cost_usd: 0.14,
    total_cost_usd: 0.14,
    episodes: [1, 2, 3].map((index) => ({
      index,
      title: ['Eins', 'Zwei', 'Drei'][index - 1],
      role: 'Kern',
      target_minutes: 15,
      run_id: null,
      status: 'pending' as const,
      total_cost_usd: 0,
      error: null,
    })),
    progress: { outlined: 0, written: 0, episodes: 0 },
    active: false,
  }
}

const store = {
  get: vi.fn(async () => series('planned')),
  graph: vi.fn(async () => seriesGraph()),
  approve: vi.fn(async () => series('queued')),
  replan: vi.fn(async () => series('queued')),
  resume: vi.fn(async () => series('queued')),
  watch: vi.fn(),
  stopWatching: vi.fn(),
  reconnecting: false,
}

vi.mock('@/stores/series', () => ({ useSeriesStore: () => store }))
vi.mock('@/stores/runs', () => ({ useRunsStore: () => ({ nodeIo: vi.fn(async () => null) }) }))

function router() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/runs', name: 'runs', component: { template: '<div />' } },
      { path: '/runs/:id', name: 'run', component: { template: '<div />' } },
      { path: '/runs/:id/review', name: 'review', component: { template: '<div />' } },
    ],
  })
}

describe('series view', () => {
  beforeEach(() => {
    for (const fn of [store.get, store.approve, store.replan, store.watch]) fn.mockClear()
  })

  it('holds at the plan and releases it on approval', async () => {
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(wrapper.find('h1').text()).toBe('Klimawandel verstehen')
    expect(wrapper.findAll('[data-node]').length).toBeGreaterThan(10)
    expect(wrapper.text()).toContain('Vom Mechanismus zum Handeln.')
    expect(store.watch).not.toHaveBeenCalled()

    await wrapper.get('[data-approve]').trigger('click')
    await flushPromises()
    expect(store.approve).toHaveBeenCalledWith('s1')
  })

  it('plans again with another count', async () => {
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    await wrapper.get('[data-replan-step="1"]').trigger('click')
    expect(wrapper.get('[data-replan-count]').text()).toBe('4')
    await wrapper.get('[data-replan]').trigger('click')
    await flushPromises()
    expect(store.replan).toHaveBeenCalledWith('s1', { episodes: 4, hint: '' })
  })

  it('follows a moving series and switches to an episode from its lane', async () => {
    store.get.mockResolvedValueOnce(series('writing'))
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(store.watch).toHaveBeenCalled()
    await wrapper.get('[data-lane="2"]').trigger('click')
    expect(wrapper.find('[data-panel="2"]').exists()).toBe(true)
  })
})
