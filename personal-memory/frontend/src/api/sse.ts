/**
 * SSE 流式读取。
 *
 * ⚠️ 这里最容易踩的坑：**不能用 EventSource**。
 * EventSource 有三个硬限制：只能 GET、不能带自定义 header、不能带请求体。
 * 而问答接口需要 POST（带 query）+ Authorization（带 JWT），三条全中。
 *
 * 所有"用 EventSource 做 AI 流式输出"的教程在这个场景都会失败，
 * 而且失败方式很迷惑：要么 405，要么一直 pending。
 *
 * 正确做法：fetch + ReadableStream 手动解析 SSE 帧。
 */

import { authHeaders } from './client'

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

export interface SseEvent {
  event: string
  data: any
}

export interface AskPayload {
  query: string
  sessionId?: string | null
  clientTurnId: string
  attachmentIds?: string[]
}

export async function* streamAsk(
  payload: AskPayload,
  signal: AbortSignal,
): AsyncGenerator<SseEvent, void, unknown> {
  const res = await fetch(`${BASE}/ask`, {
    method: 'POST', // EventSource 做不到这条
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(payload),
    signal,
  })

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '')
    throw new Error(`问答请求失败：${res.status} ${text.slice(0, 200)}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // SSE 用空行分隔事件；一次 read 可能拿到半个事件，所以要留残余
      let idx: number
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const raw = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        const parsed = parseFrame(raw)
        if (parsed) yield parsed
      }
    }
  } finally {
    reader.releaseLock()
  }
}

function parseFrame(raw: string): SseEvent | null {
  let event = 'message'
  const dataLines: string[] = []

  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
    // 忽略注释行（以 : 开头）和 id/retry
  }
  if (dataLines.length === 0) return null

  const data = dataLines.join('\n')
  try {
    return { event, data: JSON.parse(data) }
  } catch {
    return { event, data }
  }
}
