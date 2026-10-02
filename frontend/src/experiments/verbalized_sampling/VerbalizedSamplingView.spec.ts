import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { ExperimentRun, VSSetup } from '@/api/types'

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
  startRun: vi.fn(),
  waitForRun: vi.fn(),
  collect: vi.fn(),
  deleteOutput: vi.fn(),
}))

vi.mock('@/experiments/modelSettings', async (original) => ({
  ...(await original<typeof import('@/experiments/modelSettings')>()),
  loadModelCatalogue: vi.fn(async () => ({ models: [], providers: [] })),
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
  const wrapper = mount(VerbalizedSamplingView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

function titles(wrapper: Awaited<ReturnType<typeof mountView>>): string[] {
  return wrapper.findAll('.drafts .out__title').map((node) => node.text())
}

describe('Verbalized Sampling screen', () => {
  beforeEach(() => localStorage.clear())

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
})
