import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { ExperimentRun, ExperimentSource, VSSetup } from '@/api/types'
import { loadSource, startRun } from '@/experiments/api'

import VerbalizedSamplingView from './VerbalizedSamplingView.vue'

const setup: VSSetup = {
  base_prompt: 'You write one beat.',
  vs_instruction: 'Generate {{k}} versions.',
  user_template: 'Beat: {{beat_title}}',
  fields: { beat_title: 'Die Lichtreaktion' },
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
  k: 2,
}

function segment(text: string) {
  return { speaker: 'Moderator', text, kind: 'pedagogy', citations: [] }
}

const run: ExperimentRun<VSSetup> = {
  id: 'run-1',
  experiment_key: 'verbalized_sampling',
  experiment_version: '1',
  status: 'completed',
  setup,
  source: null,
  output: {
    variant: 'standard',
    k: 2,
    word_budget: 220,
    citation_check: false,
    vs: {
      drafts: [0, 1].map((index) => ({
        item: `vs:${index}`,
        source: 'vs',
        index,
        probability: 0.3,
        segments: [segment(`VS-Text ${index}`)],
        words: 2,
        claims: 0,
        pedagogy: 1,
        citations: null,
        flags: [],
      })),
      warnings: ['Fassung #2 ist gleich wie #1.'],
      error: null,
    },
    baseline: {
      drafts: [
        {
          item: 'baseline:0',
          source: 'baseline',
          index: 0,
          probability: null,
          segments: [segment('Plain-Text')],
          words: 1,
          claims: 0,
          pedagogy: 1,
          citations: null,
          flags: [],
        },
      ],
      warnings: [],
      error: null,
    },
    cost: {
      vs_usd: 0.1,
      baseline_usd: 0.02,
      vs_calls: 1,
      baseline_calls: 1,
      tokens_in: 10,
      tokens_out: 20,
      latency_ms: 1000,
      vs_per_draft_usd: 0.05,
      ratio_to_single_baseline: 5,
    },
  },
  warnings: [],
  error: null,
  total_cost_usd: 0.12,
  tokens_in: 10,
  tokens_out: 20,
  wall_ms: 1000,
  created_at: '2026-10-01T12:00:00Z',
  finished_at: '2026-10-01T12:00:01Z',
  items: ['vs:0', 'vs:1', 'baseline:0'].map((item) => ({
    item,
    title: null,
    meta: {},
    output_id: null,
  })),
  calls: [],
}

const loaded: ExperimentSource = {
  fields: { beat_title: 'Die Dunkelreaktion', passages: '[b20]\nDer Calvin-Zyklus.' },
  beats: [
    { id: 'beat-1', title: 'Die Lichtreaktion', word_budget: 220, passage_count: 3 },
    { id: 'beat-2', title: 'Die Dunkelreaktion', word_budget: 180, passage_count: 1 },
  ],
  source: {
    run_id: 'prod-1',
    document_id: 'doc-1',
    document_title: 'Photosynthese',
    beat_id: 'beat-2',
    beat_title: 'Die Dunkelreaktion',
    beat_position: 2,
    beat_total: 2,
    reference: [],
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
    key: 'verbalized_sampling',
    title: 'Verbalized Sampling',
    summary: '',
    target: '',
    version: '1',
    stats: { run_count: 1, saved_count: 0, spent_usd: 0.12, last_run_at: null },
    defaults: setup,
    fields: [],
    extras: { vs_instructions: { kalliope: setup.vs_instruction, paper: 'Paper' } },
  })),
  listOutputs: vi.fn(async () => []),
  listRuns: vi.fn(async () => [run]),
  isFinished: (r: { status: string }) => r.status === 'completed' || r.status === 'failed',
  startRun: vi.fn(async () => ({ ...run, id: 'run-2' })),
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
      { path: '/vs', name: 'vs', component: VerbalizedSamplingView },
    ],
  })
  await router.push('/vs')
  const wrapper = mount(VerbalizedSamplingView, {
    global: { plugins: [router, createPinia()], stubs: { teleport: true, CollectFolder: true } },
  })
  await flushPromises()
  return wrapper
}

function titles(wrapper: Awaited<ReturnType<typeof mountView>>): string[] {
  return wrapper.findAll('.drafts .out__title').map((node) => node.text())
}

function button(wrapper: Awaited<ReturnType<typeof mountView>>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))!
}

function badges(wrapper: Awaited<ReturnType<typeof mountView>>): Record<string, string> {
  return Object.fromEntries(
    wrapper.findAll('.vcard').map((card) => [card.find('code').text(), card.find('.badge').text()]),
  )
}

describe('Verbalized Sampling screen', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('opens a finished run blind, with a stable order', async () => {
    const wrapper = await mountView()
    expect(titles(wrapper)).toEqual(['Fassung A', 'Fassung B', 'Fassung C'])
    const drafts = wrapper.find('.drafts').text()
    expect(drafts).not.toContain('Ohne VS')
    expect(drafts).not.toMatch(/p 0,30/)
    expect(wrapper.text()).not.toContain('Fassung #2 ist gleich')
    const order = drafts
    wrapper.unmount()
    expect((await mountView()).find('.drafts').text()).toBe(order)
  })

  it('reveals methods and probabilities on Aufdecken', async () => {
    const wrapper = await mountView()
    const reveal = wrapper.findAll('button').find((b) => b.text() === 'Aufdecken')!
    await reveal.trigger('click')
    expect(titles(wrapper)).toEqual(['VS #1', 'VS #2', 'Ohne VS'])
    expect(wrapper.find('.drafts').text()).toContain('p 0,30')
    expect(wrapper.text()).toContain('Fassung #2 ist gleich')
    expect(wrapper.findAll('button').some((b) => b.text() === 'Wieder blind')).toBe(true)
  })

  it('shows blind JSON without method or probability', async () => {
    const wrapper = await mountView()
    const current = wrapper.find('.out--current')
    const jsonButton = current.findAll('.out__head .seg button')[1]
    await jsonButton.trigger('click')
    const json = current.find('pre.json').text()
    expect(json).toContain('VS-Text')
    expect(json).not.toContain('probability')
    expect(json).not.toContain('"source"')
  })

  it('fills the input fields from a run and beat, marked as loaded', async () => {
    const wrapper = await mountView()
    await button(wrapper, 'Eingabe').trigger('click')
    expect(badges(wrapper)).toEqual({ beat_title: 'Beispiel' })

    await button(wrapper, 'Aus Lauf laden').trigger('click')
    await flushPromises()
    await button(wrapper, 'Photosynthese').trigger('click')
    await flushPromises()
    expect(loadSource).toHaveBeenLastCalledWith('verbalized_sampling', 'prod-1')
    await button(wrapper, 'Die Dunkelreaktion').trigger('click')
    await button(wrapper, 'Felder füllen').trigger('click')
    await flushPromises()

    expect(loadSource).toHaveBeenLastCalledWith('verbalized_sampling', 'prod-1', 'beat-2')
    expect(badges(wrapper)).toEqual({ beat_title: 'aus Lauf', passages: 'aus Lauf' })
    expect(wrapper.find('.source').text()).toContain('Photosynthese')
    expect(wrapper.find('.summary').text()).toContain('Abschnitt 2/2')
    expect(wrapper.find('textarea[aria-label="passages"]').element).toHaveProperty(
      'value',
      '[b20]\nDer Calvin-Zyklus.',
    )
  })

  it('keeps loaded fields editable and sends where they came from', async () => {
    const wrapper = await mountView()
    await button(wrapper, 'Eingabe').trigger('click')
    await button(wrapper, 'Aus Lauf laden').trigger('click')
    await flushPromises()
    await button(wrapper, 'Photosynthese').trigger('click')
    await flushPromises()
    await button(wrapper, 'Felder füllen').trigger('click')
    await flushPromises()

    await wrapper.find('textarea[aria-label="passages"]').setValue('[b20]\nGekürzt.')
    expect(badges(wrapper)).toEqual({ beat_title: 'aus Lauf', passages: 'bearbeitet' })

    await button(wrapper, 'Beispiel').trigger('click')
    expect(badges(wrapper)).toEqual({ beat_title: 'Beispiel' })
    expect(wrapper.find('.source').text()).toContain('kein Lauf geladen')
  })

  it('starts a run with the loaded source', async () => {
    const wrapper = await mountView()
    await button(wrapper, 'Eingabe').trigger('click')
    await button(wrapper, 'Aus Lauf laden').trigger('click')
    await flushPromises()
    await button(wrapper, 'Photosynthese').trigger('click')
    await flushPromises()
    await button(wrapper, 'Felder füllen').trigger('click')
    await flushPromises()
    await wrapper.find('.btn--primary').trigger('click')
    await flushPromises()
    expect(startRun).toHaveBeenCalledWith(
      'verbalized_sampling',
      expect.objectContaining({ fields: loaded.fields }),
      loaded.source,
    )
  })
})
