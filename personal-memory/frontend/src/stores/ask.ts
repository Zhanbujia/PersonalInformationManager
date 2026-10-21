import { defineStore } from 'pinia'
import { http } from '@/api/client'
import { streamAsk } from '@/api/sse'
import type { ChatAttachment, ChatMessage, ChatSession, Citation } from '@/types'

let seq = 0
const uid = () => `${Date.now()}-${seq++}`

interface AskState {
  sessions: ChatSession[]
  messages: ChatMessage[]
  sessionId: string | null
  streaming: boolean
  error: string | null
  controller: AbortController | null
  /** 待随下一条消息发送的附件 */
  pending: ChatAttachment[]
  uploading: boolean
}

export const useAskStore = defineStore('ask', {
  state: (): AskState => ({
    sessions: [],
    messages: [],
    sessionId: null,
    streaming: false,
    error: null,
    controller: null,
    pending: [],
    uploading: false,
  }),

  actions: {
    async loadSessions() {
      const r = await http.get<{ sessions: ChatSession[] }>('/sessions')
      this.sessions = r.sessions
    },

    newSession() {
      if (this.streaming) this.stop()
      this.messages = []
      this.sessionId = null
      this.error = null
      this.pending = []
    },

    async openSession(id: string) {
      if (this.streaming) this.stop()
      this.error = null
      this.pending = []
      const r = await http.get<{ messages: ChatMessage[] }>(`/sessions/${id}/messages`)
      this.sessionId = id
      this.messages = r.messages.map((m) => ({
        ...m,
        citations: m.citations ?? [],
        attachments: m.attachments ?? [],
      }))
    },

    async deleteSession(id: string) {
      await http.del(`/sessions/${id}`)
      if (this.sessionId === id) this.newSession()
      await this.loadSessions()
    },

    async renameSession(id: string, title: string) {
      await http.patch(`/sessions/${id}`, { title })
      await this.loadSessions()
    },

    async uploadAttachment(file: File): Promise<ChatAttachment> {
      this.uploading = true
      try {
        const form = new FormData()
        form.append('file', file)
        const a = await http.upload<ChatAttachment>('/ask/attachments', form)
        this.pending.push(a)
        return a
      } finally {
        this.uploading = false
      }
    },

    removePending(id: string) {
      this.pending = this.pending.filter((a) => a.id !== id)
    },

    async send(query: string) {
      const text = query.trim()
      if (this.streaming) return
      if (!text && !this.pending.length) return

      this.error = null
      const atts = [...this.pending]
      this.messages.push({
        id: uid(),
        role: 'user',
        content: text,
        citations: [],
        attachments: atts,
      })
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
          {
            query: text,
            sessionId: this.sessionId,
            clientTurnId: uid(),
            attachmentIds: atts.map((a) => a.id),
          },
          this.controller.signal,
        )) {
          if (ev.event === 'meta') {
            const sid = ev.data.sessionId as string | undefined
            if (sid && sid !== this.sessionId) {
              this.sessionId = sid
              this.loadSessions().catch(() => void 0)
            }
            // 服务端已受理，附件从待发送区移除
            if (atts.length) this.pending = []
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
        this.loadSessions().catch(() => void 0)
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
      this.newSession()
    },
  },
})
