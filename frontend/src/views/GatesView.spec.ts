import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'
import GatesView from './GatesView.vue'
import PipelinesView from './PipelinesView.vue'

vi.mock('@/stores/pipelines', () => ({
  usePipelinesStore: () => ({
    pipelines: [],
    formats: [],
    nodes: null,
    list: vi.fn(async () => []),
    listFormats: vi.fn(async () => []),
    loadNodes: vi.fn(async () => ({ nodes: [] })),
  }),
}))

vi.mock('@/stores/catalogue', () => ({
  useCatalogueStore: () => ({
    gates: [],
    load: vi.fn(async () => undefined),
  }),
}))

vi.mock('@/stores/runs', () => ({
  useRunsStore: () => ({
    get: vi.fn(),
    gateDetail: vi.fn(),
  }),
}))

function router() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/pipelines', name: 'pipelines', component: PipelinesView },
      { path: '/runs/:id', name: 'run', component: { template: '<div />' } },
    ],
  })
}

describe('embedded quality-check catalogue', () => {
  it('keeps the standalone page chrome', async () => {
    const wrapper = mount(GatesView, { global: { plugins: [router()] } })
    await flushPromises()

    expect(wrapper.classes()).toEqual(['page'])
    expect(wrapper.find('h1').text()).toBe(t.gate.catalogueTitle)
  })

  it('sits in the pipelines tab without a nested page box', async () => {
    const app = router()
    await app.push('/pipelines?tab=gates')
    const wrapper = mount(PipelinesView, { global: { plugins: [app] } })
    await flushPromises()

    const catalogue = wrapper.findComponent(GatesView).element as HTMLElement
    expect(catalogue.parentElement?.classList.contains('page')).toBe(true)
    expect(catalogue.classList.contains('page')).toBe(false)
    expect(catalogue.classList.contains('page--embed')).toBe(true)
    expect(catalogue.textContent).toContain(t.gate.catalogueLead)
    expect(wrapper.findComponent(GatesView).find('h1').exists()).toBe(false)
  })
})
