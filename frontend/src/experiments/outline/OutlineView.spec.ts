import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type {
  ExperimentOutput,
  ExperimentRun,
  ExperimentSource,
  PromptSetup,
} from '@/api/types'
import { loadSource, startRun } from '@/experiments/api'

import OutlineView from './OutlineView.vue'

const setup: PromptSetup = {
  system_prompt: 'You plan the running order of a grounded audio episode.',
  user_template: 'Target: {{target_words}}\n{{passages}}',
  fields: { target_words: '750', passages: '[b12] Fotosynthese.' },
  settings: {
    model: 'claude-opus-5',
    temperature: null,
    top_p: null,
    top_k: null,
    thinking: 'default',
    thinking_budget: null,
    effort: null,
    max_tokens: 12000,
    structured: true,
    cache_system: true,
  },
  json_schema: { type: 'object' },
}

const outline = {
  beats: [
    {
      id: 'beat000',
      title: 'Was Licht anrichtet',
      block_ids: ['b12', 'b14'],
      word_budget: 400,
      goal_id: 'g0',
      summary: 'Die Lichtreaktion.',
    },
    {
      id: 'beat001',
      title: 'Zucker aus Luft',
      block_ids: ['b21'],
      word_budget: 350,
      goal_id: null,
      summary: null,
    },
  ],
}

function finished(output: Record<string, unknown>, warnings: string[] = []): ExperimentRun {
  return {
    id: 'run-1',
    experiment_key: 'outline',
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

const raw = { beats: outline.beats.map(({ id: _id, ...rest }) => rest) }
let latest = finished({ payload: raw, text: JSON.stringify(raw), outline })
let collected: ExperimentOutput[] = []

const loaded: ExperimentSource = {
  fields: { target_words: '1800', passages: '[b3] Aus dem Lauf.' },
  beats: [],
  source: {
    run_id: 'prod-1',
    document_id: 'doc-1',
    document_title: 'Photosynthese',
    outline: { beats: [{ ...outline.beats[1], title: 'Produktionsabschnitt' }] },
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
    key: 'outline',
    title: 'Ablaufplan',
    summary: '',
    target: 'Ablaufplan-Schritt',
    version: '1',
    stats: { run_count: 1, saved_count: 0, spent_usd: 0.03, last_run_at: null },
    defaults: setup,
    fields: [
      { key: 'target_words', label: 'Zielwörter', multiline: false, hint: null },
      { key: 'passages', label: 'Belegstellen', multiline: true, hint: null },
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
      { path: '/outline', name: 'outline', component: OutlineView },
    ],
  })
  await router.push('/outline')
  const wrapper = mount(OutlineView, {
    global: { plugins: [router], stubs: { teleport: true } },
  })
  await flushPromises()
  return wrapper
}

function button(wrapper: Awaited<ReturnType<typeof mountView>>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))!
}

describe('Ablaufplan experiment screen', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    latest = finished({ payload: raw, text: JSON.stringify(raw), outline })
    collected = []
  })

  it('reads an outline answer as a running order, with the raw JSON one click away', async () => {
    const wrapper = await mountView()
    const current = wrapper.find('.out--current')
    const beats = current.findAll('.beat')
    expect(beats).toHaveLength(2)
    expect(beats[0].text()).toContain('Was Licht anrichtet')
    expect(beats[0].text()).toContain('400')
    expect(beats[0].text()).toContain('b14')

    await current.findAll('.out__head .seg button')[1].trigger('click')
    const json = current.find('pre.json').text()
    expect(json).toContain('"title": "Zucker aus Luft"')
    expect(json).not.toContain('beat000')
    expect(current.find('.beat').exists()).toBe(false)
  })

  it('shows another shape as raw text with the warning', async () => {
    latest = finished(
      { payload: { sections: ['A'] }, text: '{"sections": ["A"]}', outline: null },
      ['the answer has no "beats" list, so it is shown as raw text'],
    )
    const wrapper = await mountView()
    const current = wrapper.find('.out--current')
    expect(current.find('.beat').exists()).toBe(false)
    expect(current.find('.warnline').text()).toContain('no "beats" list')
    expect(current.text()).toContain('sections')
  })

  it('renders collected outlines as outlines and keeps their settings', async () => {
    collected = [
      {
        id: 'out-1',
        experiment_key: 'outline',
        run_id: 'run-0',
        item: 'main',
        label: 'Plan v1',
        output: { payload: raw, text: '', outline },
        text: '',
        meta: { model: 'claude-opus-5', temperature: 0.3 },
        setup: { ...setup, system_prompt: 'Eigener Prompt.' },
        created_at: '2026-10-02T11:00:00Z',
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

  it('fills the input from a whole run without asking for a beat', async () => {
    const wrapper = await mountView()
    await button(wrapper, 'Eingabe').trigger('click')
    await button(wrapper, 'Aus Lauf laden').trigger('click')
    await flushPromises()
    await button(wrapper, 'Photosynthese').trigger('click')
    await flushPromises()
    expect(wrapper.find('.pick__label').exists()).toBe(false)
    await button(wrapper, 'Felder füllen').trigger('click')
    await flushPromises()

    expect(loadSource).toHaveBeenLastCalledWith('outline', 'prod-1', null)
    expect(wrapper.find('.source').text()).toContain('Photosynthese')
    expect(wrapper.find('.summary').text()).toContain('Lauf')
    expect(wrapper.find('textarea[aria-label="passages"]').element).toHaveProperty(
      'value',
      '[b3] Aus dem Lauf.',
    )
    expect(wrapper.find('.reference').text()).toContain('Produktionsabschnitt')

    await button(wrapper, 'Ausführen').trigger('click')
    await flushPromises()
    expect(startRun).toHaveBeenCalledWith(
      'outline',
      expect.objectContaining({
        fields: expect.objectContaining({ target_words: '1800' }),
      }),
      expect.objectContaining({ run_id: 'prod-1' }),
    )
  })
})
