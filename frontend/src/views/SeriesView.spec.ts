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
    request: { episodes: null, minutes_per_episode: 15, hint: null, approved: false },
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
        goals: [
          {
            id: `g${index}`,
            text: `Ziel ${index}`,
            source: 'generated',
            bloom_level: 'understand',
            derivation: 'Aus dem gewünschten Ergebnis',
          },
        ],
        objective_refs: [],
        target_minutes: 15,
        supportable_minutes: 16,
        recap: null,
        preview: null,
      })),
      unassigned: [],
      warnings: [],
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
    stopped_by: status === 'stopped' ? 'reviewer@kalliope.test' : null,
  }
}

const store = {
  get: vi.fn(async () => series('planned')),
  graph: vi.fn(async () => seriesGraph()),
  approve: vi.fn(async () => series('queued')),
  replan: vi.fn(async () => series('queued')),
  resume: vi.fn(async () => series('queued')),
  stop: vi.fn(async () => ({ id: 's1', status: 'stopped', outcome: 'stopped', stopped_by: null })),
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
    for (const fn of [store.get, store.approve, store.replan, store.watch, store.stop]) fn.mockClear()
    store.get.mockImplementation(async () => series('planned'))
  })

  it('holds at the plan and releases it on approval', async () => {
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(wrapper.find('h1').text()).toBe('Klimawandel verstehen')
    expect(wrapper.findAll('[data-node]').length).toBeGreaterThan(10)
    expect(wrapper.text()).toContain('Vom Mechanismus zum Handeln.')
    expect(wrapper.text()).toContain('Ziel 1 (understand)')
    expect(store.watch).not.toHaveBeenCalled()

    await wrapper.get('[data-approve]').trigger('click')
    await flushPromises()
    expect(store.approve).toHaveBeenCalledWith('s1')
  })

  it('uses the series name as the page title', async () => {
    store.get.mockResolvedValueOnce({ ...series('planned'), name: 'Klimaserie' })
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(wrapper.find('h1').text()).toBe('Klimaserie')
  })

  it('plans again with another count', async () => {
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    await wrapper.get('[data-replan-step="1"]').trigger('click')
    expect(wrapper.get('[data-replan-count]').text()).toBe('4')
    await wrapper.get('[data-replan]').trigger('click')
    await flushPromises()
    expect(store.replan).toHaveBeenCalledWith('s1', {
      episodes: 4,
      minutes_per_episode: 15,
      hint: '',
    })
  })

  it('plans again with another length', async () => {
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(wrapper.get('[data-replan-minutes-out]').text()).toBe('15')
    await wrapper.get('[data-replan-minutes]').setValue(20)
    await wrapper.get('[data-replan]').trigger('click')
    await flushPromises()
    expect(store.replan).toHaveBeenCalledWith('s1', {
      episodes: 3,
      minutes_per_episode: 20,
      hint: '',
    })
  })

  it('keeps the stored hint unless the reviewer clears it', async () => {
    const planned = series('planned')
    planned.request.hint = 'Paris extra'
    store.get.mockResolvedValueOnce(planned)
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect((wrapper.get('[data-replan-hint]').element as HTMLInputElement).value).toBe('Paris extra')
    await wrapper.get('[data-replan]').trigger('click')
    await flushPromises()
    expect(store.replan).toHaveBeenCalledWith('s1', {
      episodes: 3,
      minutes_per_episode: 15,
      hint: 'Paris extra',
    })

    await wrapper.get('[data-replan-hint]').setValue('')
    await wrapper.get('[data-replan]').trigger('click')
    await flushPromises()
    expect(store.replan).toHaveBeenLastCalledWith('s1', {
      episodes: 3,
      minutes_per_episode: 15,
      hint: '',
    })
  })

  it('shows a source-budget warning and keeps it on the plan', async () => {
    const planned = series('planned')
    const warning = 'Folge 1 übersteigt das Quellbudget um 30 Wörter, mehr Folgen wählen'
    planned.plan!.warnings = [warning]
    store.get.mockResolvedValueOnce(planned)
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(wrapper.get('[data-budget-warning]').text()).toBe(warning)
  })

  it('shows a warning when an episode has no usable learning goal', async () => {
    const planned = series('planned')
    const warning = 'Folge 1 hat keine verwertbaren Lernziele, bitte neu planen'
    planned.plan!.warnings = [warning]
    planned.plan!.episodes[0].goals = []
    store.get.mockResolvedValueOnce(planned)
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(wrapper.get('[data-budget-warning]').text()).toBe(warning)
    expect(wrapper.get('[data-panel="plan"]').text()).toContain(warning)
  })

  it('warns when the plan has a different episode count than was asked', async () => {
    const planned = series('planned')
    planned.plan!.budget.requested_episodes = 4
    store.get.mockResolvedValueOnce(planned)
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(wrapper.get('[data-count-warning]').text()).toBe('angefragt: 4, geplant: 3')
  })

  it('shows the planner error while the previous plan is still waiting', async () => {
    const planned = series('planned')
    planned.error = 'der Planer ist gescheitert'
    store.get.mockResolvedValueOnce(planned)
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(wrapper.get('[data-panel="plan"]').text()).toContain('Vom Mechanismus zum Handeln.')
    expect(wrapper.get('[data-series-error]').text()).toBe('der Planer ist gescheitert')
  })

  it('subscribes again when a terminal event leaves the series moving', async () => {
    store.get.mockResolvedValueOnce(series('writing'))
    mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    const onTerminal = store.watch.mock.calls[0][1].onTerminal as () => Promise<void>
    store.watch.mockClear()
    store.get.mockResolvedValueOnce(series('outlining'))
    await onTerminal()
    expect(store.watch).toHaveBeenCalledOnce()
  })

  it('does not subscribe again when a terminal event leaves the series idle', async () => {
    store.get.mockResolvedValueOnce(series('writing'))
    mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    const onTerminal = store.watch.mock.calls[0][1].onTerminal as () => Promise<void>
    store.watch.mockClear()
    store.get.mockResolvedValueOnce(series('planned'))
    await onTerminal()
    expect(store.watch).not.toHaveBeenCalled()
  })

  it('follows a moving series and switches to an episode from its lane', async () => {
    store.get.mockResolvedValueOnce(series('writing'))
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(store.watch).toHaveBeenCalled()
    await wrapper.get('[data-lane="2"]').trigger('click')
    expect(wrapper.find('[data-panel="2"]').exists()).toBe(true)
  })

  it('asks in the page before stopping a series that is still writing', async () => {
    store.get.mockResolvedValueOnce(series('writing'))
    const wrapper = mount(SeriesView, {
      props: { id: 's1' },
      attachTo: document.body,
      global: { plugins: [router()] },
    })
    await flushPromises()
    await wrapper.get('[data-stop]').trigger('click')
    expect(store.stop).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('Serie stoppen?')

    const stopped = series('stopped')
    stopped.episodes[0].status = 'completed'
    stopped.episodes[0].run_id = 'r1'
    stopped.episodes[1] = { ...stopped.episodes[1], status: 'stopped', run_id: 'r2', error: null }
    store.get.mockResolvedValue(stopped)
    document.body.querySelector<HTMLButtonElement>('[data-action="confirm-stop"]')?.click()
    await flushPromises()
    expect(store.stop).toHaveBeenCalledWith('s1')
    expect(wrapper.get('[data-series-status]').text()).toContain('Gestoppt')
    expect(wrapper.get('[data-series-status]').classes()).toContain('badge--idle')
    expect(wrapper.get('[data-series-status]').find('.pulse').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows a stopped series as Gestoppt, keeps the canvas, and offers resume', async () => {
    const stopped = series('stopped')
    stopped.error = null
    stopped.episodes[0] = { ...stopped.episodes[0], status: 'completed', run_id: 'r1' }
    stopped.episodes[1] = { ...stopped.episodes[1], status: 'stopped', run_id: 'r2', error: null }
    store.get.mockResolvedValueOnce(stopped)
    const wrapper = mount(SeriesView, { props: { id: 's1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(wrapper.get('[data-series-status]').text()).toContain('Gestoppt')
    expect(wrapper.get('[data-series-status]').classes()).toContain('badge--idle')
    expect(wrapper.get('[data-stopped]').text()).toContain('Gestoppt von reviewer@kalliope.test')
    expect(wrapper.find('[data-series-error]').exists()).toBe(false)
    expect(wrapper.find('[data-stop]').exists()).toBe(false)
    expect(wrapper.find('[data-resume]').exists()).toBe(true)
    expect(wrapper.findAll('[data-node]').length).toBeGreaterThan(0)
    expect(wrapper.text()).toContain('Gestoppt')
  })
})
