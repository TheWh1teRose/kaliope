import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

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
    gates: [{ id: 'G0' }],
  }),
}))

vi.mock('@/views/GatesView.vue', () => ({
  default: {
    name: 'GatesView',
    props: ['embedded', 'id'],
    template: '<section class="gates-catalogue" />',
  },
}))

async function open(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/pipelines', name: 'pipelines', component: PipelinesView },
      { path: '/pipelines/:id', name: 'pipeline', component: { template: '<div />' } },
      { path: '/formats/:id', name: 'format', component: { template: '<div />' } },
    ],
  })
  await router.push(path)
  const wrapper = mount(PipelinesView, { global: { plugins: [router] } })
  await flushPromises()
  return { router, wrapper }
}

function tab(wrapper: ReturnType<typeof mount>, label: string) {
  const button = wrapper.findAll('button.tab').find((item) => item.text().includes(label))
  if (!button) throw new Error(`missing tab ${label}`)
  return button
}

describe('pipelines quality checks tab', () => {
  it('opens the catalogue from its address and leaves the other tabs alone', async () => {
    const { wrapper } = await open('/pipelines?tab=gates')

    expect(tab(wrapper, 'Qualitätsprüfungen').classes()).toContain('tab--on')
    expect(wrapper.find('.gates-catalogue').exists()).toBe(true)
    expect(wrapper.find('.toggle').exists()).toBe(false)
    expect(tab(wrapper, 'Formate').classes()).not.toContain('tab--on')
  })

  it('writes the chosen tab into the address', async () => {
    const { router, wrapper } = await open('/pipelines')

    expect(tab(wrapper, 'Pipelines').classes()).toContain('tab--on')
    expect(router.currentRoute.value.query.tab).toBeUndefined()

    await tab(wrapper, 'Qualitätsprüfungen').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/pipelines?tab=gates')
    expect(tab(wrapper, 'Qualitätsprüfungen').classes()).toContain('tab--on')
    expect(wrapper.find('.gates-catalogue').exists()).toBe(true)
  })

  it('switches tabs in place so back leaves the page', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/documents', name: 'documents', component: { template: '<div />' } },
        { path: '/pipelines', name: 'pipelines', component: PipelinesView },
        { path: '/pipelines/:id', name: 'pipeline', component: { template: '<div />' } },
        { path: '/formats/:id', name: 'format', component: { template: '<div />' } },
      ],
    })
    await router.push('/documents')
    await router.push('/pipelines')
    const wrapper = mount(PipelinesView, { global: { plugins: [router] } })
    await flushPromises()

    await tab(wrapper, 'Formate').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/pipelines?tab=formats')

    await tab(wrapper, 'Knoten').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/pipelines?tab=nodes')

    await tab(wrapper, 'Qualitätsprüfungen').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/pipelines?tab=gates')

    await tab(wrapper, 'Pipelines').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/pipelines')

    router.back()
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('documents')
  })
})
