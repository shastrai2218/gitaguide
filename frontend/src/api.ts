/** Thin API client. Uses the Vite dev proxy unless VITE_API_BASE_URL is set. */

import type { ChatResponse, HealthResponse } from './types'

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      /* non-JSON error body; keep the status text */
    }
    throw new ApiError(detail, response.status)
  }
  return (await response.json()) as T
}

/** POST /chat - one turn of the pipeline. */
export function sendMessage(message: string, sessionId: string | null): Promise<ChatResponse> {
  return request<ChatResponse>('/chat', {
    method: 'POST',
    body: JSON.stringify({ message, session_id: sessionId }),
  })
}

/** GET /explain/{id} - the stored trace for an earlier answer. */
export function fetchExplanation(messageId: string): Promise<Record<string, unknown>> {
  return request(`/explain/${encodeURIComponent(messageId)}`)
}

/** GET /health - which backends are actually live. */
export function fetchHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health')
}
