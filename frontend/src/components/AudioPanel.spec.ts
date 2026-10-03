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
    resume: vi.fn(async () => ({})),
    approve: vi.fn(async () => ({})),
  },
  takeActive: (status: string) => status === 'queued' || status === 'running',
  lineAt: (lines: { segment_id: string; start_s: number; end_s: number }[], time: number) =>
    lines.find((line) => time >= line.start_s && time < line.end_s)?.segment_id ?? null,
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
      cached_requests: 0,
      cached_characters: 0,
    },
    audio_script: script,
    audio: null,
    chunks: [],
    plan: [],
    mix: null,
    resumable: false,
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
    expect(audioApi.start).toHaveBeenCalledWith('run-1', 'sample')
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

  it('offers the whole episode as well as the sample', async () => {
    vi.mocked(audioApi.takes).mockResolvedValue(answer(true, []))
    const wrapper = await mountPanel()
    await wrapper.findAll('button').find((b) => b.text() === 'Ganze Folge vorbereiten')!.trigger('click')
    await flushPromises()
    expect(audioApi.start).toHaveBeenCalledWith('run-1', 'full')
  })

  it('lists every chunk with its state and resumes a failed take', async () => {
    vi.mocked(audioApi.takes).mockResolvedValue(
      answer(true, [
        take({
          status: 'failed',
          scope: 'full',
          approval: null,
          error: 'ElevenLabs reports too few credits',
          resumable: true,
          plan: [
            { index: 0, characters: 1700, segment_ids: ['s1'], status: 'done' },
            { index: 1, characters: 900, segment_ids: ['s2'], status: 'failed' },
            { index: 2, characters: 1200, segment_ids: ['s3'], status: 'waiting' },
          ],
        }),
      ]),
    )
    const wrapper = await mountPanel()
    const chunks = wrapper.findAll('.chunks li')
    expect(chunks.map((chunk) => chunk.find('.badge').text())).toEqual([
      'fertig',
      'fehlgeschlagen',
      'wartet',
    ])
    expect(wrapper.text()).toContain('too few credits')
    await wrapper.findAll('button').find((b) => b.text() === 'Fortsetzen')!.trigger('click')
    await flushPromises()
    expect(audioApi.resume).toHaveBeenCalledWith('take-1')
  })

  it('plays the whole take as one file, jumps to a line and highlights the one heard', async () => {
    vi.mocked(audioApi.takes).mockResolvedValue(
      answer(true, [
        take({
          status: 'completed',
          scope: 'full',
          approval: null,
          total_cost_usd: 1.18,
          mix: {
            url: '/api/audio/takes/take-1/mix',
            download_url: '/api/audio/takes/take-1/mix?download=1',
            duration_s: 125.4,
            chunk_offsets_s: [0, 61.2],
            lines: [
              { segment_id: 's1', start_s: 0, end_s: 3.1 },
              { segment_id: 's2', start_s: 3.3, end_s: 61 },
              { segment_id: 's3', start_s: 61.2, end_s: 125 },
            ],
          },
        }),
      ]),
    )
    const wrapper = await mountPanel()
    const audio = wrapper.find('audio.full')
    expect(audio.attributes('src')).toBe('/api/audio/takes/take-1/mix')
    expect(wrapper.find('a[download]').attributes('href')).toBe(
      '/api/audio/takes/take-1/mix?download=1',
    )
    expect(wrapper.text()).toContain('2:05 · 3 Zeilen · $1.18')
    const lines = wrapper.findAll('.lines li')
    expect(lines.map((line) => line.find('.line__time').text())).toEqual(['0:00', '0:03', '1:01'])
    expect(lines[0].find('.line__tag').text()).toBe('[curious]')

    await lines[2].find('button').trigger('click')
    const element = audio.element as HTMLAudioElement
    expect(element.currentTime).toBe(61.2)
    expect(wrapper.find('.lines li.current').text()).toContain('Nicht in der Probe.')

    element.currentTime = 10
    await audio.trigger('timeupdate')
    expect(wrapper.find('.lines li.current').text()).toContain('Guten Tag.')
  })
})
