import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import View from './AudioGenerationView.vue'
import VoicePreview from './VoicePreview.vue'

const mocks = vi.hoisted(() => ({
  startRun: vi.fn(), listRuns: vi.fn(), waitForRun: vi.fn(), takes: vi.fn(),
  stop: vi.fn(), voices: vi.fn(), status: vi.fn(), loadSource: vi.fn(),
}))
vi.mock('@/experiments/api', () => ({
  getExperiment: async () => ({ extras: { models: ['eleven_v4', 'eleven_v3'], request_limits: { eleven_v4: 2000, eleven_v3: 2000 } } }),
  startRun: mocks.startRun, listRuns: mocks.listRuns, waitForRun: mocks.waitForRun, loadSource: mocks.loadSource,
}))
vi.mock('@/api/audio', () => ({ audioApi: mocks, takeActive: (s: string) => ['queued', 'running'].includes(s) }))
const loaded = { source: { run_id: 'r1', beat_id: 't1', artifact_hash: 'hash' }, fields: {
  artifact_hash: 'hash', audio_script: JSON.stringify({ lines: [
    { segment_id: 's1', speaker: 'Host', tagged: '[curious] Exact  words [pause]' },
    { segment_id: 's2', speaker: 'Host', tagged: 'x'.repeat(1990) },
  ] }),
}, beats: [] }
const mountView = () => mount(View, { global: { stubs: {
  SourceLoader: { name: 'SourceLoader', template: '<div />' }, RouterLink: true,
} } })
beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  mocks.listRuns.mockResolvedValue([])
  mocks.status.mockResolvedValue({ configured: true })
  mocks.voices.mockResolvedValue([{ voice_id: 'v1', name: 'Voice One', preview_url: 'https://samples.example/voice.mp3' }])
  mocks.startRun.mockResolvedValue({ id: 'experiment1' })
  mocks.waitForRun.mockResolvedValue({ status: 'completed' })
  mocks.loadSource.mockResolvedValue(loaded)
})
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks() })
it('edits exact tags, persists a draft, selects whole utterances and submits only on explicit click', async () => {
  let wrapper = mountView()
  await flushPromises()
  wrapper.findComponent({ name: 'SourceLoader' }).vm.$emit('loaded', loaded)
  await flushPromises()
  expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('[curious] Exact  words [pause]')
  expect(wrapper.findAll('select')[0]!.element.value).toBe('1')
  await wrapper.find('textarea').setValue('[excited] My  edit 🌻 [pause]')
  await wrapper.findAll('select')[1]!.setValue('v1')
  await wrapper.findAll('select')[2]!.setValue('eleven_v3')
  await wrapper.findAll('select')[3]!.setValue('1')
  expect(mocks.startRun).not.toHaveBeenCalled()
  expect(wrapper.find('audio').attributes('src')).toBe('https://samples.example/voice.mp3')
  expect(wrapper.find('audio').attributes('preload')).toBe('none')
  const draft = JSON.parse(localStorage.getItem('kalliope-exp:audio_generation') ?? '{}')
  expect(draft.setup.edited_text[0]).toBe('[excited] My  edit 🌻 [pause]')
  expect(draft.setup.line_count).toBe(1)
  wrapper.unmount()
  wrapper = mountView()
  await flushPromises()
  expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe(draft.setup.edited_text[0])
  await wrapper.find('button.btn--primary').trigger('click')
  await flushPromises()
  expect(mocks.startRun).toHaveBeenCalledWith('audio_generation', draft.setup, loaded.source)
  wrapper.unmount()
})
it('does not silently shrink edited selections or submit over-limit payloads', async () => {
  const wrapper = mountView()
  await flushPromises()
  wrapper.findComponent({ name: 'SourceLoader' }).vm.$emit('loaded', loaded)
  await flushPromises()
  await wrapper.findAll('select')[1]!.setValue('v1')
  await wrapper.find('textarea').setValue('x'.repeat(2001))
  expect(wrapper.text()).toContain('2001 / 2000')
  expect(wrapper.find('button.btn--primary').attributes('disabled')).toBeDefined()
  expect(wrapper.findAll('select')[0]!.element.value).toBe('1')
  await wrapper.find('button.btn--primary').trigger('click')
  expect(mocks.startRun).not.toHaveBeenCalled()
  wrapper.unmount()
})
it('uses provider previews without autoplay and handles missing/loading/error samples', async () => {
  const wrapper = mount(VoicePreview, { props: { voice: { voice_id: 'v1', name: 'One', preview_url: 'https://samples.example/one.mp3', category: null, description: null, language: null } } })
  expect(wrapper.find('audio').attributes('autoplay')).toBeUndefined()
  await wrapper.find('audio').trigger('loadstart')
  expect(wrapper.text()).toContain('wird geladen')
  await wrapper.find('audio').trigger('error')
  expect(wrapper.text()).toContain('konnte nicht geladen')
  await wrapper.setProps({ voice: { voice_id: 'v2', name: 'Two', preview_url: null, category: null, description: null, language: null } })
  expect(wrapper.find('audio').exists()).toBe(false)
  expect(wrapper.text()).toContain('keine Stimmprobe')
  expect(mocks.startRun).not.toHaveBeenCalled()
})
it('shows setup failures without generating', async () => {
  mocks.status.mockResolvedValue({ configured: false, message: 'Missing provider key' })
  const wrapper = mountView()
  await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('Missing provider key')
  expect(mocks.startRun).not.toHaveBeenCalled()
  wrapper.unmount()
})
