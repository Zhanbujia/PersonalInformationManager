import { defineStore } from 'pinia'
import { streamAsk } from '@/api/sse'
import type { ChatMessage, Citation } from '@/types'

let seq = 0
const uid = () => `${Date.now()}-${seq++}`

interface AskState {
  messages: ChatMessage[]
  sessionId: string | null
  streaming: boolean
  error: string | null
  controller: AbortController | null
}

export const useAskStore = defineStore('ask', {
  state: (): AskState => ({
    messages: [],
    sessionId: null,
    streaming: false,
    error: null,
    controller: null,
  }),

  actions: {
    async send(query: string) {
      const text = query.trim()
      if (!text || this.streaming) return

      this.error = null
      this.messages.push({ id: uid(), role: 'user', content: text, citations: [] })
      const answer: ChatMessage = {
        id: uid(),
        role: 'assistant',
        content: '',
        citations: [],
        streaming: true,
        stage: 'retrieving',
      }
      this.messages.push(answer)

      this.streaming = true
      this.controller = new AbortController()

      try {
        for await (const ev of streamAsk(
          { query: text, sessionId: this.sessionId, clientTurnId: uid() },
          this.controller.signal,
        )) {
          if (ev.event === 'meta') {
            this.sessionId = ev.data.sessionId ?? this.sessionId
          } else if (ev.event === 'status') {
            answer.stage = ev.data.stage
          } else if (ev.event === 'token') {
            answer.stage = 'answering'
            answer.content += ev.data.t ?? ''
          } else if (ev.event === 'citation') {
            answer.citations.push(ev.data as Citation)
          } else if (ev.event === 'done') {
            if (Array.isArray(ev.data.citations)) {
              // done 里是权威名单，覆盖中间累积的（防止重复）
              answer.citations = ev.data.citations as Citation[]
            }
            answer.tokens = ev.data.tokens
          } else if (ev.event === 'error') {
            this.error = ev.data.message ?? '回答失败，请重试'
          }
        }
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          this.error = (err as Error).message
        }
      } finally {
        answer.streaming = false
        answer.stage = undefined
        this.streaming = false
        this.controller = null
      }
    },

    stop() {
      this.controller?.abort()
    },

    reset() {
      this.messages = []
      this.sessionId = null
      this.error = null
    },
  },
})
