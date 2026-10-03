import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

import type { PipelineSummary } from '@/api/types'

import PipelinesView from './PipelinesView.vue'

const storeState = vi.hoisted(() => ({
  pipelines: [] as PipelineSummary[],
}))

vi.mock('@/stores/pipelines', () => ({
  usePipelinesStore: () => ({
    get pipelines() {
      return storeState.pipelines
    },
    formats: [],
    nodes: null,
    list: vi.fn(async () => storeState.pipelines),
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

function pipeline(partial: Partial<PipelineSummary> & Pick<PipelineSummary, 'id' | 'name'>): PipelineSummary {
  return {
    version: '1.0',
    revision: 1,
    description: null,
    origin: 'file',
    archived: false,
    editable: true,
    nodes: ['script'],
    gates: [],
    updated_at: null,
    updated_by_email: null,
    run_count: 0,
    valid: true,
    purpose: 'episode',
    ...partial,
  }
}

describe('pipeline groups', () => {
  it('groups by flow kind, counts them, and sorts names inside a group', async () => {
    storeState.pipelines = [
      pipeline({
        id: 'zeta',
        name: 'Zeta',
        purpose: 'episode',
        nodes: ['outline', 'script'],
      }),
      pipeline({
        id: 'voice',
        name: 'Dialog',
        purpose: '',
        nodes: ['audio_script'],
      }),
      pipeline({
        id: 'plan',
        name: 'Plan',
        purpose: 'series_plan',
        nodes: ['series_plan'],
      }),
      pipeline({
        id: 'alpha',
        name: 'Alpha',
        purpose: 'episode',
        nodes: ['script'],
      }),
      pipeline({
        id: 'old-voice',
        name: 'Archiviertes Audio',
        purpose: 'audio',
        archived: true,
        nodes: ['audio_render'],
      }),
    ]
    const { wrapper } = await open('/pipelines')

    const groups = wrapper.findAll('[data-group]')
    expect(groups.map((group) => group.attributes('data-group'))).toEqual([
      'episode',
      'series_plan',
      'audio',
    ])
    const heading = (group: (typeof groups)[number]) =>
      group.find('.group__title').text().replace(/\s+/g, ' ').trim()
    expect(groups.map(heading)).toEqual(['Normal 2', 'Serie 1', 'Audio 2'])
    expect(groups[0].findAll('.item__name').map((item) => item.text())).toEqual(['Alpha', 'Zeta'])
    const audio = groups[2]
    expect(audio.text()).toContain('Archiviertes Audio')
    expect(audio.text()).toContain('Archiviert')
    expect(wrapper.findAll('[data-group="archived"]')).toHaveLength(0)

    storeState.pipelines = []
  })
})

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
