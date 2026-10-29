import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'

import VoiceCasting from './VoiceCasting.vue'

const mocks = vi.hoisted(() => ({ sample: vi.fn() }))
vi.mock('@/api/audio', () => ({ audioApi: mocks }))

const voices = [
  { voice_id: 'v1', name: 'Jonas', preview_url: 'https://cdn.test/jonas.mp3', category: 'professional', description: 'warm', language: 'de' },
  { voice_id: 'v2', name: 'Mara', preview_url: 'https://cdn.test/mara.mp3', category: null, description: null, language: 'de' },
  { voice_id: 'v3', name: 'Lena', preview_url: null, category: 'cloned', description: null, language: 'en' },
]
const cast = { voices: [{ speaker: 'Host', voice_id: 'v1' }, { speaker: 'Gast', voice_id: '' }], model_id: 'eleven_v4', stability: 0.5 }

let play: ReturnType<typeof vi.spyOn>
let sources: string[]

function mountCasting() {
  return mount(VoiceCasting, {
    props: { speakers: ['Host', 'Gast'], needed: ['Host'], voices, cast, rate: 0.08, sampleCharacters: 60 },
  })
}

function button(wrapper: ReturnType<typeof mountCasting>, id: string) {
  return wrapper.find(`[data-voice="${id}"] [data-play]`)
}

beforeEach(() => {
  sources = []
  play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
    sources.push(this.src)
    return Promise.resolve()
  })
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})
afterEach(() => {
  vi.restoreAllMocks()
  mocks.sample.mockReset()
})

it('plays nothing until asked, and only one sample at a time', async () => {
  const wrapper = mountCasting()
  await flushPromises()
  expect(play).not.toHaveBeenCalled()
  expect(wrapper.findAll('[data-voice]')).toHaveLength(3)

  await button(wrapper, 'v1').trigger('click')
  await flushPromises()
  expect(sources).toEqual(['https://cdn.test/jonas.mp3'])
  expect(button(wrapper, 'v1').attributes('aria-pressed')).toBe('true')
  expect(button(wrapper, 'v1').attributes('aria-label')).toBe('Probe von Jonas anhalten')

  await button(wrapper, 'v2').trigger('click')
  await flushPromises()
  expect(sources).toEqual(['https://cdn.test/jonas.mp3', 'https://cdn.test/mara.mp3'])
  expect(button(wrapper, 'v1').attributes('aria-pressed')).toBe('false')
  expect(button(wrapper, 'v2').attributes('aria-pressed')).toBe('true')
  expect(wrapper.findAll('[aria-pressed="true"][data-play]')).toHaveLength(1)

  await button(wrapper, 'v2').trigger('click')
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled()
  expect(wrapper.findAll('[aria-pressed="true"][data-play]')).toHaveLength(0)
  wrapper.unmount()
})

it('makes a sample once for a voice without a preview, and shows when that fails', async () => {
  mocks.sample.mockResolvedValueOnce({ url: '/api/audio/voices/v3/sample?model_id=eleven_v4', characters: 60, cost_usd: 0.005 })
  const wrapper = mountCasting()
  expect(wrapper.find('[data-voice="v3"]').text()).toContain('Probe erzeugen · ≈ $0.005')
  await button(wrapper, 'v3').trigger('click')
  await flushPromises()
  expect(mocks.sample).toHaveBeenCalledWith('v3', 'eleven_v4')
  expect(sources).toEqual(['http://localhost:3000/api/audio/voices/v3/sample?model_id=eleven_v4'])
  await button(wrapper, 'v3').trigger('click')
  await button(wrapper, 'v3').trigger('click')
  await flushPromises()
  expect(mocks.sample).toHaveBeenCalledTimes(1)
  wrapper.unmount()

  mocks.sample.mockRejectedValueOnce(new ApiError(502, 'Sample failed', 'Too few credits'))
  const failing = mountCasting()
  await button(failing, 'v3').trigger('click')
  await flushPromises()
  expect(failing.find('[data-voice="v3"]').text()).toContain('Probe konnte nicht abgespielt werden.')
  expect(failing.emitted('error')).toHaveLength(1)
  failing.unmount()
})

it('assigns voices to speakers and marks a needed speaker without one', async () => {
  const wrapper = mountCasting()
  const rows = wrapper.findAll('[data-speaker]')
  expect(rows[0]!.find('select').classes()).not.toContain('select--missing')
  expect(rows[1]!.text()).toContain('nicht in der Auswahl')
  const chips = wrapper.find('[data-voice="v2"]').findAll('.chip')
  await chips[1]!.trigger('click')
  expect(wrapper.emitted('assign')).toEqual([['Gast', 'v2']])
  await wrapper.find('[data-voice="v1"]').findAll('.chip')[0]!.trigger('click')
  expect(wrapper.emitted('assign')![1]).toEqual(['Host', ''])

  await wrapper.find('input[type="search"]').setValue('cloned en')
  expect(wrapper.findAll('[data-voice]').map((row) => row.attributes('data-voice'))).toEqual(['v3'])
  wrapper.unmount()
})
