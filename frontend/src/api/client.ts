import { t } from '@/i18n'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly title: string,
    readonly detail: string,
  ) {
    super(detail || title)
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...init,
    headers: {
      ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(init.headers ?? {}),
    },
  })

  if (response.status === 204) return undefined as T

  const isJson = (response.headers.get('content-type') ?? '').includes('json')
  const payload = isJson ? await response.json() : await response.text()

  if (!response.ok) throw problem(response.status, payload)
  return payload as T
}

function problem(status: number, payload: unknown): ApiError {
  const body = (typeof payload === 'string' ? { title: payload } : payload) as {
    title?: string
    detail?: string
  } | null
  return new ApiError(
    status,
    body?.title ?? t.errors.generic,
    body?.detail ?? body?.title ?? t.errors.generic,
  )
}

/** A file the server builds on request, with the name it suggests for saving it. */
async function download(path: string): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(path, { credentials: 'same-origin' })
  if (!response.ok) {
    const isJson = (response.headers.get('content-type') ?? '').includes('json')
    throw problem(response.status, isJson ? await response.json() : await response.text())
  }
  const disposition = response.headers.get('content-disposition') ?? ''
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? path.split('/').pop() ?? 'download'
  return { blob: await response.blob(), filename }
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'PATCH',
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  upload: <T>(path: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<T>(path, { method: 'POST', body: form })
  },
  download,
}
