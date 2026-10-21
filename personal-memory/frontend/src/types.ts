export interface User {
  id: string
  email: string
  displayName?: string | null
  createdAt: string
}

export interface Span {
  quote: string
  start: number
  end: number
}

export interface Analysis {
  summary: string
  keyPoints: Span[]
  todosExtracted: Span[]
  confidence: number
  model: string
  version: number
  createdAt: string
}

export interface ItemLink {
  itemId: string
  title?: string | null
  relation: string
  score: number
  reason?: string | null
}

export interface Item {
  id: string
  kind: string
  title?: string | null
  bodyMd: string
  rawInput: string
  sourceChannel: string
  sourceUrl?: string | null
  tags: string[]
  capturedAt: string
  updatedAt: string
  analysisStatus: 'pending' | 'running' | 'done' | 'failed' | 'skipped'
  analysisVersion: number
  userPinned: boolean
  // 文件类记录才有值
  fileName?: string | null
  mimeType?: string | null
  fileSize?: number | null
  fileExt?: string | null
  parseError?: string | null
  collectionId?: string | null
  analysis?: Analysis | null
  links: ItemLink[]
}

/** 资料库：一级文件夹 */
export interface Collection {
  id: string
  name: string
  fileCount: number
  createdAt: string
}

/** 资料库列表里的文件（轻量投影，正文只有 excerpt；全文走 /items/{id}） */
export interface LibraryFile {
  id: string
  title?: string | null
  fileName?: string | null
  mimeType?: string | null
  fileSize?: number | null
  fileExt?: string | null
  parseError?: string | null
  analysisStatus: Item['analysisStatus']
  analysisVersion: number
  collectionId?: string | null
  capturedAt: string
  updatedAt: string
  excerpt: string
}

export interface LibraryPage {
  items: LibraryFile[]
  total: number
  limit: number
  offset: number
}

export interface Paged<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface Citation {
  n: number
  chunkId?: string
  itemId?: string
  title?: string | null
  quote?: string | null
  verified?: boolean
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  streaming?: boolean
  stage?: string
  tokens?: { in: number; out: number }
}

export interface Todo {
  id: string
  text: string
  status: 'open' | 'done' | 'dropped'
  dueDate?: string | null
  priority: number
  sourceItemId?: string | null
  createdAt: string
  doneAt?: string | null
}

export interface TodoGroup {
  bucket: string
  label: string
  todos: Todo[]
}

export interface ActionItem {
  text: string
  reason: string
  evidenceItemIds: string[]
}

export interface Digest {
  id: string
  kind: string
  periodStart: string
  periodEnd: string
  contentMd: string
  stats: Record<string, any>
  actionItems: ActionItem[]
  stale: boolean
  model?: string | null
  createdAt: string
}

export interface UsageRow {
  day: string
  path: string
  model: string
  tokensIn: number
  tokensOut: number
  cachedTokens: number
  calls: number
}

export interface UsageSummary {
  month: string
  totalIn: number
  totalOut: number
  totalCached: number
  quota: number
  byPath: UsageRow[]
}
