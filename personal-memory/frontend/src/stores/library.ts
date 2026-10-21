import { defineStore } from 'pinia'
import { http, ApiError, downloadFile } from '@/api/client'
import type { Collection, Item, LibraryFile, LibraryPage } from '@/types'

interface LibraryState {
  collections: Collection[]
  uncategorized: number
  total: number
  files: LibraryFile[]
  fileTotal: number
  loading: boolean
  uploading: boolean
  /** null=全部文件；'uncategorized'=未分类；其它=文件夹 id */
  collectionFilter: string | null
  q: string
  ext: string
  view: 'grid' | 'list'
}

export const useLibraryStore = defineStore('library', {
  state: (): LibraryState => ({
    collections: [],
    uncategorized: 0,
    total: 0,
    files: [],
    fileTotal: 0,
    loading: false,
    uploading: false,
    collectionFilter: null,
    q: '',
    ext: '',
    view: 'grid',
  }),

  actions: {
    async fetchCollections() {
      const r = await http.get<{
        collections: Collection[]
        uncategorized: number
        total: number
      }>('/library/collections')
      this.collections = r.collections
      this.uncategorized = r.uncategorized
      this.total = r.total
    },

    async fetchFiles(reset = true) {
      this.loading = true
      try {
        const p = new URLSearchParams()
        if (this.collectionFilter === 'uncategorized') p.set('uncategorized', 'true')
        else if (this.collectionFilter) p.set('collectionId', this.collectionFilter)
        if (this.q.trim()) p.set('q', this.q.trim())
        if (this.ext) p.set('ext', this.ext)
        p.set('limit', '60')
        if (!reset) p.set('offset', String(this.files.length))
        const page = await http.get<LibraryPage>(`/library/files?${p.toString()}`)
        this.files = reset ? page.items : [...this.files, ...page.items]
        this.fileTotal = page.total
      } finally {
        this.loading = false
      }
    },

    async refresh() {
      await Promise.all([this.fetchCollections(), this.fetchFiles(true)])
    },

    async getFile(id: string): Promise<Item> {
      return http.get<Item>(`/items/${id}`)
    },

    async createCollection(name: string) {
      const c = await http.post<Collection>('/library/collections', { name })
      await this.fetchCollections()
      return c
    },

    async renameCollection(id: string, name: string) {
      await http.patch(`/library/collections/${id}`, { name })
      await this.fetchCollections()
    },

    async deleteCollection(id: string) {
      await http.del(`/library/collections/${id}`)
      if (this.collectionFilter === id) this.collectionFilter = null
      await this.refresh()
    },

    async moveFile(id: string, collectionId: string | null) {
      await http.patch(`/library/files/${id}`, { collectionId })
      await this.refresh()
    },

    async renameFile(id: string, title: string) {
      await http.patch(`/items/${id}`, { title })
      await this.fetchFiles(true)
    },

    /** 顺序上传多个文件；重复文件跳过并计数。`collectionId` 为 null/未分类时不带。 */
    async uploadFiles(files: File[], collectionId: string | null) {
      this.uploading = true
      let ok = 0
      let dup = 0
      try {
        for (const f of files) {
          const form = new FormData()
          form.append('file', f)
          if (collectionId && collectionId !== 'uncategorized') {
            form.append('collectionId', collectionId)
          }
          try {
            await http.upload<{ id: string }>('/items/upload', form)
            ok++
          } catch (e) {
            if (e instanceof ApiError && e.code === 'duplicate_content') {
              dup++
              continue
            }
            throw e
          }
        }
      } finally {
        this.uploading = false
      }
      await this.refresh()
      return { ok, dup }
    },

    async removeFile(id: string) {
      await http.del(`/items/${id}`)
      await this.refresh()
    },

    async download(id: string, filename: string) {
      await downloadFile(`/items/${id}/file`, filename)
    },
  },
})
