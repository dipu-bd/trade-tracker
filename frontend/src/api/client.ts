const BASE = '/api'

let accessToken: string | null = null

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message)
  }
}

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: { code?: string; message?: string } }
    return new ApiError(
      response.status,
      body.error?.code ?? 'error',
      body.error?.message ?? response.statusText,
    )
  } catch {
    return new ApiError(response.status, 'error', response.statusText)
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set('Content-Type', 'application/json')
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)

  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  })

  if (response.status === 204) return undefined as T
  if (!response.ok) throw await parseError(response)
  return (await response.json()) as T
}

export function subscribeToEvents(onEvent: (event: unknown) => void): () => void {
  const source = new EventSource(`${BASE}/events/stream`, { withCredentials: true })
  source.onmessage = (message) => onEvent(JSON.parse(message.data))
  return () => source.close()
}
