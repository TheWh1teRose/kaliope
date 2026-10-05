import { api, ApiError } from '@/api/client'

export type LineReaction = 'impressed' | 'dislike' | 'horrible'

export interface SharedCitation {
  rects: { page: number; bbox: [number, number, number, number] }[]
}
export interface SharedPage {
  page: number
  width: number
  height: number
  url: string
}
export interface SharedSegment {
  ordinal?: number
  speaker: string
  text: string
  citations?: SharedCitation[]
}
export interface SharedEpisode {
  index: number
  title: string
  segments: SharedSegment[]
  audio: { url: string; duration_s: number } | null
  pages?: SharedPage[]
}
export interface FeedbackMark {
  episode: number
  ordinal: number
  reaction: LineReaction
  slop: boolean
  comment: string | null
}
export interface FeedbackState {
  label: string | null
  stars: number | null
  worked: string | null
  did_not: string | null
  marks: FeedbackMark[]
}
export interface FeedbackSummary {
  responses: {
    index: number
    label: string | null
    stars: number | null
    worked: string | null
    did_not: string | null
    impressed: number
    dislike: number
    horrible: number
    slop: number
  }[]
  impressed: number
  dislike: number
  horrible: number
  slop: number
  lines: {
    response: number
    label: string | null
    key: string
    speaker: string
    text: string
    reaction: LineReaction
    slop: boolean
    comment: string | null
  }[]
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
  feedback?: FeedbackSummary
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
  feedback: (token: string) => publicJson<FeedbackState>(token, '/feedback'),
  saveMark: (
    token: string,
    body: {
      episode: number
      ordinal: number
      reaction: LineReaction | null
      slop: boolean
      comment: string | null
    },
  ) =>
    publicJson<FeedbackState>(token, '/feedback/marks', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  saveSheet: (
    token: string,
    body: {
      label: string | null
      stars: number | null
      worked: string | null
      did_not: string | null
    },
  ) =>
    publicJson<FeedbackState>(token, '/feedback/sheet', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
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

const REVIEWER_KEY = 'kalliope.reviewer-key'
const REVIEWER_LABEL = 'kalliope.reviewer-label'

export function reviewerKey(): string {
  const existing = localStorage.getItem(REVIEWER_KEY)
  if (existing && /^[0-9a-f]{64}$/.test(existing)) return existing
  const bytes = new Uint8Array(32)
  crypto.getRandomValues(bytes)
  const key = [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('')
  localStorage.setItem(REVIEWER_KEY, key)
  return key
}

export function rememberedLabel(): string {
  return localStorage.getItem(REVIEWER_LABEL) ?? ''
}

export function rememberLabel(label: string): void {
  const trimmed = label.trim()
  if (trimmed) localStorage.setItem(REVIEWER_LABEL, trimmed.slice(0, 40))
  else localStorage.removeItem(REVIEWER_LABEL)
}

async function publicJson<T>(
  token: string,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('X-Reviewer-Key', reviewerKey())
  if (init.body) headers.set('Content-Type', 'application/json')
  const response = await fetch(
    `/api/public/review-links/${encodeURIComponent(token)}${path}`,
    {
      ...init,
      headers,
      credentials: 'omit',
      cache: 'no-store',
    },
  )
  if (!response.ok) throw new ApiError(response.status, '', '')
  const data: unknown = await response.json()
  if (!isFeedback(data)) throw new ApiError(response.status, '', '')
  return data as T
}

function isFeedback(data: unknown): data is FeedbackState {
  return Boolean(
    data &&
      typeof data === 'object' &&
      Array.isArray((data as FeedbackState).marks),
  )
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
