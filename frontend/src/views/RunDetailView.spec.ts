import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api } from '@/api/client'
import { sharingApi } from '@/api/sharing'
import type { RunGraphOut, RunOut, RunStatus } from '@/api/types'
import { t } from '@/i18n'
import { seriesGraph } from '@/series/fixtures'

import RunDetailView from './RunDetailView.vue'

function run(status: RunStatus): RunOut {
  return {
    id: 'r1',
    document_id: 'd1',
    document_title: 'Physiologie',
    name: null,
    flow_id: 'baseline_v0',
    flow_version: '1.0',
    status,
    created_at: '2026-10-03T10:00:00Z',
    started_at: '2026-10-03T10:00:01Z',
    finished_at: status === 'stopped' ? '2026-10-03T10:05:00Z' : null,
    error: null,
    total_cost_usd: 0.02,
    target_minutes: 15,
    verdict: null,
    format_spec: null,
    audience_spec: null,
    nodes: [],
    gates: [],
    manifest: null,
    pause: null,
    stopped_by: status === 'stopped' ? 'reviewer@kalliope.test' : null,
  }
}

const store = {
  get: vi.fn(async () => run('running')),
  graph: vi.fn(async (): Promise<RunGraphOut | null> => null),
  watch: vi.fn(),
  stopWatching: vi.fn(),
  stop: vi.fn(async () => ({
    id: 'r1',
    status: 'stopping',
    outcome: 'stopping',
    stopped_by: 'reviewer@kalliope.test',
  })),
  restore: vi.fn(async () => run('completed')),
  progress: [] as { type: string; at: string }[],
  reconnecting: false,
}

vi.mock('@/stores/runs', () => ({ useRunsStore: () => store }))

function router() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/runs', name: 'runs', component: { template: '<div />' } },
      { path: '/documents/:id/runs/new', name: 'new-run', component: { template: '<div />' } },
      { path: '/experiments/bench', name: 'bench', component: { template: '<div />' } },
      { path: '/runs/:id/gates', name: 'run-gates', component: { template: '<div />' } },
      { path: '/runs/:id/review', name: 'review', component: { template: '<div />' } },
      { path: '/review-links/:id/feedback', name: 'shared-feedback', component: { template: '<div />' } },
    ],
  })
}

describe('run detail', () => {
  beforeEach(() => {
    store.get.mockReset()
    store.get.mockImplementation(async () => run('running'))
    store.stop.mockClear()
    store.watch.mockClear()
  })

  it('puts sharing and received feedback under the Feedback menu without replacing run controls or routes', async () => {
    vi.spyOn(sharingApi, 'links').mockResolvedValue({ configured: true, links: [] })
    vi.spyOn(sharingApi, 'options').mockResolvedValue({ title: 'Review', episodes: [] })
    vi.spyOn(sharingApi, 'all').mockResolvedValue({ links: [] })
    const navigation = router()
    const wrapper = mount(RunDetailView, { props: { id: 'r1' }, attachTo: document.body, global: { plugins: [navigation] } })
    await flushPromises()
    expect(wrapper.find('[data-feedback-tab]').exists()).toBe(false)
    expect(wrapper.find('.head').text()).not.toContain('Review-Link')
    await wrapper.findAll('nav button')[1].trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-feedback-tab] form').exists()).toBe(true)
    expect(wrapper.find('[data-run-tab]').isVisible()).toBe(false)
    expect(wrapper.find('[data-stop]').exists()).toBe(true)
    expect(sharingApi.links).toHaveBeenCalledWith('runs', 'r1')
    // The same list as the Feedback page, narrowed to this run.
    expect(sharingApi.all).toHaveBeenCalledWith({ kind: 'runs', id: 'r1' })
    expect(wrapper.find('[data-feedback-tab] [data-empty]').exists()).toBe(true)
    await wrapper.findAll('nav button')[0].trigger('click')
    expect(wrapper.find('[data-run-tab]').isVisible()).toBe(true)
    expect(wrapper.find('[data-feedback-tab]').exists()).toBe(false)
    wrapper.unmount()
    vi.restoreAllMocks()
  })

  it('opens on the Feedback tab when the feedback list links here', async () => {
    vi.spyOn(sharingApi, 'links').mockResolvedValue({ configured: true, links: [] })
    vi.spyOn(sharingApi, 'options').mockResolvedValue({ title: 'Review', episodes: [] })
    vi.spyOn(sharingApi, 'all').mockResolvedValue({ links: [] })
    const navigation = router()
    navigation.addRoute({ path: '/runs/:id', name: 'run', component: { template: '<div />' } })
    await navigation.push({ name: 'run', params: { id: 'r1' }, query: { tab: 'feedback' } })
    const wrapper = mount(RunDetailView, { props: { id: 'r1' }, global: { plugins: [navigation] } })
    await flushPromises()
    expect(wrapper.find('[data-feedback-tab]').exists()).toBe(true)
    wrapper.unmount()
    vi.restoreAllMocks()
  })

  it('asks in the page before stopping a running run', async () => {
    const wrapper = mount(RunDetailView, {
      props: { id: 'r1' },
      attachTo: document.body,
      global: { plugins: [router()] },
    })
    await flushPromises()
    expect(wrapper.get('[data-stop]').text()).toBe('Stoppen')
    await wrapper.get('[data-stop]').trigger('click')
    expect(store.stop).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('Lauf stoppen?')
    expect(document.body.textContent).toContain('Abbrechen')

    store.get.mockImplementation(async () => run('stopped'))
    document.body.querySelector<HTMLButtonElement>('[data-action="confirm-stop"]')?.click()
    await flushPromises()
    expect(store.stop).toHaveBeenCalledWith('r1')
    expect(wrapper.find('[data-stop]').exists()).toBe(false)
    expect(wrapper.get('.badge--idle').text()).toContain('Gestoppt')
    expect(wrapper.find('.pulse').exists()).toBe(false)
    expect(wrapper.get('[data-stopped]').text()).toContain('Gestoppt von reviewer@kalliope.test')
    expect(wrapper.find('pre.trace').exists()).toBe(false)
    expect(wrapper.get('[data-again]').text()).toBe('Erneut starten')
    expect(wrapper.get('[data-again]').attributes('href')).toBe('/documents/d1/runs/new')
    wrapper.unmount()
  })

  it('refreshes a stopped run until its final paid state arrives', async () => {
    vi.useFakeTimers()
    let drained = false
    store.get.mockImplementation(async () => ({
      ...run('stopped'), active: !drained, total_cost_usd: drained ? 0.42 : 0.02,
    }))
    const wrapper = mount(RunDetailView, {
      props: { id: 'r1' },
      global: { plugins: [router()] },
    })
    try {
      await flushPromises()
      expect(store.watch).not.toHaveBeenCalled()
      expect(wrapper.text()).toContain('$0.0200')
      await vi.advanceTimersByTimeAsync(1499)
      expect(store.get).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(1)
      expect(store.get).toHaveBeenCalledTimes(2)
      drained = true
      await vi.advanceTimersByTimeAsync(1500)
      expect(wrapper.text()).toContain('$0.4200')
      expect(store.watch).not.toHaveBeenCalled()
      await vi.advanceTimersByTimeAsync(6000)
      expect(store.get).toHaveBeenCalledTimes(3)
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('shows rejected stop errors without closing the confirmation', async () => {
    store.stop.mockRejectedValueOnce(new ApiError(401, 'Unauthorized', 'Bitte erneut anmelden.'))
    const wrapper = mount(RunDetailView, {
      props: { id: 'r1' },
      attachTo: document.body,
      global: { plugins: [router()] },
    })
    await flushPromises()
    await wrapper.get('[data-stop]').trigger('click')
    const confirm = () => document.body.querySelector<HTMLButtonElement>('[data-action="confirm-stop"]')!
    confirm().click()
    await flushPromises()
    expect(document.body.querySelector('[role="alert"]')?.textContent).toContain('Bitte erneut anmelden.')
    expect(confirm().disabled).toBe(false)
    expect(wrapper.find('[data-stop]').exists()).toBe(true)
    store.stop.mockRejectedValueOnce(new Error('offline'))
    confirm().click()
    await flushPromises()
    expect(document.body.querySelector('[role="alert"]')?.textContent).toContain(t.errors.generic)
    store.get.mockImplementation(async () => run('stopped'))
    confirm().click()
    await flushPromises()
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    expect(wrapper.find('[data-stop]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows a stopped run as Gestoppt and offers to run it again', async () => {
    store.get.mockImplementation(async () => run('stopped'))
    const wrapper = mount(RunDetailView, {
      props: { id: 'r1' },
      global: { plugins: [router()] },
    })
    await flushPromises()
    expect(wrapper.find('[data-stop]').exists()).toBe(false)
    expect(wrapper.get('.badge--idle').text()).toContain('Gestoppt')
    expect(wrapper.find('.pulse').exists()).toBe(false)
    expect(wrapper.get('[data-stopped]').text()).toContain('Gestoppt von reviewer@kalliope.test')
    expect(wrapper.find('pre.trace').exists()).toBe(false)
    expect(wrapper.get('[data-again]').attributes('href')).toBe('/documents/d1/runs/new')
  })

  it('says a trashed run is in the trash and restores it from the page', async () => {
    store.get.mockImplementation(async () => ({ ...run('completed'), trashed_at: '2026-10-04T09:00:00Z' }))
    const wrapper = mount(RunDetailView, {
      props: { id: 'r1' },
      attachTo: document.body,
      global: { plugins: [router()] },
    })
    await flushPromises()
    expect(wrapper.get('[data-trashed]').text()).toContain(t.run.trashedDetail)
    store.get.mockImplementation(async () => run('completed'))
    await wrapper.get('[data-restore]').trigger('click')
    await flushPromises()
    expect(store.restore).toHaveBeenCalledWith('r1')
    expect(wrapper.find('[data-trashed]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('offers restore instead of feedback submit while a paused run is trashed', async () => {
    store.get.mockImplementation(async () => ({ ...run('paused'), trashed_at: '2026-10-04T09:00:00Z' }))
    const wrapper = mount(RunDetailView, {
      props: { id: 'r1' },
      global: { plugins: [router()] },
    })
    await flushPromises()
    expect(wrapper.text()).not.toContain(t.run.openFeedback)
    expect(wrapper.get('[data-trashed]').text()).toContain(t.run.trashedDetail)
    expect(wrapper.get('[data-restore]').text()).toBe(t.run.restore)
    wrapper.unmount()
  })

  it('says a trashed episode is in the trash with its series', async () => {
    store.get.mockImplementation(async () => ({
      ...run('completed'),
      series_id: 's1',
      episode_index: 2,
      trashed_at: '2026-10-04T09:00:00Z',
    }))
    const wrapper = mount(RunDetailView, {
      props: { id: 'r1' },
      global: { plugins: [router()] },
    })
    await flushPromises()
    expect(wrapper.get('[data-trashed]').text()).toContain(t.run.trashedEpisodeDetail)
    expect(wrapper.get('[data-restore]').text()).toBe(t.run.restore)
    wrapper.unmount()
  })

  it('shows why the run could not be loaded instead of a blank page, and retries', async () => {
    store.get.mockRejectedValueOnce(new ApiError(500, 'Internal Server Error', 'Internal Server Error'))
    const wrapper = mount(RunDetailView, { props: { id: 'r1' }, global: { plugins: [router()] } })
    await flushPromises()
    const failure = wrapper.get('[data-load-error]')
    expect(failure.attributes('role')).toBe('alert')
    expect(failure.text()).toContain(t.run.loadError)
    expect(failure.text()).toContain('Internal Server Error')
    expect(wrapper.find('h1').exists()).toBe(false)
    expect(store.watch).not.toHaveBeenCalled()

    await wrapper.get('[data-retry]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-load-error]').exists()).toBe(false)
    expect(wrapper.find('h1').exists()).toBe(true)
    expect(store.watch).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('says it is loading until the run arrives', async () => {
    let arrive: (value: RunOut) => void = () => undefined
    store.get.mockImplementationOnce(() => new Promise<RunOut>((resolve) => (arrive = resolve)))
    const wrapper = mount(RunDetailView, { props: { id: 'r1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(wrapper.get('[data-loading]').text()).toBe(t.common.loading)
    arrive(run('completed'))
    await flushPromises()
    // The audio panel of a finished run shows its own indicator; the page one is gone.
    expect(wrapper.find('.page > [data-loading]').exists()).toBe(false)
    expect(wrapper.find('h1').exists()).toBe(true)
    wrapper.unmount()
  })

  it('offers the data download in the graph header, including while the run is in the trash', async () => {
    store.graph.mockImplementation(async () => seriesGraph().episodes[0].graph)
    store.get.mockImplementation(async () => run('completed'))
    const wrapper = mount(RunDetailView, { props: { id: 'r1' }, global: { plugins: [router()] } })
    await flushPromises()
    const button = wrapper.get('.sheet .spread [data-bundle]')
    expect(button.text()).toBe(t.graph.bundle)
    expect(button.attributes('disabled')).toBeUndefined()
    wrapper.unmount()

    store.get.mockImplementation(async () => ({ ...run('completed'), trashed_at: '2026-10-04T09:00:00Z' }))
    const trashed = mount(RunDetailView, { props: { id: 'r1' }, global: { plugins: [router()] } })
    await flushPromises()
    expect(trashed.get('[data-bundle]').attributes('disabled')).toBeUndefined()
    const download = vi.spyOn(api, 'download').mockRejectedValue(new Error('Unavailable'))
    await trashed.get('[data-bundle]').trigger('click')
    await flushPromises()
    expect(download).toHaveBeenCalledWith('/api/runs/r1/bundle')
    expect(trashed.get('[role="alert"]').text()).toBe(t.errors.generic)
    download.mockRestore()
    expect(trashed.get('[data-bundle]').attributes('title')).toBe(t.graph.bundleHint)
    trashed.unmount()
    store.graph.mockImplementation(async () => null)
  })
})
