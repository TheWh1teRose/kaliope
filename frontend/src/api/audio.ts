/** Audio takes and voices: the calls the run view's audio panel makes. */
import { api } from '@/api/client'
import type {
  AudioStatus,
  AudioTakeOut,
  FormatVoices,
  RunAudioOut,
  SpeechVoice,
  StopOut,
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
  start: (runId: string, flowId?: string) =>
    api.post<AudioTakeOut>(`/api/runs/${runId}/audio`, flowId ? { flow_id: flowId } : {}),
  approve: (takeId: string, cast: VoiceCast) =>
    api.post<AudioTakeOut>(`/api/audio/takes/${takeId}/approve`, { voice_cast: cast }),
  stop: (takeId: string) => api.post<StopOut>(`/api/audio/takes/${takeId}/stop`),
}

/** A take is still moving while it is queued or running. */
export function takeActive(status: string): boolean {
  return status === 'queued' || status === 'running'
}
