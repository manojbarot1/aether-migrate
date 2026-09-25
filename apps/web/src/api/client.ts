/**
 * AETHER MIGRATE API client
 *
 * Wraps fetch with:
 * - Base URL from VITE_API_URL environment variable
 * - Authorization header injection from stored JWT
 * - Structured error handling
 */

const BASE_URL = import.meta.env.VITE_API_URL ?? ''

const TOKEN_KEY = 'aether_access_token'

export function setToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
}

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY)
}

export interface ApiError {
  status: number
  error: string
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.headers as Record<string, string>),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(`${BASE_URL}${path}`, { ...init, headers })

  if (!response.ok) {
    let errorBody: { error?: string } = {}
    try {
      errorBody = await response.json()
    } catch {
      // ignore parse errors
    }
    const err: ApiError = {
      status: response.status,
      error: errorBody.error ?? `HTTP ${response.status}`,
    }
    throw err
  }

  // 204 No Content
  if (response.status === 204) {
    return undefined as unknown as T
  }

  return response.json() as Promise<T>
}

export const apiClient = {
  get: <T>(path: string) => request<T>(path, { method: 'GET' }),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

export default apiClient
