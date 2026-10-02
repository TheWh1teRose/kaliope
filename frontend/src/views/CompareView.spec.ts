import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import type { ExperimentOutput, ExperimentSummary } from '@/api/types'
import OutputDecision from '@/components/experiments/OutputDecision.vue'
import { compareTable } from '@/experiments/compare'
import CompareView from '@/views/CompareView.vue'

interface Call {
  url: string
  method: string
  body?: string
}

const calls: Call[] = []
const cleanups: Array<() => void> = []

const experiments: ExperimentSummary[] = [
  {
    key: 'direct_style',
    title: 'Direkter Stil',
    summary: '',
    target: 'Skript-Schritt',
    version: '1',
    stats: { run_count: 2, saved_count: 2, spent_usd: 0.1, last_run_at: null },
  },
]

function settings(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    model: 'claude-opus-5',
    temperature: null,
    top_p: null,
    top_k: null,
    thinking: 'default',
    thinking_budget: null,
    effort: 'high',
    max_tokens: 8000,
    structured: true,
    ...overrides,
  }
}

function output(
  id: string,
  overrides: Partial<ExperimentOutput<unknown>> = {},
): ExperimentOutput<unknown> {
  return {
    id,
    experiment_key: 'direct_style',
    run_id: `run-${id}`,
    item: 'main',
    label: `Ausgabe ${id}`,
    output: { payload: { segments: [{ speaker: 'Mia', text: `Text ${id}`, kind: 'claim' }] } },
    text: null,
    meta: { model: 'claude-opus-5', tokens_in: 5000, tokens_out: 600, cost_usd: 0.12 },
    setup: {
      system_prompt: 'Sprich die Hörer direkt an.',
      user_template: 'Abschnitt: {{beat}}',
      fields: { beat: 'Licht' },
      settings: settings(),
    },
    created_at: '2026-10-02T09:00:00Z',
    folder_id: null,
    folder_path: [],
    created_by: 'felix@example.com',
    status: null,
    note: null,
    decided_by: null,
    decided_at: null,
    ...overrides,
  }
}

const first = output('a')
const second = output('b', {
  meta: { model: 'claude-sonnet-5-5', tokens_in: 5000, tokens_out: 540, cost_usd: 0.02 },
  setup: {
    system_prompt: 'Sprich die Hörer direkt an. Kurze Sätze.',
    user_template: 'Abschnitt: {{beat}}',
    fields: { beat: 'Licht' },
    settings: settings({ model: 'claude-sonnet-5-5', temperature: 0.7, effort: null }),
  },
})
const third = output('c', { status: 'kandidat', note: 'Mit a vergleichen.' })

let served: ExperimentOutput<unknown>[] = [first, second, third]

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
      const body = typeof init?.body === 'string' ? init.body : undefined
      calls.push({ url, method, body })
      if (method === 'PATCH') {
        const id = url.split('/').at(-1)!
        const base = served.find((item) => item.id === id)!
        return json({ ...base, ...JSON.parse(body ?? '{}'), decided_by: 'felix@example.com' })
      }
      if (url.startsWith('/api/experiments/outputs')) {
        const wanted = new URL(url, 'http://x').searchParams.get('ids')?.split(',') ?? []
        const items = served.filter((item) => wanted.includes(item.id)).reverse()
        return json({ items, total: items.length, all_count: 3, root_count: 3 })
      }
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
      { path: '/experiments/compare', name: 'experiment-compare', component: CompareView },
    ],
  })
  await router.push(path)
  await router.isReady()
  return router
}

async function mountAt(path: string) {
  const router = await routerAt(path)
  const wrapper = mount(CompareView, { attachTo: document.body, global: { plugins: [router] } })
  cleanups.push(() => wrapper.unmount())
  await settle()
  return { wrapper, router }
}

beforeEach(() => {
  served = [first, second, third]
  installFetch()
})

afterEach(() => {
  while (cleanups.length) cleanups.pop()?.()
  vi.unstubAllGlobals()
})

describe('compare table', () => {
  it('marks only the rows that differ in what was sent', () => {
    const table = compareTable([first, second], () => 'Direkter Stil', new Set())
    const row = (key: string) => table.sent.find((r) => r.key === key)
    expect(row('model')?.values).toEqual(['claude-opus-5', 'claude-sonnet-5-5'])
    expect(row('model')?.differs).toBe(true)
    expect(row('temperature')?.values).toEqual(['Standard', '0,7'])
    expect(row('system')?.differs).toBe(true)
    expect(row('template')?.differs).toBe(false)
    expect(row('experiment')?.differs).toBe(false)
    // Rows empty for every output are left out.
    expect(row('vs')).toBeUndefined()
    expect(table.result.find((r) => r.key === 'cost')?.values).toEqual(['$0.1200', '$0.0200'])
  })

  it('keeps a VS draft blind until its run is revealed', () => {
    const draft = output('vs', {
      experiment_key: 'verbalized_sampling',
      run_id: 'run-vs',
      meta: { vs_source: 'vs', vs_index: 1, probability: 0.18, k: 5, variant: 'standard' },
    })
    const blind = compareTable([draft, first], () => 'x', new Set())
    expect(blind.sent.find((r) => r.key === 'source')?.values[0]).toBe('blind')
    const open = compareTable([draft, first], () => 'x', new Set(['run-vs']))
    expect(open.sent.find((r) => r.key === 'source')?.values[0]).toBe('VS #2 · p 0,18')
    expect(open.sent.find((r) => r.key === 'vs')?.values).toEqual(['5 / standard', '—'])
  })
})

describe('compare view', () => {
  it('shows the outputs in the order of the link, with their settings', async () => {
    const { wrapper } = await mountAt('/experiments/compare?ids=a,b,c')
    expect(calls.some((call) => call.url.includes('ids=a%2Cb%2Cc'))).toBe(true)
    const heads = wrapper.findAll('thead th').map((th) => th.text())
    expect(heads.slice(1)).toEqual(['1Ausgabe a', '2Ausgabe b', '3Ausgabe c'])
    expect(wrapper.find('tr[data-row="model"]').classes()).toContain('diff')
    expect(wrapper.find('tr[data-row="template"]').classes()).not.toContain('diff')
    expect(wrapper.findAll('.cols .out')).toHaveLength(3)
    expect(wrapper.text()).toContain('Mit a vergleichen.')
  })

  it('hides the rows that are the same', async () => {
    const { wrapper } = await mountAt('/experiments/compare?ids=a,b')
    expect(wrapper.find('tr[data-row="template"]').exists()).toBe(true)
    await wrapper.find('.check input').setValue(true)
    expect(wrapper.find('tr[data-row="template"]').exists()).toBe(false)
    expect(wrapper.find('tr[data-row="model"]').exists()).toBe(true)
    expect(wrapper.find('tr[data-row="cost"]').exists()).toBe(false)
  })

  it('drops a column from the link, but keeps at least two', async () => {
    const { wrapper, router } = await mountAt('/experiments/compare?ids=a,b,c')
    const remove = wrapper.findAll('.cols .out')[2].findAll('button').at(-1)!
    await remove.trigger('click')
    await settle()
    expect(router.currentRoute.value.query.ids).toBe('a,b')
    expect(wrapper.findAll('.cols .out')).toHaveLength(2)
    expect(wrapper.findAll('.cols .out')[0].findAll('button').at(-1)!.attributes()).toHaveProperty(
      'disabled',
    )
  })

  it('says when outputs are gone and needs two to compare', async () => {
    served = [first]
    const { wrapper } = await mountAt('/experiments/compare?ids=a,gone')
    expect(wrapper.text()).toContain('Nicht mehr vorhanden: 1')
    expect(wrapper.text()).toContain('mindestens zwei')
    expect(wrapper.find('table').exists()).toBe(false)
  })

  it('records a status and a note in a column', async () => {
    const { wrapper } = await mountAt('/experiments/compare?ids=a,b')
    const column = wrapper.findAll('.cols .out')[0]
    await column.find('button[data-status="gewaehlt"]').trigger('click')
    await settle()
    const patch = calls.find((call) => call.method === 'PATCH')
    expect(patch?.url).toBe('/api/experiments/outputs/a')
    expect(JSON.parse(patch!.body!)).toEqual({ status: 'gewaehlt' })
    expect(
      wrapper.findAll('.cols .out')[0].find('button[data-status="gewaehlt"]').attributes(
        'aria-pressed',
      ),
    ).toBe('true')
  })
})

describe('decision', () => {
  it('clears the active status and saves a note', async () => {
    const wrapper = mount(OutputDecision, { props: { output: third } })
    await wrapper.find('button[data-status="kandidat"]').trigger('click')
    await settle()
    expect(JSON.parse(calls.at(-1)!.body!)).toEqual({ status: null })

    const noteButton = wrapper.findAll('button').find((b) => b.text() === 'Notiz ändern')!
    await noteButton.trigger('click')
    await wrapper.find('textarea').setValue('Gewählt für Folge 3.')
    await wrapper.find('form').trigger('submit')
    await settle()
    expect(JSON.parse(calls.at(-1)!.body!)).toEqual({ note: 'Gewählt für Folge 3.' })
    expect(wrapper.emitted('updated')).toHaveLength(2)
    wrapper.unmount()
  })
})
