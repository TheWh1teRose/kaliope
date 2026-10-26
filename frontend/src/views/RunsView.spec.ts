import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import type { RunStatus } from '@/api/types'
import { t } from '@/i18n'

import RunsView from './RunsView.vue'

function runRow(id: string, status: RunStatus) {
  return {
    id,
    document_id: 'd1',
    document_title: 'Physiologie',
    name: null,
    flow_id: 'baseline_v0',
    flow_version: '1.0',
    status,
    active: false,
    created_at: '2026-10-03T10:00:00Z',
    total_cost_usd: 0.01,
    target_minutes: 15,
    series_id: null as string | null,
    episode_index: null as number | null,
    trashed_at: null as string | null,
  }
}

function seriesRow(id: string, status: string) {
  return {
    id,
    document_id: 'd1',
    document_title: 'Physiologie',
    name: null,
    plan: { title: `Serie ${id}` },
    flow_id: 'baseline_v0',
    flow_version: '1.0',
    status,
    active: false,
    created_at: '2026-10-03T10:00:00Z',
    total_cost_usd: 0.02,
    episodes: [{ index: 1 }, { index: 2 }],
    request: { episodes: 2 },
    trashed_at: null as string | null,
  }
}

const runs = {
  items: [] as ReturnType<typeof runRow>[],
  load: vi.fn(async () => {}),
  stop: vi.fn(async () => ({ id: 'r-live', status: 'stopping', outcome: 'stopping', stopped_by: null })),
  trashed: vi.fn(async () => [] as ReturnType<typeof runRow>[]),
  trash: vi.fn(async (id: string) => ({ ...runRow(id, 'completed'), trashed_at: '2026-10-04T09:00:00Z' })),
  restore: vi.fn(async (id: string) => runRow(id, 'completed')),
}

const seriesStore = {
  list: vi.fn(async () => [] as ReturnType<typeof seriesRow>[]),
  trashed: vi.fn(async () => [] as ReturnType<typeof seriesRow>[]),
  trash: vi.fn(async (id: string) => ({ ...seriesRow(id, 'stopped'), trashed_at: '2026-10-04T09:00:00Z' })),
  restore: vi.fn(async (id: string) => seriesRow(id, 'stopped')),
  stop: vi.fn(async () => ({ id: 's-live', status: 'stopping', outcome: 'stopping', stopped_by: null })),
}

vi.mock('@/stores/runs', () => ({ useRunsStore: () => runs }))
vi.mock('@/stores/series', () => ({ useSeriesStore: () => seriesStore }))

function router() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/runs', name: 'runs', component: { template: '<div />' } },
      { path: '/runs/:id', name: 'run', component: { template: '<div />' } },
      { path: '/series/:id', name: 'series', component: { template: '<div />' } },
      { path: '/runs/:id/review', name: 'review', component: { template: '<div />' } },
    ],
  })
}

async function mountList(path = '/runs') {
  const routes = router()
  await routes.push(path)
  const wrapper = mount(RunsView, { attachTo: document.body, global: { plugins: [routes] } })
  await flushPromises()
  return wrapper
}

describe('runs list', () => {
  beforeEach(() => {
    runs.items = [runRow('r-live', 'running'), runRow('r-stop', 'stopped'), runRow('r-fail', 'failed')]
    seriesStore.list.mockResolvedValue([seriesRow('s-live', 'writing'), seriesRow('s-stop', 'stopped')])
    runs.stop.mockClear()
    seriesStore.stop.mockClear()
  })

  it('stops a running run only after an in-page confirmation', async () => {
    const wrapper = await mountList()
    const stopButtons = wrapper.findAll('[data-stop]')
    expect(stopButtons).toHaveLength(1)
    await stopButtons[0].trigger('click')
    expect(runs.stop).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('Lauf stoppen?')
    document.body.querySelector<HTMLButtonElement>('[data-action="confirm-stop"]')?.click()
    await flushPromises()
    expect(runs.stop).toHaveBeenCalledWith('r-live')
    wrapper.unmount()
  })

  it('stops a writing series only after an in-page confirmation', async () => {
    const wrapper = await mountList()
    await wrapper.get('[data-stop-series]').trigger('click')
    expect(seriesStore.stop).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('Serie stoppen?')
    document.body.querySelector<HTMLButtonElement>('[data-action="confirm-stop"]')?.click()
    await flushPromises()
    expect(seriesStore.stop).toHaveBeenCalledWith('s-live')
    wrapper.unmount()
  })

  it.each(['run', 'series'] as const)('shows rejected %s stop errors and allows retry', async (kind) => {
    const stop = kind === 'run' ? runs.stop : seriesStore.stop
    stop.mockRejectedValueOnce(new ApiError(401, 'Unauthorized', 'Bitte erneut anmelden.'))
    const wrapper = await mountList()
    await wrapper.get(kind === 'run' ? '[data-stop]' : '[data-stop-series]').trigger('click')
    const confirm = () => document.body.querySelector<HTMLButtonElement>('[data-action="confirm-stop"]')!
    confirm().click()
    await flushPromises()
    expect(document.body.querySelector('[role="alert"]')?.textContent).toContain('Bitte erneut anmelden.')
    expect(confirm().disabled).toBe(false)
    stop.mockRejectedValueOnce(new Error('offline'))
    confirm().click()
    await flushPromises()
    expect(document.body.querySelector('[role="alert"]')?.textContent).toContain(t.errors.generic)
    confirm().click()
    await flushPromises()
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    wrapper.unmount()
  })

  it('offers stop for a queued or running run, not an outlined one', async () => {
    runs.items = [
      runRow('r-queue', 'queued'),
      runRow('r-live', 'running'),
      runRow('r-outline', 'outlined'),
    ]
    seriesStore.list.mockResolvedValue([])
    const wrapper = await mountList()
    expect(wrapper.findAll('[data-stop]')).toHaveLength(2)
    wrapper.unmount()
  })

  it.each(['run', 'series'])('refreshes stopped %s costs until the worker drains', async (kind) => {
    vi.useFakeTimers()
    runs.items = [{ ...runRow('r-live', 'stopped'), active: kind === 'run' }]
    seriesStore.list.mockResolvedValue([{ ...seriesRow('s-live', 'stopped'), active: kind === 'series' }])
    const wrapper = await mountList()
    try {
      const reads = runs.load.mock.calls.length
      await vi.advanceTimersByTimeAsync(3999)
      expect(runs.load).toHaveBeenCalledTimes(reads)
      runs.load.mockImplementationOnce(async () => {
        runs.items = [{ ...runRow('r-live', 'stopped'), total_cost_usd: 0.42 }]
      })
      seriesStore.list.mockResolvedValue([{ ...seriesRow('s-live', 'stopped'), total_cost_usd: 0.42 }])
      await vi.advanceTimersByTimeAsync(1)
      expect(wrapper.text()).toContain('$0.4200')
      expect(runs.load).toHaveBeenCalledTimes(reads + 1)
      await vi.advanceTimersByTimeAsync(12000)
      expect(runs.load).toHaveBeenCalledTimes(reads + 1)
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('drops a stale runs failure after a newer load stops the poll', async () => {
    vi.useFakeTimers()
    runs.items = [runRow('r-live', 'running')]
    seriesStore.list.mockResolvedValue([])
    const wrapper = await mountList()
    const stale: { reject: ((error: unknown) => void) | null } = { reject: null }
    try {
      const reads = runs.load.mock.calls.length
      runs.load.mockImplementationOnce(
        () =>
          new Promise<void>((_resolve, reject) => {
            stale.reject = reject
          }),
      )
      await vi.advanceTimersByTimeAsync(4000)
      expect(runs.load).toHaveBeenCalledTimes(reads + 1)

      runs.load.mockImplementationOnce(async () => {
        runs.items = [runRow('r-live', 'stopped')]
      })
      seriesStore.list.mockResolvedValueOnce([seriesRow('s-new', 'stopped')])
      await wrapper.get('[data-stop]').trigger('click')
      document.body.querySelector<HTMLButtonElement>('[data-action="confirm-stop"]')?.click()
      await vi.advanceTimersByTimeAsync(0)
      expect(wrapper.find('[data-runs-load-error]').exists()).toBe(false)
      expect(wrapper.text()).toContain('Gestoppt')
      expect(wrapper.text()).toContain('Serie s-new')

      stale.reject?.(new Error('offline'))
      stale.reject = null
      await vi.advanceTimersByTimeAsync(0)
      expect(wrapper.find('[data-runs-load-error]').exists()).toBe(false)
      expect(wrapper.text()).toContain('Serie s-new')
      const settled = runs.load.mock.calls.length
      await vi.advanceTimersByTimeAsync(12000)
      expect(runs.load).toHaveBeenCalledTimes(settled)
    } finally {
      stale.reject?.(new Error('offline'))
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('keeps a newer runs failure when an older load succeeds last', async () => {
    vi.useFakeTimers()
    runs.items = [runRow('r-live', 'running')]
    seriesStore.list.mockResolvedValue([])
    const wrapper = await mountList()
    const stale: { resolve: (() => void) | null } = { resolve: null }
    try {
      runs.load.mockImplementationOnce(
        () =>
          new Promise<void>((resolve) => {
            stale.resolve = resolve
          }),
      )
      await vi.advanceTimersByTimeAsync(4000)
      runs.load.mockRejectedValueOnce(new ApiError(503, 'Down', 'Läufe nicht geladen.'))
      await vi.advanceTimersByTimeAsync(4000)
      expect(wrapper.get('[data-runs-load-error]').text()).toContain('Läufe nicht geladen.')

      stale.resolve?.()
      stale.resolve = null
      await vi.advanceTimersByTimeAsync(0)
      expect(wrapper.get('[data-runs-load-error]').text()).toContain('Läufe nicht geladen.')
    } finally {
      stale.resolve?.()
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('keeps the newer series when an older load finishes last', async () => {
    vi.useFakeTimers()
    runs.items = [runRow('r-live', 'stopped')]
    seriesStore.list.mockResolvedValue([{ ...seriesRow('s-live', 'writing') }])
    const wrapper = await mountList()
    const stale: { release: ((rows: ReturnType<typeof seriesRow>[]) => void) | null } = {
      release: null,
    }
    try {
      seriesStore.list.mockResolvedValue([seriesRow('s-new', 'stopped')])
      seriesStore.list.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            stale.release = resolve
          }),
      )
      await vi.advanceTimersByTimeAsync(4000)
      expect(stale.release).not.toBeNull()
      expect(wrapper.text()).toContain('Serie s-live')
      expect(wrapper.text()).not.toContain('Serie s-new')
      await vi.advanceTimersByTimeAsync(4000)
      expect(wrapper.text()).toContain('Serie s-new')
      expect(wrapper.text()).not.toContain('Serie s-live')

      stale.release?.([seriesRow('s-old', 'writing')])
      stale.release = null
      await vi.advanceTimersByTimeAsync(0)
      expect(wrapper.text()).toContain('Serie s-new')
      expect(wrapper.text()).not.toContain('Serie s-old')
      const settled = runs.load.mock.calls.length
      await vi.advanceTimersByTimeAsync(12000)
      expect(runs.load).toHaveBeenCalledTimes(settled)
    } finally {
      stale.release?.([])
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('shows Gestoppt with a neutral badge and no stop button', async () => {
    const wrapper = await mountList()
    const stoppedRun = wrapper.findAll('.badge').find((badge) => badge.text() === 'Gestoppt')
    expect(stoppedRun).toBeTruthy()
    const idle = wrapper.findAll('.badge--idle').filter((badge) => badge.text().includes('Gestoppt'))
    expect(idle.length).toBeGreaterThanOrEqual(2)
    for (const badge of idle) expect(badge.find('.pulse').exists()).toBe(false)
    const failed = wrapper.find('.badge--fail')
    expect(failed.text()).toContain('Fehlgeschlagen')
    expect(wrapper.findAll('[data-stop]')).toHaveLength(1)
    expect(wrapper.findAll('[data-stop-series]')).toHaveLength(1)
    wrapper.unmount()
  })
})

describe('runs trash', () => {
  beforeEach(() => {
    runs.items = [
      runRow('r-done', 'completed'),
      runRow('r-live', 'running'),
      { ...runRow('r-episode', 'completed'), series_id: 's1', episode_index: 1 },
    ]
    seriesStore.list.mockResolvedValue([])
    runs.trashed.mockResolvedValue([])
    seriesStore.trashed.mockResolvedValue([])
    runs.trash.mockClear()
    runs.restore.mockClear()
    seriesStore.trash.mockClear()
    seriesStore.restore.mockClear()
  })

  it('offers delete only for a finished run, and an episode only beside its series', async () => {
    const wrapper = await mountList()
    const buttons = wrapper.findAll('[data-trash]')
    expect(buttons).toHaveLength(1)
    expect(buttons[0].text()).toBe(t.run.delete)
    wrapper.unmount()
  })

  it('moves a run to the trash without asking and offers to undo', async () => {
    const wrapper = await mountList()
    runs.load.mockImplementationOnce(async () => {
      runs.items = runs.items.filter((run) => run.id !== 'r-done')
    })
    runs.trashed.mockResolvedValueOnce([{ ...runRow('r-done', 'completed'), trashed_at: '2026-10-04T09:00:00Z' }])
    await wrapper.get('[data-trash]').trigger('click')
    await flushPromises()
    expect(runs.trash).toHaveBeenCalledWith('r-done')
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    expect(wrapper.get('[data-trashed-notice]').text()).toContain('liegt im Papierkorb')
    expect(wrapper.get('[data-tab="trash"]').text()).toContain('1')

    await wrapper.get('[data-undo]').trigger('click')
    await flushPromises()
    expect(runs.restore).toHaveBeenCalledWith('r-done')
    expect(wrapper.find('[data-trashed-notice]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('lists trashed runs in the trash tab and restores one in a click', async () => {
    runs.trashed.mockResolvedValue([{ ...runRow('r-old', 'failed'), trashed_at: '2026-10-04T09:00:00Z' }])
    const wrapper = await mountList('/runs?tab=trash')
    expect(wrapper.find('[data-trash]').exists()).toBe(false)
    const rows = wrapper.findAll('[data-trash-list] tbody tr')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('Physiologie')
    expect(wrapper.text()).toContain(t.run.trashLead)
    await wrapper.get('[data-restore]').trigger('click')
    await flushPromises()
    expect(runs.restore).toHaveBeenCalledWith('r-old')
    wrapper.unmount()
  })

  it('shows an empty trash', async () => {
    const wrapper = await mountList('/runs?tab=trash')
    expect(wrapper.text()).toContain(t.run.trashEmpty)
    wrapper.unmount()
  })

  it('shows a trash load failure instead of an empty trash', async () => {
    runs.trashed.mockRejectedValueOnce(new Error('offline'))
    const wrapper = await mountList('/runs?tab=trash')
    expect(wrapper.get('[data-trash-load-error]').text()).toContain(t.errors.generic)
    expect(wrapper.text()).not.toContain(t.run.trashEmpty)
    expect(wrapper.find('[data-trash-list]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps a loaded trash list when a later load fails', async () => {
    const row = { ...runRow('r-old', 'failed'), trashed_at: '2026-10-04T09:00:00Z' }
    runs.trashed.mockResolvedValueOnce([row])
    const wrapper = await mountList('/runs?tab=trash')
    expect(wrapper.findAll('[data-trash-list] tbody tr')).toHaveLength(1)
    runs.trashed.mockRejectedValueOnce(new ApiError(503, 'Down', 'Papierkorb nicht geladen.'))
    await wrapper.get('[data-restore]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-trash-load-error]').text()).toContain('Papierkorb nicht geladen.')
    expect(wrapper.findAll('[data-trash-list] tbody tr')).toHaveLength(1)
    expect(wrapper.text()).not.toContain(t.run.trashEmpty)
    wrapper.unmount()
  })

  it('shows a runs load failure instead of an empty list', async () => {
    runs.items = []
    seriesStore.list.mockResolvedValue([seriesRow('s-live', 'writing')])
    runs.load.mockRejectedValueOnce(new Error('offline'))
    const wrapper = await mountList()
    expect(wrapper.get('[data-runs-load-error]').text()).toContain(t.errors.generic)
    expect(wrapper.text()).not.toContain(t.run.empty)
    expect(wrapper.find('[data-loading]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Serie s-live')
    wrapper.unmount()
  })

  it('keeps a loaded runs list when a later load fails', async () => {
    const wrapper = await mountList()
    const rows = wrapper.findAll('[data-trash]').length
    expect(rows).toBeGreaterThan(0)
    runs.load.mockRejectedValueOnce(new ApiError(503, 'Down', 'Läufe nicht geladen.'))
    await wrapper.get('[data-trash]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-runs-load-error]').text()).toContain('Läufe nicht geladen.')
    expect(wrapper.findAll('[data-trash]')).toHaveLength(rows)
    expect(wrapper.text()).not.toContain(t.run.empty)
    wrapper.unmount()
  })

  it('shows a loading indicator instead of the empty list until the runs arrive', async () => {
    runs.items = []
    let answer: () => void = () => {}
    runs.load.mockImplementationOnce(() => new Promise<void>((resolve) => (answer = resolve)))
    const wrapper = await mountList()
    expect(wrapper.find('[data-loading]').exists()).toBe(true)
    expect(wrapper.text()).toContain(t.common.loading)
    expect(wrapper.text()).not.toContain(t.run.empty)
    answer()
    await flushPromises()
    expect(wrapper.find('[data-loading]').exists()).toBe(false)
    expect(wrapper.text()).toContain(t.run.empty)
    wrapper.unmount()
  })

  it('keeps the list in place while a later load refreshes it', async () => {
    const wrapper = await mountList()
    runs.load.mockImplementationOnce(() => new Promise<void>(() => {}))
    await wrapper.get('[data-trash]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-loading]').exists()).toBe(false)
    expect(wrapper.findAll('[data-trash]').length).toBeGreaterThan(0)
    wrapper.unmount()
  })

  it('shows a loading indicator instead of an empty trash until it arrives', async () => {
    let answer: (rows: ReturnType<typeof runRow>[]) => void = () => {}
    runs.trashed.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)))
    const wrapper = await mountList('/runs?tab=trash')
    expect(wrapper.find('[data-loading]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain(t.run.trashEmpty)
    answer([])
    await flushPromises()
    expect(wrapper.find('[data-loading]').exists()).toBe(false)
    expect(wrapper.text()).toContain(t.run.trashEmpty)
    wrapper.unmount()
  })

  it('shows a rejected trash request', async () => {
    runs.trash.mockRejectedValueOnce(new ApiError(409, 'Run is still in progress', 'Erst stoppen.'))
    const wrapper = await mountList()
    await wrapper.get('[data-trash]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Erst stoppen.')
    expect(wrapper.find('[data-trashed-notice]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('offers delete for an idle series and its episodes, not a writing one', async () => {
    runs.items = [
      { ...runRow('r-ep-done', 'completed'), series_id: 's-stop', episode_index: 1 },
      { ...runRow('r-ep-live', 'completed'), series_id: 's-live', episode_index: 1 },
    ]
    seriesStore.list.mockResolvedValue([seriesRow('s-live', 'writing'), seriesRow('s-stop', 'stopped')])
    const wrapper = await mountList()
    const seriesButtons = wrapper.findAll('[data-trash-series]')
    expect(seriesButtons).toHaveLength(1)
    expect(seriesButtons[0].attributes('title')).toBe(t.series.trash)
    const episodeButtons = wrapper.findAll('[data-trash]')
    expect(episodeButtons).toHaveLength(1)
    expect(episodeButtons[0].attributes('title')).toBe(t.series.trash)
    wrapper.unmount()
  })

  it('moves a whole series to the trash from an episode row and undoes it', async () => {
    runs.items = [{ ...runRow('r-ep', 'completed'), series_id: 's-stop', episode_index: 2 }]
    seriesStore.list.mockResolvedValue([seriesRow('s-stop', 'stopped')])
    const wrapper = await mountList()
    seriesStore.trashed.mockResolvedValueOnce([{ ...seriesRow('s-stop', 'stopped'), trashed_at: '2026-10-04T09:00:00Z' }])
    await wrapper.get('[data-trash]').trigger('click')
    await flushPromises()
    expect(seriesStore.trash).toHaveBeenCalledWith('s-stop')
    expect(runs.trash).not.toHaveBeenCalled()
    expect(wrapper.get('[data-trashed-notice]').text()).toContain('Serie s-stop')
    expect(wrapper.get('[data-trashed-notice]').text()).toContain('mit allen Folgen')
    expect(wrapper.get('[data-tab="trash"]').text()).toContain('1')

    await wrapper.get('[data-undo]').trigger('click')
    await flushPromises()
    expect(seriesStore.restore).toHaveBeenCalledWith('s-stop')
    expect(runs.restore).not.toHaveBeenCalled()
    expect(wrapper.find('[data-trashed-notice]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('moves a series to the trash from its own row', async () => {
    seriesStore.list.mockResolvedValue([seriesRow('s-fail', 'failed')])
    const wrapper = await mountList()
    await wrapper.get('[data-trash-series]').trigger('click')
    await flushPromises()
    expect(seriesStore.trash).toHaveBeenCalledWith('s-fail')
    expect(wrapper.get('[data-trashed-notice]').text()).toContain('Serie s-fail')
    wrapper.unmount()
  })

  it('shows a rejected series trash request', async () => {
    seriesStore.list.mockResolvedValue([seriesRow('s-stop', 'stopped')])
    seriesStore.trash.mockRejectedValueOnce(new ApiError(409, 'Series is still in progress', 'Erst die Serie stoppen.'))
    const wrapper = await mountList()
    await wrapper.get('[data-trash-series]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Erst die Serie stoppen.')
    expect(wrapper.find('[data-trashed-notice]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('lists a trashed series as one entry and restores it whole', async () => {
    runs.trashed.mockResolvedValue([{ ...runRow('r-old', 'failed'), trashed_at: '2026-10-04T09:00:00Z' }])
    seriesStore.trashed.mockResolvedValue([{ ...seriesRow('s-old', 'completed'), trashed_at: '2026-10-05T09:00:00Z' }])
    const wrapper = await mountList('/runs?tab=trash')
    expect(wrapper.get('[data-tab="trash"]').text()).toContain('2')
    const rows = wrapper.findAll('[data-trash-series-list] tbody tr')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('Serie s-old')
    expect(wrapper.findAll('[data-trash-list] tbody tr')).toHaveLength(1)
    await wrapper.get('[data-restore-series]').trigger('click')
    await flushPromises()
    expect(seriesStore.restore).toHaveBeenCalledWith('s-old')
    expect(runs.restore).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
