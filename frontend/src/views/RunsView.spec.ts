import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RunStatus } from '@/api/types'

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
    created_at: '2026-10-03T10:00:00Z',
    total_cost_usd: 0.01,
    target_minutes: 15,
    series_id: null,
    episode_index: null,
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
    created_at: '2026-10-03T10:00:00Z',
    total_cost_usd: 0.02,
    episodes: [{ index: 1 }, { index: 2 }],
    request: { episodes: 2 },
  }
}

const runs = {
  items: [] as ReturnType<typeof runRow>[],
  load: vi.fn(async () => {}),
  stop: vi.fn(async () => ({ id: 'r-live', status: 'stopping', outcome: 'stopping', stopped_by: null })),
}

const seriesStore = {
  list: vi.fn(async () => [] as ReturnType<typeof seriesRow>[]),
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

async function mountList() {
  const wrapper = mount(RunsView, { attachTo: document.body, global: { plugins: [router()] } })
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
