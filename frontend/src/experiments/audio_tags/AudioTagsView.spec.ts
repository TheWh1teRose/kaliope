import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { ExperimentRun, ExperimentSource, PromptSetup } from '@/api/types'
import { loadSource, startRun } from '@/experiments/api'

import AudioTagsView from './AudioTagsView.vue'

const setup: PromptSetup = {
  system_prompt: 'You prepare one beat of a podcast script for speech synthesis with ElevenLabs.',
  user_template: 'Speakers:\n{{speakers}}\n{{lines}}',
  fields: {
    speakers: '- Moderator (host)\n- Expertin (expert)',
    lines: 'Moderator: Wovon lebt eine Pflanze?\nExpertin (claim): Von Licht und CO₂.',
  },
  settings: {
    model: 'claude-sonnet-5-5',
    temperature: 0.3,
    top_p: null,
    top_k: null,
    thinking: 'default',
    thinking_budget: null,
    effort: null,
    max_tokens: 8000,
    structured: true,
    cache_system: true,
  },
  json_schema: { type: 'object' },
}

const audioScript = {
  model: 'claude-sonnet-5-5',
  tag_language: 'en',
  lines: [
    {
      segment_id: 'l001',
      beat_id: null,
      speaker: 'Moderator',
      kind: null,
      text: 'Wovon lebt eine Pflanze?',
      spoken: 'Wovon lebt eine Pflanze?',
      tagged: '[curious] Wovon lebt eine Pflanze?',
      spoken_forms: [],
      tags: ['[curious]'],
      guard: 'pass',
      problem: null,
    },
    {
      segment_id: 'l002',
      beat_id: null,
      speaker: 'Expertin',
      kind: 'claim',
      text: 'Von Licht und CO₂.',
      spoken: 'Von Licht und CO₂.',
      tagged: 'Von Licht und CO₂.',
      spoken_forms: [],
      tags: [],
      guard: 'fallback',
      problem: "words changed: 'licht' became 'sonne'",
    },
  ],
}

function finished(output: Record<string, unknown>, warnings: string[] = []): ExperimentRun {
  return {
    id: 'run-1',
    experiment_key: 'audio_tags',
    experiment_version: '1',
    status: 'completed',
    setup,
    source: null,
    output,
    warnings,
    error: null,
    total_cost_usd: 0.01,
    tokens_in: 10,
    tokens_out: 20,
    wall_ms: 1000,
    created_at: '2026-10-03T12:00:00Z',
    finished_at: '2026-10-03T12:00:01Z',
    items: [{ item: 'main', title: null, meta: {}, output_id: null }],
    calls: [],
  }
}

let latest = finished({ payload: {}, text: '{}', audio_script: audioScript })

const loaded: ExperimentSource = {
  fields: {
    speakers: '- Moderator (asks the questions a listener would ask)',
    lines: '[beat001-s0004] Moderator (pedagogy): Aus dem Lauf.',
    context: '',
    language: 'de',
  },
  beats: [],
  source: {
    run_id: 'prod-1',
    document_id: 'doc-1',
    document_title: 'Photosynthese',
    beat_id: 'beat001',
    beat_title: 'Die Lichtreaktion',
    beat_position: 2,
    beat_total: 5,
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
    key: 'audio_tags',
    title: 'Audio ausprobieren',
    summary: '',
    target: 'Audio-Skript-Schritt',
    version: '1',
    stats: { run_count: 1, saved_count: 0, spent_usd: 0.01, last_run_at: null },
    defaults: setup,
    fields: [
      { key: 'lines', label: 'Skript', multiline: true, hint: null },
      { key: 'speakers', label: 'Sprecher', multiline: true, hint: null },
    ],
    extras: {},
  })),
  listOutputs: vi.fn(async () => []),
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
  loadModelCatalogue: vi.fn(async () => ({
    models: [{ id: 'claude-sonnet-5-5' }],
    providers: [],
  })),
  settingsState: () => ({ valid: true }),
}))

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'experiments', component: { template: '<div />' } },
      { path: '/audio', name: 'audio', component: AudioTagsView },
    ],
  })
  await router.push('/audio')
  const wrapper = mount(AudioTagsView, {
    global: {
      plugins: [router, createPinia()],
      stubs: { teleport: true, CollectFolder: true },
    },
  })
  await flushPromises()
  return wrapper
}

function button(wrapper: Awaited<ReturnType<typeof mountView>>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))!
}

describe('Audio ausprobieren experiment screen', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    latest = finished({ payload: {}, text: '{}', audio_script: audioScript })
  })

  it('reads the answer as tagged lines with the guard result per line', async () => {
    const wrapper = await mountView()
    const current = wrapper.find('.out--current')
    const lines = current.findAll('.script-seg')
    expect(lines).toHaveLength(2)
    expect(lines[0].find('.audio-tag').text()).toBe('[curious]')
    expect(lines[0].text()).toContain('Wächter: ok')
    expect(lines[1].text()).toContain('Wächter: ohne Tags')
    expect(lines[1].find('.audio-problem').text()).toContain('sonne')
    expect(current.text()).toContain('2 Zeilen · 1 Tags · 1 ohne Tags')
  })

  it('shows another shape as raw text with the warning', async () => {
    latest = finished({ payload: { other: 1 }, text: '{"other": 1}', audio_script: null }, [
      'the answer is not JSON, so it is shown as raw text',
    ])
    const wrapper = await mountView()
    const current = wrapper.find('.out--current')
    expect(current.find('.script-seg').exists()).toBe(false)
    expect(current.find('.warnline').text()).toContain('not JSON')
  })

  it('fills the lines from a beat of a run and sends them with the run as source', async () => {
    const wrapper = await mountView()
    await button(wrapper, 'Eingabe').trigger('click')
    await button(wrapper, 'Aus Lauf laden').trigger('click')
    await flushPromises()
    await button(wrapper, 'Photosynthese').trigger('click')
    await flushPromises()
    await button(wrapper, 'Felder füllen').trigger('click')
    await flushPromises()

    expect(loadSource).toHaveBeenCalledWith('audio_tags', 'prod-1')
    expect(wrapper.find('.summary').text()).toContain('2/5')
    expect(wrapper.find('textarea[aria-label="lines"]').element).toHaveProperty(
      'value',
      '[beat001-s0004] Moderator (pedagogy): Aus dem Lauf.',
    )

    await button(wrapper, 'Ausführen').trigger('click')
    await flushPromises()
    expect(startRun).toHaveBeenCalledWith(
      'audio_tags',
      expect.objectContaining({
        fields: expect.objectContaining({
          lines: '[beat001-s0004] Moderator (pedagogy): Aus dem Lauf.',
        }),
      }),
      expect.objectContaining({ run_id: 'prod-1' }),
    )
  })
})
