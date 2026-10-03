import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { audioApi } from '@/api/audio'
import type { AudioTakeOut, RunAudioOut } from '@/api/types'

import AudioPanel from './AudioPanel.vue'

vi.mock('@/api/audio', () => ({
  audioApi: {
    status: vi.fn(async () => ({
      configured: false,
      message: null,
      flows: [{ id: 'elevenlabs_dialog_v0', version: '0.2', description: null }],
      models: ['eleven_v4', 'eleven_v3'],
      usd_per_1k_characters: { eleven_v4: 0.08 },
    })),
    voices: vi.fn(async () => {
      throw new Error('no list')
    }),
    formatVoices: vi.fn(),
    saveFormatVoices: vi.fn(async () => ({})),
    takes: vi.fn(),
    start: vi.fn(async () => ({})),
    approve: vi.fn(async () => ({})),
    stop: vi.fn(async () => ({
      id: 'take-1',
      status: 'stopped',
      outcome: 'stopped',
      stopped_by: null,
    })),
  },
  takeActive: (status: string) => status === 'queued' || status === 'running',
}))

const SETUP = 'ElevenLabs ist nicht eingerichtet: ELEVENLABS_API_KEY fehlt.'

const script = {
  model: 'claude-sonnet-5-5',
  tag_language: 'en',
  lines: [
    {
      segment_id: 's1',
      speaker: 'Moderator',
      kind: null,
      text: 'Hallo?',
      spoken: 'Hallo?',
      tagged: '[curious] Hallo?',
      spoken_forms: [],
      tags: ['[curious]'],
      guard: 'pass',
      problem: null,
    },
    {
      segment_id: 's2',
      speaker: 'Expertin',
      kind: 'claim',
      text: 'Guten Tag.',
      spoken: 'Guten Tag.',
      tagged: 'Guten Tag.',
      spoken_forms: [],
      tags: [],
      guard: 'pass',
      problem: null,
    },
    {
      segment_id: 's3',
      speaker: 'Moderator',
      kind: null,
      text: 'Nicht in der Probe.',
      spoken: 'Nicht in der Probe.',
      tagged: 'Nicht in der Probe.',
      spoken_forms: [],
      tags: [],
      guard: 'pass',
      problem: null,
    },
  ],
}

function take(overrides: Partial<AudioTakeOut>): AudioTakeOut {
  return {
    id: 'take-1',
    run_id: 'run-1',
    flow_id: 'elevenlabs_dialog_v0',
    flow_version: '0.2',
    status: 'paused',
    scope: 'sample',
    created_at: '2026-10-03T12:00:00Z',
    finished_at: null,
    error: null,
    total_cost_usd: 0.01,
    voice_cast: { voices: [], model_id: 'eleven_v4', stability: 0.5, language_code: 'de' },
    speakers: ['Moderator', 'Expertin'],
    approval: {
      lines: 2,
      characters: 1040,
      requests: 1,
      estimate_usd: 0.0832,
      model_id: 'eleven_v4',
      missing_voices: ['Expertin', 'Moderator'],
    },
    audio_script: script,
    audio: null,
    chunks: [],
    ...overrides,
  }
}

function answer(configured: boolean, takes: AudioTakeOut[]): RunAudioOut {
  return { configured, message: configured ? null : SETUP, takes }
}

async function mountPanel() {
  const wrapper = mount(AudioPanel, { props: { runId: 'run-1', formatId: 'two_host_dialogue' } })
  await flushPromises()
  await flushPromises()
  return wrapper
}

describe('AudioPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('says how to set up ElevenLabs and still lets the sample be prepared', async () => {
    vi.mocked(audioApi.takes).mockResolvedValue(answer(false, []))
    const wrapper = await mountPanel()
    expect(wrapper.find('.notice--warn').text()).toContain('ELEVENLABS_API_KEY')

    await wrapper.find('.btn--mark').trigger('click')
    await flushPromises()
    expect(audioApi.start).toHaveBeenCalledWith('run-1')
  })

  it('shows the price and the sample lines and approves only with every voice set', async () => {
    vi.mocked(audioApi.takes).mockResolvedValue(answer(true, [take({})]))
    const wrapper = await mountPanel()

    expect(wrapper.text()).toContain('≈ $0.08')
    expect(wrapper.text()).toContain('1.040')
    expect(wrapper.findAll('.script-seg')).toHaveLength(2)
    const approve = wrapper.findAll('button').find((b) => b.text().startsWith('Freigeben'))!
    expect(approve.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Ohne Stimme: Moderator, Expertin')

    const inputs = wrapper.findAll('input.input')
    await inputs[0].setValue('v-mod')
    await inputs[1].setValue('v-exp')
    expect(approve.attributes('disabled')).toBeUndefined()
    await approve.trigger('click')
    await flushPromises()

    const cast = expect.objectContaining({
      model_id: 'eleven_v4',
      voices: expect.arrayContaining([
        expect.objectContaining({ speaker: 'Moderator', voice_id: 'v-mod' }),
        expect.objectContaining({ speaker: 'Expertin', voice_id: 'v-exp' }),
      ]),
    })
    expect(audioApi.saveFormatVoices).toHaveBeenCalledWith('two_host_dialogue', cast)
    expect(audioApi.approve).toHaveBeenCalledWith('take-1', cast)
  })

  it('cannot approve without a key', async () => {
    vi.mocked(audioApi.takes).mockResolvedValue(
      answer(false, [
        take({
          voice_cast: {
            voices: [
              { speaker: 'Moderator', voice_id: 'a' },
              { speaker: 'Expertin', voice_id: 'b' },
            ],
            model_id: 'eleven_v4',
            stability: 0.5,
          },
        }),
      ]),
    )
    const wrapper = await mountPanel()
    const approve = wrapper.findAll('button').find((b) => b.text().startsWith('Freigeben'))!
    expect(approve.attributes('disabled')).toBeDefined()
    expect(wrapper.find('.notice--warn').text()).toContain('ELEVENLABS_API_KEY')
  })

  it('plays the finished sample chunk by chunk', async () => {
    vi.mocked(audioApi.takes).mockResolvedValue(
      answer(true, [
        take({
          status: 'completed',
          approval: null,
          chunks: [
            {
              index: 0,
              url: '/api/audio/takes/take-1/chunks/0',
              characters: 1040,
              duration_s: 58.4,
              cost_usd: 0.0832,
              segment_ids: ['s1', 's2'],
            },
          ],
        }),
      ]),
    )
    const wrapper = await mountPanel()
    const audio = wrapper.find('audio')
    expect(audio.attributes('src')).toBe('/api/audio/takes/take-1/chunks/0')
    expect(wrapper.text()).toContain('58.4 s · 1.040 Zeichen · $0.08')
    expect(wrapper.findAll('.script-seg')).toHaveLength(2)
  })

  it('asks in the page before stopping a take that is waiting for approval', async () => {
    vi.mocked(audioApi.takes)
      .mockResolvedValueOnce(answer(true, [take({})]))
      .mockResolvedValueOnce(answer(true, [take({ status: 'stopped', error: null, approval: null })]))
    const wrapper = mount(AudioPanel, {
      props: { runId: 'run-1', formatId: 'two_host_dialogue' },
      attachTo: document.body,
    })
    await flushPromises()
    await flushPromises()
    await wrapper.get('[data-stop]').trigger('click')
    expect(audioApi.stop).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('Audio stoppen?')
    document.body.querySelector<HTMLButtonElement>('[data-action="confirm-stop"]')?.click()
    await flushPromises()
    expect(audioApi.stop).toHaveBeenCalledWith('take-1')
    expect(wrapper.get('[data-stopped]').text()).toContain('Gestoppt')
    expect(wrapper.find('.notice--fail').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows a stopped take without treating it as a failure', async () => {
    vi.mocked(audioApi.takes).mockResolvedValue(
      answer(true, [take({ status: 'stopped', error: null, approval: null })]),
    )
    const wrapper = await mountPanel()
    expect(wrapper.get('[data-stopped] .badge').text()).toBe('Gestoppt')
    expect(wrapper.get('[data-stopped] .badge').classes()).toContain('badge--idle')
    expect(wrapper.find('.notice--fail').exists()).toBe(false)
    expect(wrapper.find('[data-stop]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Neue Probe')
  })
})
