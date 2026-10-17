import { defineStore } from 'pinia'
import { http, ApiError } from '@/api/client'
import type { Item, Paged } from '@/types'

interface ItemsState {
  list: Item[]
  total: number
  loading: boolean
  filters: { kind?: string; tag?: string; q?: string }
  lastDuplicate: string | null
}

export const useItemsStore = defineStore('items', {
  state: (): ItemsState => ({
    list: [],
    total: 0,
    loading: false,
    filters: {},
    lastDuplicate: null,
  }),

  actions: {
    async fetch(reset = true) {
      this.loading = true
      try {
        const params = new URLSearchParams()
        params.set('limit', '30')
        if (this.filters.kind) params.set('kind', this.filters.kind)
        if (this.filters.tag) params.set('tag', this.filters.tag)
        if (this.filters.q) params.set('q', this.filters.q)
        if (!reset) params.set('offset', String(this.list.length))

        const page = await http.get<Paged<Item>>(`/items?${params.toString()}`)
        this.list = reset ? page.items : [...this.list, ...page.items]
        this.total = page.total
      } finally {
        this.loading = false
      }
    },

    /** 新建记录。重复时把已有 id 放到 lastDuplicate 里，页面据此给"去看原文"的入口。 */
    async create(rawInput: string, kind = 'note') {
      this.lastDuplicate = null
      try {
        const created = await http.post<{ id: string }>('/items', {
          rawInput,
          kind,
          sourceChannel: 'web',
        })
        await this.fetch(true)
        return created
      } catch (err) {
        if (err instanceof ApiError && err.code === 'duplicate_content') {
          const detail = err.detail as { duplicateOf?: string } | undefined
          this.lastDuplicate = detail?.duplicateOf ?? null
          return null
        }
        throw err
      }
    },

    async get(id: string): Promise<Item> {
      return http.get<Item>(`/items/${id}`)
    },

    async patch(id: string, patch: Partial<Pick<Item, 'title' | 'bodyMd' | 'kind' | 'tags'>>) {
      const updated = await http.patch<Item>(`/items/${id}`, patch)
      const idx = this.list.findIndex((i) => i.id === id)
      if (idx >= 0) this.list[idx] = updated
      return updated
    },

    async remove(id: string) {
      await http.del(`/items/${id}`)
      this.list = this.list.filter((i) => i.id !== id)
      this.total = Math.max(0, this.total - 1)
    },

    async reanalyze(id: string) {
      await http.post(`/items/${id}/reanalyze`)
      const idx = this.list.findIndex((i) => i.id === id)
      if (idx >= 0) this.list[idx].analysisStatus = 'pending'
    },

    /** 只轮询"还在分析中"的那几条，避免整页刷新造成闪烁。 */
    async pollPending() {
      const pending = this.list.filter(
        (i) => i.analysisStatus === 'pending' || i.analysisStatus === 'running',
      )
      if (pending.length === 0) return
      await Promise.all(
        pending.map(async (i) => {
          try {
            const s = await http.get<{ analysisStatus: Item['analysisStatus'] }>(
              `/items/${i.id}/status`,
            )
            if (s.analysisStatus !== i.analysisStatus) {
              const full = await this.get(i.id)
              const idx = this.list.findIndex((x) => x.id === i.id)
              if (idx >= 0) this.list[idx] = full
            }
          } catch {
            /* 单条失败不影响其它条 */
          }
        }),
      )
    },
  },
})
