import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type {
  ExperimentOutput,
  ExperimentRun,
  ExperimentSource,
  PromptSetup,
} from '@/api/types'
import { loadSource, startRun } from '@/experiments/api'

import SeriesPlanView from './SeriesPlanView.vue'

const setup: PromptSetup = {
  system_prompt: 'You split a source document into a series of grounded audio episodes.',
  user_template: 'Plan {{episode_count}} episodes.\n{{document}}',
  fields: { episode_count: '2', document: '[b12] (weight 1.0, 40 words)\nFotosynthese.' },
  settings: {
    model: 'claude-opus-5',
    temperature: null,
    top_p: null,
    top_k: null,
    thinking: 'default',
    thinking_budget: null,
    effort: null,
    max_tokens: 16000,
    structured: true,
    cache_system: true,
  },
  json_schema: { type: 'object' },
}

const plan = {
  title: 'Eine Serie',
  through_line: 'Vom Grundsatz zur Anwendung.',
  episodes: [
    {
      title: 'Was Licht anrichtet',
      role: 'Einführung',
      summary: 'Die Lichtreaktion.',
      block_ids: ['b12', 'b14'],
      goals: [{ text: 'Die Lichtreaktion erklären', bloom_level: 'understand', derivation: null }],
    },
    {
      title: 'Zucker aus Luft',
      role: 'Vertiefung',
      summary: null,
      block_ids: ['b21'],
      goals: [{ text: 'Den Zyklus anwenden', bloom_level: 'apply', derivation: null }],
    },
  ],
}

function finished(output: Record<string, unknown>, warnings: string[] = []): ExperimentRun {
  return {
    id: 'run-1',
    experiment_key: 'series_plan',
    experiment_version: '1',
    status: 'completed',
    setup,
    source: null,
    output,
    warnings,
    error: null,
    total_cost_usd: 0.03,
    tokens_in: 10,
    tokens_out: 20,
    wall_ms: 1000,
    created_at: '2026-10-02T12:00:00Z',
    finished_at: '2026-10-02T12:00:01Z',
    items: [{ item: 'main', title: null, meta: {}, output_id: null }],
    calls: [],
  }
}

let latest = finished({ payload: plan, text: JSON.stringify(plan), plan })
let collected: ExperimentOutput[] = []

const loaded: ExperimentSource = {
  fields: { episode_count: '3', document: '[b3] Aus dem Lauf.' },
  beats: [],
  source: {
    run_id: 'prod-1',
    document_id: 'doc-1',
    document_title: 'Photosynthese',
    plan: { ...plan, episodes: [{ ...plan.episodes[1], title: 'Produktionsfolge' }] },
  },
}

vi.mock('@/api/client', async (original) => ({
  ...(await original<typeof import('@/api/client')>()),
  api: {
    get: vi.fn(async () => [
      {
        id: 'prod-1',
        document_id: 'doc-1',
        document_title: 'Photosynthese',
        flow_id: 'baseline_v0',
        status: 'completed',
        created_at: '2026-10-01T10:00:00Z',
      },
    ]),
  },
}))

vi.mock('@/experiments/api', () => ({
  getExperiment: vi.fn(async () => ({
    key: 'series_plan',
    title: 'Folgen planen',
    summary: '',
    target: 'Serienplaner',
    version: '1',
    stats: { run_count: 1, saved_count: 0, spent_usd: 0.03, last_run_at: null },
    defaults: setup,
    fields: [
      { key: 'episode_count', label: 'Anzahl Folgen', multiline: false, hint: null },
      { key: 'document', label: 'Dokument', multiline: true, hint: null },
    ],
    extras: {},
  })),
  listOutputs: vi.fn(async () => collected),
  listRuns: vi.fn(async () => [latest]),
  isFinished: (r: { status: string }) => r.status === 'completed' || r.status === 'failed',
  startRun: vi.fn(async () => latest),
  loadSource: vi.fn(async () => loaded),
  waitForRun: vi.fn(),
  collect: vi.fn(),
  deleteOutput: vi.fn(),
}))

vi.mock('@/experiments/modelSettings', async (original) => ({
  ...(await original<typeof import('@/experiments/modelSettings')>()),
  loadModelCatalogue: vi.fn(async () => ({ models: [{ id: 'claude-opus-5' }], providers: [] })),
  settingsState: () => ({ valid: true }),
}))

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'experiments', component: { template: '<div />' } },
      { path: '/series-plan', name: 'series-plan', component: SeriesPlanView },
    ],
  })
  await router.push('/series-plan')
  const wrapper = mount(SeriesPlanView, {
    global: { plugins: [router, createPinia()], stubs: { teleport: true, CollectFolder: true } },
  })
  await flushPromises()
  return wrapper
}

function button(wrapper: Awaited<ReturnType<typeof mountView>>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))!
}

describe('Folgen planen experiment screen', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    latest = finished({ payload: plan, text: JSON.stringify(plan), plan })
    collected = []
  })

  it('reads a plan as episodes, with the raw JSON one click away', async () => {
    const wrapper = await mountView()
    const current = wrapper.find('.out--current')
    const episodes = current.findAll('.beat')
    expect(episodes).toHaveLength(2)
    expect(episodes[0].text()).toContain('Was Licht anrichtet')
    expect(episodes[0].text()).toContain('Einführung')
    expect(episodes[0].text()).toContain('Die Lichtreaktion erklären')
    expect(episodes[0].text()).toContain('understand')
    expect(episodes[0].text()).toContain('b14')

    await current.findAll('.out__head .seg button')[1].trigger('click')
    const json = current.find('pre.json').text()
    expect(json).toContain('"title": "Zucker aus Luft"')
    expect(current.find('.beat').exists()).toBe(false)
  })

  it('shows another shape as raw text with the warning', async () => {
    latest = finished(
      { payload: { sections: ['A'] }, text: '{"sections": ["A"]}', plan: null },
      ['the answer has no "episodes" list, so it is shown as raw text'],
    )
    const wrapper = await mountView()
    const current = wrapper.find('.out--current')
    expect(current.find('.beat').exists()).toBe(false)
    expect(current.find('.warnline').text()).toContain('no "episodes" list')
    expect(current.text()).toContain('sections')
  })

  it('renders collected plans as episodes and keeps their settings', async () => {
    collected = [
      {
        id: 'out-1',
        experiment_key: 'series_plan',
        run_id: 'run-0',
        item: 'main',
        label: 'Plan v1',
        output: { payload: plan, text: '', plan },
        text: '',
        meta: { model: 'claude-opus-5', temperature: 0.3 },
        setup: { ...setup, system_prompt: 'Eigener Prompt.' },
        created_at: '2026-10-02T11:00:00Z',
        folder_id: null,
        folder_path: [],
        created_by: null,
        status: null,
        note: null,
        decided_by: null,
        decided_at: null,
      },
    ]
    const wrapper = await mountView()
    const card = wrapper.findAll('.collection .out')[0]
    expect(card.findAll('.beat')).toHaveLength(2)
    expect(card.text()).toContain('T 0.3')

    await button(card as never, 'Setup übernehmen').trigger('click')
    expect(wrapper.find('textarea[aria-label="System-Prompt"]').element).toHaveProperty(
      'value',
      'Eigener Prompt.',
    )
  })

  it('fills the input from a finished run', async () => {
    const wrapper = await mountView()
    await button(wrapper, 'Eingabe').trigger('click')
    await button(wrapper, 'Aus Lauf laden').trigger('click')
    await flushPromises()
    await button(wrapper, 'Photosynthese').trigger('click')
    await flushPromises()
    await button(wrapper, 'Felder füllen').trigger('click')
    await flushPromises()

    expect(loadSource).toHaveBeenLastCalledWith('series_plan', 'prod-1', null)
    expect(wrapper.find('.source').text()).toContain('Photosynthese')
    expect(wrapper.find('.summary').text()).toContain('Lauf')
    expect(wrapper.find('textarea[aria-label="document"]').element).toHaveProperty(
      'value',
      '[b3] Aus dem Lauf.',
    )
    expect(wrapper.find('.reference').text()).toContain('Produktionsfolge')

    await button(wrapper, 'Ausführen').trigger('click')
    await flushPromises()
    expect(startRun).toHaveBeenCalledWith(
      'series_plan',
      expect.objectContaining({
        fields: expect.objectContaining({ episode_count: '3' }),
      }),
      expect.objectContaining({ run_id: 'prod-1' }),
    )
  })
})
