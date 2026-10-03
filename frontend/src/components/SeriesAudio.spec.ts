import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { audioApi } from '@/api/audio'
import type { AudioTakeOut, SeriesAudioOut } from '@/api/types'

import SeriesAudio from './SeriesAudio.vue'

vi.mock('@/api/audio', () => ({
  audioApi: {
    series: vi.fn(),
    startSeries: vi.fn(),
    approveSeries: vi.fn(),
  },
  takeActive: (status: string) => status === 'queued' || status === 'running',
}))

function take(overrides: Partial<AudioTakeOut>): AudioTakeOut {
  return {
    id: 'take',
    run_id: 'run',
    flow_id: 'elevenlabs_dialog_v0',
    flow_version: '0.3',
    status: 'paused',
    scope: 'full',
    created_at: null,
    finished_at: null,
    error: null,
    total_cost_usd: 0,
    voice_cast: { voices: [], model_id: 'eleven_v4', stability: 0.5 },
    speakers: ['Moderator', 'Expertin'],
    approval: null,
    audio_script: null,
    audio: null,
    chunks: [],
    plan: [],
    mix: null,
    resumable: false,
    ...overrides,
  }
}

const series: SeriesAudioOut = {
  series_id: 'ser-1',
  configured: true,
  message: null,
  format_id: 'two_host_dialogue',
  waiting: 1,
  estimate_usd: 1.16,
  episodes: [
    {
      index: 1,
      name: 'Klima Teil 1',
      run_id: 'r1',
      run_status: 'completed',
      take: take({
        id: 't1',
        status: 'completed',
        mix: {
          url: '/api/audio/takes/t1/mix',
          download_url: '/api/audio/takes/t1/mix?download=1',
          duration_s: 905,
          chunk_offsets_s: [0],
          lines: [],
        },
      }),
    },
    {
      index: 2,
      name: null,
      run_id: 'r2',
      run_status: 'completed',
      take: take({
        id: 't2',
        approval: {
          lines: 80,
          characters: 14500,
          requests: 9,
          estimate_usd: 1.16,
          model_id: 'eleven_v4',
          missing_voices: [],
          cached_requests: 0,
          cached_characters: 0,
        },
      }),
    },
    { index: 3, name: null, run_id: 'r3', run_status: 'running', take: null },
  ],
}

async function mountSheet() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/runs/:id', name: 'run', component: { template: '<div />' } }],
  })
  const wrapper = mount(SeriesAudio, { props: { seriesId: 'ser-1' }, global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

describe('SeriesAudio', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(audioApi.series).mockResolvedValue(series)
    vi.mocked(audioApi.approveSeries).mockResolvedValue({ ...series, waiting: 0 })
    vi.mocked(audioApi.startSeries).mockResolvedValue(series)
  })

  it('shows every episode with its audio and approves the waiting ones together', async () => {
    const wrapper = await mountSheet()
    const episodes = wrapper.findAll('.episodes li')
    expect(episodes).toHaveLength(3)
    expect(episodes[0].text()).toContain('Klima Teil 1')
    expect(episodes[0].find('audio').attributes('src')).toBe('/api/audio/takes/t1/mix')
    expect(episodes[0].text()).toContain('15:05')
    expect(episodes[1].text()).toContain('Folge 2')
    expect(episodes[1].text()).toContain('≈ $1.16')
    expect(episodes[2].text()).toContain('noch kein Audio')

    const approve = wrapper.findAll('button').find((b) => b.text().startsWith('1 Folgen freigeben'))!
    expect(approve.text()).toContain('≈ $1.16')
    await approve.trigger('click')
    await flushPromises()
    expect(audioApi.approveSeries).toHaveBeenCalledWith('ser-1')

    await wrapper.findAll('button').find((b) => b.text() === 'Alle Folgen vorbereiten')!.trigger('click')
    await flushPromises()
    expect(audioApi.startSeries).toHaveBeenCalledWith('ser-1')
  })
})
