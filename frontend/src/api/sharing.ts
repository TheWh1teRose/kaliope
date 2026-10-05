import { api, ApiError } from '@/api/client'

export interface SharedEpisode {
  index: number
  title: string
  segments: { speaker: string; text: string }[]
  audio: { url: string; duration_s: number } | null
}
export interface SharedSnapshot {
  title: string
  created_at: string
  expires_at: string
  episodes: SharedEpisode[]
}
export interface ShareChoice {
  index: number
  title: string
  take_id: string | null
}
export interface ShareSelection {
  title: string
  episodes: ShareChoice[]
  acknowledge_missing_audio: boolean
}
export interface ShareOptions {
  title: string
  episodes: {
    index: number
    title: string
    takes: {
      id: string
      created_at: string
      duration_s: number
      older_script: boolean
    }[]
  }[]
}
export interface LinkMetadata {
  id: string
  created_at: string
  expires_at: string
  status: 'active' | 'expired' | 'revoked'
}
export interface OwnerLinks {
  configured: boolean
  links: LinkMetadata[]
}
export interface SharePreview {
  snapshot: SharedSnapshot
  key: string
}
export const sharingApi = {
  options: (kind: string, id: string) =>
    api.get<ShareOptions>(
      `/api/${kind}/${encodeURIComponent(id)}/review-link/options`,
    ),
  links: (kind: string, id: string) =>
    api.get<OwnerLinks>(`/api/${kind}/${encodeURIComponent(id)}/review-link`),
  preview: (kind: string, id: string, choice: ShareSelection) =>
    api.post<SharePreview>(
      `/api/${kind}/${encodeURIComponent(id)}/review-link/preview`,
      choice,
    ),
  create: (
    kind: string,
    id: string,
    choice: ShareSelection,
    key: string,
    replaceId: string | null,
  ) =>
    api.post<{ link: LinkMetadata; url: string }>(
      `/api/${kind}/${encodeURIComponent(id)}/review-link`,
      {
        ...choice,
        preview_key: key,
        replace_link_id: replaceId,
      },
    ),
  revoke: (id: string) =>
    api.delete(`/api/review-links/${encodeURIComponent(id)}`),
  async read(token: string): Promise<SharedSnapshot> {
    const response = await fetch(
      `/api/public/review-links/${encodeURIComponent(token)}`,
      {
        credentials: 'omit',
        cache: 'no-store',
      },
    )
    if (!response.ok) throw new ApiError(response.status, '', '')
    return response.json() as Promise<SharedSnapshot>
  },
}

export function sharedDate(value: string): string {
  return new Date(value).toLocaleString('de-DE', {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}
export function sharedDuration(value: number): string {
  return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`
}
