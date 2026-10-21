/**
 * 统一的 HTTP 客户端。
 *
 * 三条规则：
 * 1. 只用原生 fetch。axios 对流式响应的支持要绕一圈，这个项目有 SSE，不引入它。
 * 2. token 只从 localStorage 读一处，避免多个地方各存一份导致不一致。
 * 3. 401 统一处理：清 token 并跳登录，不让每个页面各写一遍。
 */

const BASE = import.meta.env.VITE_API_BASE ?? '/api'
const TOKEN_KEY = 'pim.token'

export class ApiError extends Error {
  status: number
  code: string
  detail?: unknown

  constructor(status: number, code: string, message: string, detail?: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.detail = detail
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export function authHeaders(): Record<string, string> {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    setToken(null)
    onUnauthorized?.()
    throw new ApiError(401, 'unauthenticated', '登录已过期，请重新登录')
  }

  if (res.status === 204) return undefined as T

  const text = await res.text()
  const body = text ? safeJson(text) : null

  if (!res.ok) {
    // 后端的错误结构是 {code, message, detail}；detail 里可能藏着 duplicateOf
    const code = body?.code ?? body?.detail?.code ?? 'request_failed'
    const message = body?.message ?? body?.detail?.message ?? `请求失败（${res.status}）`
    throw new ApiError(res.status, code, message, body?.detail ?? body)
  }
  return body as T
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...authHeaders(),
      ...(init.headers ?? {}),
    },
  })
  return handleResponse<T>(res)
}

/** multipart 上传。不要手动设 Content-Type：浏览器要自己带 boundary。 */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    body: form,
    headers: { ...authHeaders() },
  })
  return handleResponse<T>(res)
}

function safeJson(text: string): any {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

export const http = {
  get: <T>(path: string) => api<T>(path),
  post: <T>(path: string, body?: unknown) =>
    api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    api<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  del: <T>(path: string) => api<T>(path, { method: 'DELETE' }),
  upload: <T>(path: string, form: FormData) => apiUpload<T>(path, form),
}
