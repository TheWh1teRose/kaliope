/** Audio takes and voices: the calls the run view's audio panel makes. */
import { api } from '@/api/client'
import type {
  AudioStatus,
  AudioTakeOut,
  FormatVoices,
  LineTime,
  RunAudioOut,
  SeriesAudioOut,
  SpeechVoice,
  VoiceCast,
} from '@/api/types'

export const audioApi = {
  status: () => api.get<AudioStatus>('/api/audio/status'),
  voices: () => api.get<SpeechVoice[]>('/api/audio/voices'),
  formatVoices: (formatId: string) =>
    api.get<FormatVoices>(`/api/formats/${encodeURIComponent(formatId)}/voices`),
  saveFormatVoices: (formatId: string, cast: VoiceCast) =>
    api.put<FormatVoices>(`/api/formats/${encodeURIComponent(formatId)}/voices`, cast),
  takes: (runId: string) => api.get<RunAudioOut>(`/api/runs/${runId}/audio`),
  start: (runId: string, scope: AudioScope = 'sample') =>
    api.post<AudioTakeOut>(`/api/runs/${runId}/audio`, { scope }),
  approve: (takeId: string, cast: VoiceCast) =>
    api.post<AudioTakeOut>(`/api/audio/takes/${takeId}/approve`, { voice_cast: cast }),
  resume: (takeId: string) => api.post<AudioTakeOut>(`/api/audio/takes/${takeId}/resume`),
  series: (seriesId: string) => api.get<SeriesAudioOut>(`/api/series/${seriesId}/audio`),
  startSeries: (seriesId: string, scope: AudioScope = 'full') =>
    api.post<SeriesAudioOut>(`/api/series/${seriesId}/audio`, { scope }),
  approveSeries: (seriesId: string) =>
    api.post<SeriesAudioOut>(`/api/series/${seriesId}/audio/approve`),
}

export type AudioScope = 'sample' | 'full'

/** The line being heard at ``time``, or null between lines. */
export function lineAt(lines: LineTime[], time: number): string | null {
  for (const line of lines) {
    if (time >= line.start_s && time < line.end_s) return line.segment_id
  }
  return null
}

/** A take is still moving while it is queued or running. */
export function takeActive(status: string): boolean {
  return status === 'queued' || status === 'running'
}
