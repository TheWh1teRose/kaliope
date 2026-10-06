import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import View from './AudioGenerationView.vue'

const mocks = vi.hoisted(() => ({
  startRun: vi.fn(), listRuns: vi.fn(), waitForRun: vi.fn(), takes: vi.fn(),
  stop: vi.fn(), voices: vi.fn(), status: vi.fn(),
}))
vi.mock('@/experiments/api', () => ({
  getExperiment: async () => ({ extras: { models: ['eleven_v4', 'eleven_v3'] } }),
  startRun: mocks.startRun, listRuns: mocks.listRuns, waitForRun: mocks.waitForRun,
}))
vi.mock('@/api/audio', () => ({ audioApi: mocks, takeActive: (s: string) => ['queued', 'running'].includes(s) }))
beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  mocks.listRuns.mockResolvedValue([])
  mocks.status.mockResolvedValue({ configured: true })
  mocks.voices.mockResolvedValue([{ voice_id: 'v1', name: 'Voice One' }])
  mocks.startRun.mockResolvedValue({ id: 'experiment1' })
  mocks.waitForRun.mockResolvedValue({ status: 'completed' })
})
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks() })
it('imports exact tagged lines and only generates on an explicit click with selected settings', async () => {
  const wrapper = mount(View, { global: { stubs: { SourceLoader: { name: 'SourceLoader', template: '<div />' } } } })
  await flushPromises()
  const loaded = { source: { run_id: 'r1', beat_id: 't1', artifact_hash: 'hash' }, fields: {
    artifact_hash: 'hash', audio_script: JSON.stringify({ lines: [{ segment_id: 's1', speaker: 'Host', tagged: '[curious] Exact  words [pause]' }] }),
  }, beats: [] }
  wrapper.findComponent({ name: 'SourceLoader' }).vm.$emit('loaded', loaded)
  await flushPromises()
  expect(wrapper.text()).toContain('[curious] Exact  words [pause]')
  expect(mocks.startRun).not.toHaveBeenCalled()
  await wrapper.findAll('select')[0]!.setValue('v1')
  await wrapper.findAll('select')[1]!.setValue('eleven_v3')
  await wrapper.findAll('select')[2]!.setValue('1')
  expect(mocks.startRun).not.toHaveBeenCalled()
  expect(JSON.parse(localStorage.getItem('kalliope-exp:audio_generation') ?? '{}')).toEqual(expect.objectContaining({ source: loaded.source, setup: expect.objectContaining({ artifact_hash: 'hash' }) }))
  mocks.listRuns.mockResolvedValue([{ id: 'experiment1', created_at: '2026-10-06', status: 'completed', setup: { artifact_hash: 'hash', voice_cast: { model_id: 'eleven_v3' } }, output: { run_id: 'r1', take_id: 'take1' } }])
  mocks.takes.mockResolvedValue({ takes: [{ id: 'take1', status: 'failed', error: 'Too few credits', total_cost_usd: 0.01, plan: [{ status: 'done' }, { status: 'failed' }], chunks: [{ index: 0, url: '/api/audio/takes/take1/chunks/0' }], mix: null }] })
  await wrapper.find('button.btn--primary').trigger('click')
  await flushPromises()
  expect(mocks.startRun).toHaveBeenCalledWith('audio_generation', expect.objectContaining({ artifact_hash: 'hash', voice_cast: expect.objectContaining({ model_id: 'eleven_v3', stability: 1 }) }), loaded.source)
  expect(wrapper.text()).toContain('Too few credits')
  expect(wrapper.text()).toContain('1/2 Chunks')
  expect(wrapper.find('audio').attributes('src')).toBe('/api/audio/takes/take1/chunks/0')
  wrapper.unmount()
})
it('shows setup failures without generating', async () => {
  mocks.status.mockResolvedValue({ configured: false, message: 'Missing provider key' })
  const wrapper = mount(View, { global: { stubs: { SourceLoader: true } } })
  await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('Missing provider key')
  expect(mocks.startRun).not.toHaveBeenCalled()
  wrapper.unmount()
})
