import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RunOut, RunStatus } from '@/api/types'

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
  graph: vi.fn(async () => null),
  watch: vi.fn(),
  stopWatching: vi.fn(),
  stop: vi.fn(async () => ({
    id: 'r1',
    status: 'stopping',
    outcome: 'stopping',
    stopped_by: 'reviewer@kalliope.test',
  })),
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
    expect(wrapper.get('[data-stopped]').text()).toContain('Gestoppt')
    expect(wrapper.find('pre.trace').exists()).toBe(false)
    expect(wrapper.get('[data-again]').text()).toBe('Erneut starten')
    expect(wrapper.get('[data-again]').attributes('href')).toBe('/documents/d1/runs/new')
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
    expect(wrapper.find('[data-stopped]').exists()).toBe(true)
    expect(wrapper.find('pre.trace').exists()).toBe(false)
    expect(wrapper.get('[data-again]').attributes('href')).toBe('/documents/d1/runs/new')
  })
})
