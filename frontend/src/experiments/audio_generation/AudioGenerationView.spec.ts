import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import View from './AudioGenerationView.vue'

const mocks = vi.hoisted(() => ({
  startRun: vi.fn(),
  listRuns: vi.fn(),
  waitForRun: vi.fn(),
  loadSource: vi.fn(),
  takes: vi.fn(),
  stop: vi.fn(),
  resume: vi.fn(),
  voices: vi.fn(),
  status: vi.fn(),
  sample: vi.fn(),
}))
vi.mock('@/experiments/api', () => ({
  getExperiment: async () => ({
    key: 'audio_generation',
    title: 'ElevenLabs ausprobieren',
    summary: 'Vorbereitetes Skript',
    stats: { spent_usd: 0, run_count: 0 },
    extras: { models: ['eleven_v4', 'eleven_v3'], characters_per_second: 10, sample_characters: 60 },
  }),
  isFinished: (run: { status: string }) => run.status === 'completed' || run.status === 'failed',
  startRun: mocks.startRun,
  listRuns: mocks.listRuns,
  waitForRun: mocks.waitForRun,
  loadSource: mocks.loadSource,
}))
vi.mock('@/api/audio', () => ({
  audioApi: mocks,
  takeActive: (s: string) => ['queued', 'running'].includes(s),
  lineAt: () => null,
}))
vi.mock('vue-router', () => ({ onBeforeRouteLeave: () => undefined, RouterLink: { template: '<a><slot /></a>' } }))

const script = {
  lines: [
    { segment_id: 's1', speaker: 'Host', tagged: '[curious] Exact  words [pause]' },
    { segment_id: 's2', speaker: 'Gast', tagged: 'Second line.' },
    { segment_id: 's3', speaker: 'Host', tagged: 'Third line.' },
  ],
}
const loaded = {
  source: { run_id: 'r1', beat_id: 't1', artifact_hash: 'hash', name: 'Folge 1' },
  fields: { artifact_hash: 'hash', audio_script: JSON.stringify(script) },
  beats: [],
}
const voices = [
  { voice_id: 'v1', name: 'Jonas', preview_url: 'https://cdn.test/jonas.mp3', category: null, description: null, language: 'de' },
  { voice_id: 'v2', name: 'Mara', preview_url: null, category: null, description: null, language: 'de' },
]

function mountView(): VueWrapper {
  return mount(View, {
    global: { stubs: { SourceLoader: { name: 'SourceLoader', props: ['source'], template: '<div />' } } },
  })
}

async function load(wrapper: VueWrapper): Promise<void> {
  wrapper.findComponent({ name: 'SourceLoader' }).vm.$emit('loaded', loaded)
  await flushPromises()
}

async function tab(wrapper: VueWrapper, name: string): Promise<void> {
  await wrapper.find(`[data-tab="${name}"]`).trigger('click')
}

beforeEach(() => {
  localStorage.clear()
  mocks.listRuns.mockResolvedValue([])
  mocks.status.mockResolvedValue({ configured: true, usd_per_1k_characters: { eleven_v4: 0.08, eleven_v3: 0.1 } })
  mocks.voices.mockResolvedValue(voices)
  mocks.startRun.mockResolvedValue({ id: 'experiment1', status: 'queued', created_at: '2026-10-06', total_cost_usd: 0, setup: {}, source: null, output: null })
  mocks.waitForRun.mockResolvedValue({ status: 'completed' })
  mocks.loadSource.mockResolvedValue(loaded)
  mocks.takes.mockResolvedValue({ takes: [] })
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

describe('ElevenLabs experiment', () => {
  it('shows the empty states before a script is loaded', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Noch kein Skript')
    expect(wrapper.text()).toContain('Noch kein Audio.')
    expect(wrapper.find('[data-generate]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('shows the prepared script segment by segment with a live estimate, without generating', async () => {
    const wrapper = mountView()
    await flushPromises()
    await load(wrapper)
    const segments = wrapper.findAll('[data-segment]')
    expect(segments).toHaveLength(3)
    expect(segments[0]!.text()).toContain('Exact  words')
    expect(segments[0]!.find('.tag').text()).toBe('[curious]')
    const estimate = wrapper.find('[data-estimate]').text()
    expect(estimate).toContain('3 von 3 Abschnitten')
    expect(estimate).toContain('53 Zeichen')
    expect(estimate).toContain('Ohne Stimme: Host, Gast')

    await segments[1]!.find('input[type="checkbox"]').trigger('click')
    expect(wrapper.find('[data-estimate]').text()).toContain('2 von 3 Abschnitten')
    expect(wrapper.find('[data-estimate]').text()).toContain('Ohne Stimme: Host')
    expect(wrapper.find('[data-estimate]').text()).not.toContain('Gast')
    expect(mocks.startRun).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('edits keep the speaker, can be reset, and are what gets synthesized', async () => {
    const wrapper = mountView()
    await flushPromises()
    await load(wrapper)
    await wrapper.find('[data-range="from"]').setValue('2')
    await wrapper.find('[data-range="to"]').setValue('3')
    await wrapper.find('[data-range="apply"]').trigger('click')
    expect(wrapper.find('[data-estimate]').text()).toContain('2 von 3 Abschnitten')

    const second = () => wrapper.find('[data-segment="s2"]')
    await second().find('[data-edit-start]').trigger('click')
    await second().find('textarea').setValue('[excited] Ganz neu.')
    expect(second().text()).toContain('Gast')
    expect(second().text()).toContain('bearbeitet')
    await second().find('[data-restore]').trigger('click')
    expect(second().text()).not.toContain('bearbeitet')
    expect((second().find('textarea').element as HTMLTextAreaElement).value).toBe('Second line.')
    await second().find('textarea').setValue('[excited] Ganz neu.')

    await tab(wrapper, 'voices')
    await flushPromises()
    const selects = wrapper.findAll('select')
    await selects[0]!.setValue('v1')
    await selects[1]!.setValue('v2')
    await tab(wrapper, 'model')
    await wrapper.findAll('.seg button').find((b) => b.text() === 'eleven_v3')!.trigger('click')
    await wrapper.findAll('.seg button').find((b) => b.text().startsWith('Robust'))!.trigger('click')
    expect(mocks.startRun).not.toHaveBeenCalled()
    expect(wrapper.find('[data-generate]').text()).toContain('Audio generieren')

    mocks.listRuns.mockResolvedValue([
      {
        id: 'experiment1',
        created_at: '2026-10-06T10:00:00Z',
        status: 'completed',
        total_cost_usd: 0,
        setup: { artifact_hash: 'hash', voice_cast: { model_id: 'eleven_v3', stability: 1, voices: [] }, segment_ids: ['s2', 's3'], edits: { s2: '[excited] Ganz neu.' } },
        source: loaded.source,
        output: { run_id: 'r1', take_id: 'take1', segments: 2, characters: 30, edited: 1 },
      },
    ])
    mocks.takes.mockResolvedValue({
      takes: [
        {
          id: 'take1',
          status: 'completed',
          error: null,
          total_cost_usd: 0.003,
          plan: [{ status: 'done' }],
          chunks: [],
          resumable: false,
          audio_script: { lines: [{ segment_id: 's2', speaker: 'Gast', tagged: '[excited] Ganz neu.' }] },
          mix: { url: '/api/audio/takes/take1/mix', download_url: '/api/audio/takes/take1/mix?download=1', duration_s: 3, lines: [], chunk_offsets_s: [] },
        },
      ],
    })
    await wrapper.find('[data-generate]').trigger('click')
    await flushPromises()
    expect(mocks.startRun).toHaveBeenCalledTimes(1)
    const [key, sent, source] = mocks.startRun.mock.calls[0]!
    expect(key).toBe('audio_generation')
    expect(source).toEqual(loaded.source)
    expect(sent).toEqual(
      expect.objectContaining({
        artifact_hash: 'hash',
        segment_ids: ['s2', 's3'],
        edits: { s2: '[excited] Ganz neu.' },
        voice_cast: expect.objectContaining({ model_id: 'eleven_v3', stability: 1 }),
      }),
    )
    expect(sent.voice_cast.voices).toEqual([
      { speaker: 'Host', voice_id: 'v1', voice_name: 'Jonas' },
      { speaker: 'Gast', voice_id: 'v2', voice_name: 'Mara' },
    ])
    expect(wrapper.find('audio').attributes('src')).toBe('/api/audio/takes/take1/mix')
    expect(wrapper.find('[data-download]').attributes('href')).toBe('/api/audio/takes/take1/mix?download=1')
    expect(wrapper.text()).toContain('1 bearbeitet')
    expect(wrapper.text()).toContain('Ganz neu.')
    wrapper.unmount()
  })

  it('keeps the original script recoverable across a reload of the draft', async () => {
    const wrapper = mountView()
    await flushPromises()
    await load(wrapper)
    await wrapper.find('[data-segment="s1"] [data-edit-start]').trigger('click')
    await wrapper.find('[data-segment="s1"] textarea').setValue('Anders.')
    wrapper.unmount()

    const again = mountView()
    await flushPromises()
    expect(mocks.loadSource).toHaveBeenCalledWith('audio_generation', 'r1', 't1')
    expect(again.find('[data-segment="s1"]').text()).toContain('Anders.')
    await again.find('[data-restore-all]').trigger('click')
    expect(again.find('[data-segment="s1"]').text()).toContain('Exact  words')
    again.unmount()
  })

  it('shows a failed take with its error and offers to resume', async () => {
    mocks.listRuns.mockResolvedValue([
      { id: 'e1', created_at: '2026-10-06', status: 'completed', total_cost_usd: 0, setup: { artifact_hash: 'hash', voice_cast: { model_id: 'eleven_v4', stability: 0.5, voices: [] } }, source: loaded.source, output: { run_id: 'r1', take_id: 'take1' } },
    ])
    mocks.takes.mockResolvedValue({
      takes: [{ id: 'take1', status: 'failed', error: 'Too few credits', total_cost_usd: 0.01, plan: [{ status: 'done' }, { status: 'failed' }], chunks: [{ index: 0, url: '/api/audio/takes/take1/chunks/0' }], mix: null, resumable: true, audio_script: null }],
    })
    mocks.resume.mockResolvedValue({})
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toContain('Too few credits')
    expect(wrapper.find('audio').attributes('src')).toBe('/api/audio/takes/take1/chunks/0')
    await wrapper.findAll('button').find((b) => b.text().startsWith('Fortsetzen'))!.trigger('click')
    await flushPromises()
    expect(mocks.resume).toHaveBeenCalledWith('take1')
    wrapper.unmount()
  })

  it('explains a missing setup and never generates', async () => {
    mocks.status.mockResolvedValue({ configured: false, message: 'Missing provider key', usd_per_1k_characters: {} })
    const wrapper = mountView()
    await flushPromises()
    await load(wrapper)
    expect(wrapper.text()).toContain('Missing provider key')
    expect(mocks.voices).not.toHaveBeenCalled()
    expect(wrapper.find('[data-generate]').attributes('disabled')).toBeDefined()
    expect(mocks.startRun).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})


const completedRun = {
  id: 'e1', created_at: '2026-10-06', status: 'completed', total_cost_usd: 0,
  setup: { artifact_hash: 'hash', voice_cast: { model_id: 'eleven_v4', stability: 0.5, voices: [] } },
  source: loaded.source, output: { run_id: 'r1', take_id: 'take1' },
}

function take(status: string) {
  return {
    id: 'take1', status, error: null, total_cost_usd: 0,
    plan: [], chunks: [], resumable: status === 'failed', audio_script: null,
    mix: status === 'completed' ? {
      url: '/finished.mp3', download_url: '/finished.mp3?download=1', duration_s: 3,
      lines: [], chunk_offsets_s: [],
    } : null,
  }
}

async function nextPoll() {
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
}

it.each(['runs', 'takes'])('retries a failed initial %s refresh until audio is available', async (failure) => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  mocks.listRuns.mockResolvedValue([completedRun])
  mocks.takes.mockResolvedValue({ takes: [take('completed')] })
  if (failure === 'runs') mocks.listRuns.mockRejectedValueOnce(new Error('Unavailable'))
  else mocks.takes.mockRejectedValueOnce(new Error('Unavailable'))
  const wrapper = mountView()
  await flushPromises()
  expect(wrapper.find('[data-run]').exists()).toBe(false)
  await nextPoll()
  expect(wrapper.find('audio').attributes('src')).toBe('/finished.mp3')
  const calls = mocks.listRuns.mock.calls.length
  await nextPoll()
  expect(mocks.listRuns).toHaveBeenCalledTimes(calls)
  wrapper.unmount()
})

it('keeps the queued run until its take is fetched after generation', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const wrapper = mountView()
  await flushPromises()
  await load(wrapper)
  await tab(wrapper, 'voices')
  const selects = wrapper.findAll('select')
  await selects[0]!.setValue('v1')
  await selects[1]!.setValue('v2')
  mocks.listRuns.mockResolvedValue([{ ...completedRun, id: 'experiment1' }])
  mocks.takes.mockRejectedValueOnce(new Error('Unavailable'))
  mocks.takes.mockResolvedValue({ takes: [take('completed')] })
  await wrapper.find('[data-generate]').trigger('click')
  await flushPromises()
  expect(wrapper.findComponent({ name: 'TakeResult' }).props('run').status).toBe('queued')
  expect(wrapper.find('audio').exists()).toBe(false)
  await nextPoll()
  expect(wrapper.find('audio').attributes('src')).toBe('/finished.mp3')
  wrapper.unmount()
})

it.each(['resume', 'stop'] as const)('retries failed refreshes after %s and stops polling on unmount', async (action) => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  mocks.listRuns.mockResolvedValue([completedRun])
  mocks.takes.mockResolvedValueOnce({ takes: [take(action === 'resume' ? 'failed' : 'running')] })
  mocks.takes.mockRejectedValueOnce(new Error('Unavailable'))
  mocks.takes.mockRejectedValueOnce(new Error('Still unavailable'))
  mocks.takes.mockResolvedValue({ takes: [take('running')] })
  mocks[action].mockResolvedValue({})
  const wrapper = mountView()
  await flushPromises()
  const result = wrapper.findComponent({ name: 'TakeResult' })
  result.vm.$emit(action, take(action === 'resume' ? 'failed' : 'running'))
  await flushPromises()
  expect(mocks[action]).toHaveBeenCalledWith('take1')
  await nextPoll()
  expect(mocks.takes).toHaveBeenCalledTimes(3)
  await nextPoll()
  expect(result.props('take').status).toBe('running')
  wrapper.unmount()
  await nextPoll()
  expect(mocks.takes).toHaveBeenCalledTimes(4)
})

it.each([['resolve', false], ['reject', false], ['resolve', true], ['reject', true]] as const)(
  'ignores an older poll after a newer generation refresh (%s, newer failed: %s)', async (outcome, newerFailed) => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  mocks.listRuns.mockResolvedValue([completedRun])
  mocks.takes.mockResolvedValue({ takes: [take('running')] })
  const wrapper = mountView()
  await flushPromises()
  await tab(wrapper, 'voices')
  const selects = wrapper.findAll('select')
  await selects[0]!.setValue('v1')
  await selects[1]!.setValue('v2')

  let resolve!: (value: { takes: ReturnType<typeof take>[] }) => void
  let reject!: (reason: Error) => void
  mocks.takes.mockReturnValueOnce(new Promise((yes, no) => { resolve = yes; reject = no }))
  await nextPoll()
  const newerRun = { ...completedRun, id: 'experiment1', output: { run_id: 'r1', take_id: 'take2' } }
  mocks.listRuns.mockResolvedValue([newerRun, completedRun])
  if (newerFailed) mocks.takes.mockRejectedValueOnce(new Error('Latest refresh unavailable'))
  mocks.takes.mockResolvedValue({ takes: [{ ...take('running'), id: 'take2' }, take('completed')] })
  await wrapper.find('[data-generate]').trigger('click')
  await flushPromises()
  expect(wrapper.find('[data-run="experiment1"]').exists()).toBe(true)

  if (outcome === 'resolve') resolve({ takes: [take('completed')] })
  else reject(new Error('Stale poll failed'))
  await flushPromises()
  expect(wrapper.find('[data-run="experiment1"]').exists()).toBe(true)
  if (newerFailed) expect(wrapper.find('[role="alert"]').text()).toContain('Latest refresh unavailable')
  else expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  const calls = mocks.listRuns.mock.calls.length
  await nextPoll()
  expect(mocks.listRuns).toHaveBeenCalledTimes(calls + 1)
  expect(wrapper.find('[data-run="experiment1"]').exists()).toBe(true)
  wrapper.unmount()
})

it.each([['resolve', 'queued'], ['reject', 'queued'], ['resolve', 'completed'], ['reject', 'completed']] as const)(
  'ignores an older poll (%s) after inserting a %s run whose wait fails', async (outcome, status) => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    mocks.listRuns.mockResolvedValue([completedRun])
    mocks.takes.mockResolvedValue({ takes: [take('running')] })
    const wrapper = mountView()
    await flushPromises()
    await tab(wrapper, 'voices')
    const selects = wrapper.findAll('select')
    await selects[0]!.setValue('v1')
    await selects[1]!.setValue('v2')

    let resolve!: (value: { takes: ReturnType<typeof take>[] }) => void
    let reject!: (reason: Error) => void
    mocks.takes.mockReturnValueOnce(new Promise((yes, no) => { resolve = yes; reject = no }))
    await nextPoll()
    mocks.startRun.mockResolvedValue({
      id: 'experiment1', status, created_at: '2026-10-06', total_cost_usd: 0,
      setup: {}, source: null, output: null,
    })
    mocks.waitForRun.mockRejectedValueOnce(new Error('Wait temporarily unavailable'))
    await wrapper.find('[data-generate]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-run="experiment1"]').exists()).toBe(true)

    if (outcome === 'resolve') resolve({ takes: [take('completed')] })
    else reject(new Error('Stale poll failed'))
    await flushPromises()
    expect(wrapper.find('[data-run="experiment1"]').exists()).toBe(true)
    expect(wrapper.find('[role="alert"]').text()).toContain('Wait temporarily unavailable')

    mocks.listRuns.mockResolvedValue([
      { ...completedRun, id: 'experiment1', output: { run_id: 'r1', take_id: 'take2' } },
      completedRun,
    ])
    mocks.takes.mockResolvedValue({ takes: [{ ...take('completed'), id: 'take2' }, take('completed')] })
    const calls = mocks.listRuns.mock.calls.length
    await nextPoll()
    expect(mocks.listRuns).toHaveBeenCalledTimes(calls + 1)
    expect(wrapper.find('[data-run="experiment1"] audio').attributes('src')).toBe('/finished.mp3')
    await nextPoll()
    expect(mocks.listRuns).toHaveBeenCalledTimes(calls + 1)
    wrapper.unmount()
  },
)
