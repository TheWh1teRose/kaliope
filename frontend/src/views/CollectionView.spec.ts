import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import type {
  ExperimentOutput,
  ExperimentSummary,
  OutputFolderOut,
  OutputPage,
} from '@/api/types'
import CollectFolder from '@/components/experiments/CollectFolder.vue'
import CollectionList from '@/components/experiments/CollectionList.vue'
import FolderTree from '@/components/FolderTree.vue'
import { presenterFor } from '@/experiments/presenters'
import { type FolderRow, useFoldersStore, useOutputFoldersStore } from '@/stores/folders'
import CollectionView from '@/views/CollectionView.vue'
import ExperimentsView from '@/views/ExperimentsView.vue'

interface Call {
  url: string
  method: string
  body?: string
}

const calls: Call[] = []
let pinia: Pinia
const cleanups: Array<() => void> = []

const folders: OutputFolderOut[] = [
  {
    id: 'f-ton',
    name: 'Ton & Stil',
    parent_id: null,
    depth: 0,
    path: ['Ton & Stil'],
    output_count: 0,
    total_output_count: 1,
    created_at: '2026-10-01T00:00:00Z',
  },
  {
    id: 'f-opus',
    name: 'Opus',
    parent_id: 'f-ton',
    depth: 1,
    path: ['Ton & Stil', 'Opus'],
    output_count: 1,
    total_output_count: 1,
    created_at: '2026-10-01T00:00:00Z',
  },
]

const experiments: ExperimentSummary[] = [
  {
    key: 'direct_style',
    title: 'Direkter Stil',
    summary: '',
    target: 'Skript-Schritt',
    version: '1',
    stats: { run_count: 2, saved_count: 1, spent_usd: 0.1, last_run_at: null },
  },
  {
    key: 'verbalized_sampling',
    title: 'Verbalized Sampling',
    summary: '',
    target: 'Skript-Schritt',
    version: '1',
    stats: { run_count: 1, saved_count: 1, spent_usd: 0.2, last_run_at: null },
  },
]

function output(overrides: Partial<ExperimentOutput<unknown>> = {}): ExperimentOutput<unknown> {
  return {
    id: 'o-1',
    experiment_key: 'direct_style',
    run_id: 'r-1',
    item: 'main',
    label: 'Du-Ansprache',
    output: { payload: { segments: [{ speaker: 'Mia', text: 'Hallo Licht', kind: 'claim' }] } },
    text: null,
    meta: { model: 'claude-opus-5' },
    setup: {},
    created_at: '2026-10-02T09:00:00Z',
    folder_id: 'f-opus',
    folder_path: ['Ton & Stil', 'Opus'],
    created_by: 'felix@example.com',
    status: null,
    note: null,
    decided_by: null,
    decided_at: null,
    ...overrides,
  }
}

const vsDraft = output({
  id: 'o-2',
  experiment_key: 'verbalized_sampling',
  run_id: 'r-vs',
  item: 'vs:0',
  label: null,
  output: {
    vs: {
      drafts: [
        {
          item: 'vs:0',
          source: 'vs',
          index: 0,
          probability: 0.3,
          words: 3,
          claims: 1,
          pedagogy: 0,
          segments: [{ speaker: 'Jonas', text: 'Pflanzen frühstücken Licht.', kind: 'claim' }],
        },
      ],
    },
    baseline: { drafts: [] },
  },
  folder_id: null,
  folder_path: [],
})

function page(items: ExperimentOutput<unknown>[]): OutputPage {
  return { items, total: items.length, all_count: 2, root_count: 1 }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function installFetch(): void {
  calls.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
      if (url === '/api/experiment-folders' && method === 'GET') return json(folders)
      if (url === '/api/experiment-folders' && method === 'POST') {
        return json({ ...folders[0], id: 'f-neu', name: 'Neu', path: ['Neu'] }, 201)
      }
      if (url.startsWith('/api/experiments/outputs/move')) return json({ moved: 1 })
      if (url.startsWith('/api/experiments/outputs')) return json(page([output(), vsDraft]))
      if (url === '/api/experiments') return json(experiments)
      return json({})
    }),
  )
}

async function settle(): Promise<void> {
  for (let i = 0; i < 6; i += 1) await flushPromises()
}

async function routerAt(path: string): Promise<Router> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/experiments', name: 'experiments', component: { template: '<div />' } },
      { path: '/experiments/:key', name: 'experiment', component: { template: '<div />' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  return router
}

async function mountCollection(path = '/experiments?tab=collection') {
  const wrapper = mount(CollectionView, {
    props: { experiments },
    attachTo: document.body,
    global: { plugins: [pinia, await routerAt(path)] },
  })
  cleanups.push(() => wrapper.unmount())
  await settle()
  return wrapper
}

function outputGets(): string[] {
  return calls
    .filter((call) => call.method === 'GET' && call.url.startsWith('/api/experiments/outputs'))
    .map((call) => call.url)
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
  installFetch()
})

afterEach(() => {
  while (cleanups.length) cleanups.pop()?.()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('Experimente page tabs', () => {
  it('shows the experiment cards by default and the Sammlung on ?tab=collection', async () => {
    const cards = mount(ExperimentsView, {
      global: { plugins: [pinia, await routerAt('/experiments')] },
    })
    cleanups.push(() => cards.unmount())
    await settle()
    expect(cards.findAll('.xcard')).toHaveLength(2)
    expect(cards.find('.lib').exists()).toBe(false)
    expect(cards.find('.tab--on').text()).toContain('Experimente')

    const router = await routerAt('/experiments?tab=collection')
    const sammlung = mount(ExperimentsView, { global: { plugins: [pinia, router] } })
    cleanups.push(() => sammlung.unmount())
    await settle()
    expect(sammlung.find('.lib').exists()).toBe(true)
    expect(sammlung.find('.tab--on').text()).toContain('Sammlung')
    // The tab counts every collected output.
    expect(sammlung.find('.tab--on').text()).toContain('2')

    await sammlung.findAll('.tab')[0].trigger('click')
    await settle()
    expect(router.currentRoute.value.query.tab).toBeUndefined()
  })
})

describe('Sammlung', () => {
  it('lists every experiment, each read as its own page reads it', async () => {
    const wrapper = await mountCollection()
    const titles = wrapper.findAll('.out__title').map((node) => node.text())
    expect(titles).toEqual(['Du-Ansprache', 'Fassung A (blind gesammelt)'])
    const first = wrapper.findAll('.out')[0]
    expect(first.text()).toContain('Direkter Stil')
    expect(first.text()).toContain('▸ Ton & Stil / Opus')
    expect(first.text()).toContain('von felix@example.com')
    // A blind VS draft shows neither its method nor its probability.
    expect(wrapper.findAll('.out')[1].text()).not.toContain('p 0,30')
    expect(wrapper.text()).toContain('Pflanzen frühstücken Licht.')
  })

  it('filters by folder, experiment and search text', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const wrapper = await mountCollection('/experiments?tab=collection&experiment=direct_style')
    expect(outputGets().at(-1)).toContain('experiment=direct_style')

    await wrapper.findAll('.tree .row').find((row) => row.text().includes('Ton & Stil'))!.trigger(
      'click',
    )
    await settle()
    expect(outputGets().at(-1)).toContain('folder_id=f-ton')
    expect(wrapper.find('.crumbs').text()).toContain('Ton & Stil')

    await wrapper.find('.check input').setValue(false)
    await settle()
    expect(outputGets().at(-1)).toContain('include_sub=false')

    await wrapper.find('#sammlung-q').setValue('Licht')
    await vi.advanceTimersByTimeAsync(300)
    await settle()
    expect(outputGets().at(-1)).toContain('q=Licht')
  })

  it('moves several outputs at once from the selection bar', async () => {
    const wrapper = await mountCollection()
    const boxes = wrapper.findAll('.check__box')
    await boxes[0].setValue(true)
    await boxes[1].setValue(true)
    expect(wrapper.find('.bulk').text()).toContain('2 ausgewählt')

    await wrapper.findAll('.bulk .btn').find((b) => b.text().includes('In Ordner'))!.trigger('click')
    await settle()
    const dialog = document.body.querySelector('[role="dialog"]') as HTMLElement
    const target = [...dialog.querySelectorAll('label')].find((l) => l.textContent?.includes('Opus'))
    target!.querySelector('input')!.click()
    await settle()
    ;[...dialog.querySelectorAll('button')].find((b) => b.textContent?.includes('Verschieben'))!.click()
    await settle()

    const move = calls.find((call) => call.url === '/api/experiments/outputs/move')
    expect(JSON.parse(move!.body!)).toEqual({ ids: ['o-1', 'o-2'], folder_id: 'f-opus' })
    expect(wrapper.find('.bulk').exists()).toBe(false)
  })

  it('files the cards dropped onto a folder', async () => {
    const wrapper = await mountCollection()
    wrapper.getComponent({ name: 'FolderTree' }).vm.$emit('drop-items', {
      ids: ['o-2'],
      folderId: null,
    })
    await settle()
    const move = calls.find((call) => call.url === '/api/experiments/outputs/move')
    expect(JSON.parse(move!.body!)).toEqual({ ids: ['o-2'], folder_id: null })
  })
})

describe('FolderTree for outputs', () => {
  it('uses its own labels, drag type and counts', async () => {
    const wrapper = mount(FolderTree, {
      props: {
        folders,
        selected: null,
        dropping: true,
        totalCount: 7,
        rootCount: 2,
        countOf: (folder: FolderRow) => (folder as OutputFolderOut).total_output_count,
        dragType: 'text/kalliope-output',
        labels: { title: 'Ordner', all: 'Alle Ausgaben', root: 'Ohne Ordner' },
      },
    })
    expect(wrapper.text()).toContain('Alle Ausgaben')
    const opus = wrapper.findAll('.row').find((row) => row.text().includes('Opus'))!
    const data = new Map([['text/kalliope-output', 'o-1,o-2']])
    await opus.trigger('drop', { dataTransfer: { getData: (type: string) => data.get(type) ?? '' } })
    expect(wrapper.emitted('drop-items')?.[0]).toEqual([{ ids: ['o-1', 'o-2'], folderId: 'f-opus' }])

    // A document dragged here carries another type and files nothing.
    await opus.trigger('drop', { dataTransfer: { getData: () => '' } })
    expect(wrapper.emitted('drop-items')).toHaveLength(1)
  })
})

describe('Sammeln in', () => {
  it('remembers the folder per experiment and falls back when it is gone', async () => {
    localStorage.setItem('kalliope-collect-folder:direct_style', 'f-opus')
    localStorage.setItem('kalliope-collect-folder:outline', 'f-deleted')
    const direct = mount(CollectFolder, {
      props: { experimentKey: 'direct_style', modelValue: null },
      global: { plugins: [pinia] },
    })
    const outline = mount(CollectFolder, {
      props: { experimentKey: 'outline', modelValue: null },
      global: { plugins: [pinia] },
    })
    await settle()
    expect(direct.emitted('update:modelValue')?.at(-1)).toEqual(['f-opus'])
    expect(outline.emitted('update:modelValue')?.at(-1)).toEqual([null])
    expect(localStorage.getItem('kalliope-collect-folder:outline')).toBeNull()

    await direct.setProps({ modelValue: 'f-ton' })
    await settle()
    expect(localStorage.getItem('kalliope-collect-folder:direct_style')).toBe('f-ton')
    direct.unmount()
    outline.unmount()
  })
})

describe('per-experiment collection', () => {
  it('shows the folder and files an output with "In Ordner …"', async () => {
    const wrapper = mount(CollectionList, {
      props: { outputs: [output()], experimentKey: 'direct_style' },
      attachTo: document.body,
      global: { plugins: [pinia, await routerAt('/experiments/direct_style')] },
    })
    cleanups.push(() => wrapper.unmount())
    expect(wrapper.find('.folderchip').text()).toBe('▸ Ton & Stil / Opus')
    expect(wrapper.find('a').attributes('href')).toBe(
      '/experiments?tab=collection&experiment=direct_style',
    )

    await wrapper.findAll('button').find((b) => b.text().includes('In Ordner'))!.trigger('click')
    await settle()
    const dialog = document.body.querySelector('[role="dialog"]') as HTMLElement
    ;[...dialog.querySelectorAll('label')][0].querySelector('input')!.click()
    await settle()
    ;[...dialog.querySelectorAll('button')].find((b) => b.textContent?.includes('Verschieben'))!.click()
    await settle()
    const move = calls.find((call) => call.url === '/api/experiments/outputs/move')
    expect(JSON.parse(move!.body!)).toEqual({ ids: ['o-1'], folder_id: null })
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })
})

describe('output presenters', () => {
  it('reads VS drafts blind until revealed and outlines as outlines', () => {
    expect(presenterFor('verbalized_sampling', new Set()).titleOf(vsDraft)).toContain('blind')
    const revealed = presenterFor('verbalized_sampling', new Set(['r-vs']))
    expect(revealed.titleOf(vsDraft)).toBe('VS #1')
    expect(revealed.badgesOf(vsDraft).map((b) => b.text)).toContain('p 0,30')

    const outline = output({ experiment_key: 'outline', output: { outline: { beats: [] } } })
    expect(presenterFor('outline', new Set()).artifactOf(outline)?.model).toBe('Outline')
    const selection = output({ experiment_key: 'selection', output: { selection: { picks: [] } } })
    expect(presenterFor('selection', new Set()).artifactOf(selection)?.model).toBe('Selection')
    const series = output({
      experiment_key: 'series_plan',
      output: { plan: { episodes: [] } },
    })
    expect(presenterFor('series_plan', new Set()).artifactOf(series)?.model).toBe('SeriesPlan')
    expect(presenterFor('direct_style', new Set()).artifactOf(output())).toBeNull()
  })

  it('keeps the two folder trees on their own endpoints', async () => {
    await useOutputFoldersStore().load()
    await useFoldersStore().load()
    expect(calls.map((call) => call.url)).toEqual(['/api/experiment-folders', '/api/folders'])
  })
})
