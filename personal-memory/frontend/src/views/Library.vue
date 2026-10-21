<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useLibraryStore } from '@/stores/library'
import type { Collection, LibraryFile } from '@/types'

const store = useLibraryStore()

const selected = ref<LibraryFile | null>(null)
const previewText = ref('')
const previewLoading = ref(false)

const fileInput = ref<HTMLInputElement | null>(null)
const dragOver = ref(false)
const accept =
  '.pdf,.docx,.xlsx,.pptx,.txt,.md,.markdown,.csv,.tsv,.json,.log,.yaml,.yml,.html,.htm,.xml'

const extOptions = [
  { value: '', label: '全部类型' },
  { value: '.pdf', label: 'PDF' },
  { value: '.docx', label: 'Word' },
  { value: '.xlsx', label: 'Excel' },
  { value: '.pptx', label: 'PPT' },
  { value: '.md', label: 'Markdown' },
  { value: '.txt', label: '文本' },
]

const currentName = computed(() => {
  const f = store.collectionFilter
  if (!f) return '全部文件'
  if (f === 'uncategorized') return '未分类'
  return store.collections.find((c) => c.id === f)?.name || '文件夹'
})

let timer: number | undefined

onMounted(() => {
  store.refresh().catch((e) => ElMessage.error((e as Error).message))
  // 只轮询还在解析中的文件，避免整页反复刷新
  timer = window.setInterval(() => {
    if (store.files.some((f) => f.analysisStatus === 'pending' || f.analysisStatus === 'running')) {
      store.fetchFiles(true).catch(() => void 0)
    }
  }, 4000)
})

onUnmounted(() => window.clearInterval(timer))

let debounce: number | undefined
watch(
  () => store.q,
  () => {
    window.clearTimeout(debounce)
    debounce = window.setTimeout(() => store.fetchFiles(true).catch(() => void 0), 320)
  },
)
watch(() => store.ext, () => store.fetchFiles(true).catch(() => void 0))

function selectFilter(f: string | null) {
  store.collectionFilter = f
  store.fetchFiles(true).catch((e) => ElMessage.error((e as Error).message))
}

function pickFiles() {
  if (!store.uploading) fileInput.value?.click()
}

async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (files.length) await doUpload(files)
}

function onDrop(e: DragEvent) {
  dragOver.value = false
  const files = Array.from(e.dataTransfer?.files || [])
  if (files.length) doUpload(files)
}

async function doUpload(files: File[]) {
  try {
    const { ok, dup } = await store.uploadFiles(files, store.collectionFilter)
    if (ok) ElMessage.success(`已上传 ${ok} 个文件，正在后台解析`)
    if (dup) ElMessage.warning(`${dup} 个文件之前传过，已跳过`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function newFolder() {
  try {
    const { value } = await ElMessageBox.prompt('给文件夹起个名字', '新建文件夹', {
      inputPlaceholder: '如：合同 / 论文 / 会议纪要',
      confirmButtonText: '创建',
      cancelButtonText: '取消',
    })
    const name = (value || '').trim()
    if (!name) return
    await store.createCollection(name)
    ElMessage.success('已创建')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error((e as Error).message)
  }
}

async function renameFolder(c: Collection) {
  try {
    const { value } = await ElMessageBox.prompt('新的文件夹名', '重命名文件夹', {
      inputValue: c.name,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    })
    const name = (value || '').trim()
    if (!name || name === c.name) return
    await store.renameCollection(c.id, name)
    ElMessage.success('已重命名')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error((e as Error).message)
  }
}

async function removeFolder(c: Collection) {
  try {
    await ElMessageBox.confirm(
      `删除文件夹「${c.name}」？里面的文件会移到「未分类」，不会被删除。`,
      '删除文件夹',
      { confirmButtonText: '删除', cancelButtonText: '取消' },
    )
    await store.deleteCollection(c.id)
    ElMessage.success('已删除')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error((e as Error).message)
  }
}

async function openFile(f: LibraryFile) {
  selected.value = f
  previewText.value = ''
  previewLoading.value = true
  try {
    const full = await store.getFile(f.id)
    previewText.value = full.bodyMd || ''
  } catch {
    previewText.value = ''
  } finally {
    previewLoading.value = false
  }
}

async function renameFile(f: LibraryFile) {
  try {
    const { value } = await ElMessageBox.prompt('新的文件名', '重命名', {
      inputValue: f.title || f.fileName || '',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    })
    const title = (value || '').trim()
    if (!title || title === f.title) return
    await store.renameFile(f.id, title)
    if (selected.value?.id === f.id) selected.value = { ...selected.value, title }
    ElMessage.success('已重命名')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error((e as Error).message)
  }
}

async function onMoveChange(e: Event) {
  if (!selected.value) return
  const value = (e.target as HTMLSelectElement).value
  try {
    await store.moveFile(selected.value.id, value || null)
    selected.value = { ...selected.value, collectionId: value || null }
    ElMessage.success('已移动')
  } catch (err) {
    ElMessage.error((err as Error).message)
  }
}

async function removeFile(f: LibraryFile) {
  try {
    await ElMessageBox.confirm(`删除「${f.title || f.fileName}」？`, '删除文件', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await store.removeFile(f.id)
    if (selected.value?.id === f.id) selected.value = null
    ElMessage.success('已删除')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error((e as Error).message)
  }
}

async function download(f: LibraryFile) {
  try {
    await store.download(f.id, f.fileName || f.title || f.id)
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

function formatSize(n?: number | null) {
  if (!n) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function formatDate(s: string) {
  return new Date(s).toLocaleString('zh-CN', {
    hour12: false,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function extLabel(f: LibraryFile) {
  return (f.fileExt || '').replace('.', '').toUpperCase().slice(0, 4) || 'FILE'
}

function typeClass(f: LibraryFile) {
  const e = (f.fileExt || '').toLowerCase()
  if (e === '.pdf') return 'pdf'
  if (e === '.docx') return 'doc'
  if (e === '.xlsx') return 'xls'
  if (e === '.pptx') return 'ppt'
  return 'txt'
}

function statusText(f: LibraryFile) {
  if (f.analysisStatus === 'pending') return '排队中'
  if (f.analysisStatus === 'running') return '解析中'
  if (f.analysisStatus === 'failed') return '解析失败'
  return ''
}
</script>

<template>
  <div class="library" :class="{ 'with-preview': !!selected }">
    <!-- 左侧：目录 -->
    <aside class="rail card">
      <button class="btn primary new-folder" @click="newFolder">＋ 新建文件夹</button>

      <button class="rail-item" :class="{ on: !store.collectionFilter }" @click="selectFilter(null)">
        <span class="rail-name">全部文件</span>
        <span class="rail-count">{{ store.total }}</span>
      </button>
      <button
        class="rail-item"
        :class="{ on: store.collectionFilter === 'uncategorized' }"
        @click="selectFilter('uncategorized')"
      >
        <span class="rail-name">未分类</span>
        <span class="rail-count">{{ store.uncategorized }}</span>
      </button>

      <div class="rail-title">文件夹</div>
      <div
        v-for="c in store.collections"
        :key="c.id"
        class="rail-item-wrap"
        :class="{ on: store.collectionFilter === c.id }"
      >
        <button class="rail-item" @click="selectFilter(c.id)">
          <span class="rail-name">{{ c.name }}</span>
          <span class="rail-count">{{ c.fileCount }}</span>
        </button>
        <span class="rail-actions">
          <button class="mini" @click.stop="renameFolder(c)">改名</button>
          <button class="mini danger" @click.stop="removeFolder(c)">删除</button>
        </span>
      </div>
      <p v-if="!store.collections.length" class="rail-empty small muted">还没有文件夹</p>
    </aside>

    <!-- 中间：文件区 -->
    <section class="content">
      <div class="page-head">
        <div>
          <h1 class="page-title">{{ currentName }}</h1>
          <p class="page-sub">共 {{ store.fileTotal }} 个文件。上传后后台会自动解析成可检索的文本。</p>
        </div>
        <div class="head-actions">
          <button class="btn" :class="{ active: store.view === 'grid' }" @click="store.view = 'grid'">
            网格
          </button>
          <button class="btn" :class="{ active: store.view === 'list' }" @click="store.view = 'list'">
            列表
          </button>
          <button class="btn primary" :disabled="store.uploading" @click="pickFiles">
            {{ store.uploading ? '上传中…' : '＋ 上传文件' }}
          </button>
          <input ref="fileInput" type="file" multiple :accept="accept" hidden @change="onFileChange" />
        </div>
      </div>

      <div class="toolbar">
        <input v-model="store.q" type="search" placeholder="搜索文件名或内容…" />
        <select v-model="store.ext">
          <option v-for="o in extOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
      </div>

      <div
        class="dropzone"
        :class="{ over: dragOver }"
        @dragover.prevent="dragOver = true"
        @dragleave.prevent="dragOver = false"
        @drop.prevent="onDrop"
      >
        把文件拖到这里上传（也可点右上角「上传文件」）
      </div>

      <div v-if="!store.loading && !store.files.length" class="empty card">
        <p>这个位置还没有文件。</p>
        <button class="btn primary" @click="pickFiles">上传第一份文件</button>
      </div>

      <div v-else-if="store.view === 'grid'" class="grid">
        <button
          v-for="f in store.files"
          :key="f.id"
          class="file-card"
          :class="{ sel: selected?.id === f.id }"
          @click="openFile(f)"
        >
          <span class="file-badge" :class="typeClass(f)">{{ extLabel(f) }}</span>
          <span class="file-name">{{ f.title || f.fileName || '未命名' }}</span>
          <span class="file-meta">{{ formatSize(f.fileSize) }} · {{ formatDate(f.capturedAt) }}</span>
          <span v-if="statusText(f)" class="file-status" :class="{ bad: f.analysisStatus === 'failed' }">
            {{ statusText(f) }}
          </span>
        </button>
      </div>

      <div v-else class="file-list">
        <div
          v-for="f in store.files"
          :key="f.id"
          class="file-row"
          :class="{ sel: selected?.id === f.id }"
          @click="openFile(f)"
        >
          <span class="file-badge small" :class="typeClass(f)">{{ extLabel(f) }}</span>
          <span class="file-name">{{ f.title || f.fileName || '未命名' }}</span>
          <span class="file-meta">{{ formatSize(f.fileSize) }}</span>
          <span class="file-meta">{{ formatDate(f.capturedAt) }}</span>
          <span v-if="statusText(f)" class="file-status" :class="{ bad: f.analysisStatus === 'failed' }">
            {{ statusText(f) }}
          </span>
        </div>
      </div>

      <div v-if="store.files.length < store.fileTotal" class="more">
        <button class="btn" @click="store.fetchFiles(false)">加载更多</button>
      </div>
      <div v-if="store.loading" class="small muted loading">加载中…</div>
    </section>

    <!-- 右侧：预览 -->
    <aside v-if="selected" class="preview card">
      <div class="preview-head">
        <span class="file-badge" :class="typeClass(selected)">{{ extLabel(selected) }}</span>
        <div class="preview-title">
          <div class="name">{{ selected.title || selected.fileName }}</div>
          <div class="small muted">{{ selected.fileName }}</div>
        </div>
        <button class="linkish" @click="selected = null">关闭</button>
      </div>

      <div class="preview-meta small muted">
        {{ formatSize(selected.fileSize) }} · {{ formatDate(selected.capturedAt) }}
        <span v-if="statusText(selected)"> · {{ statusText(selected) }}</span>
      </div>

      <div class="preview-actions">
        <button class="btn" @click="download(selected)">下载</button>
        <button class="btn" @click="renameFile(selected)">重命名</button>
        <select :value="selected.collectionId || ''" @change="onMoveChange">
          <option value="">未分类</option>
          <option v-for="c in store.collections" :key="c.id" :value="c.id">{{ c.name }}</option>
        </select>
        <button class="btn danger" @click="removeFile(selected)">删除</button>
      </div>

      <div v-if="selected.parseError" class="parse-error">{{ selected.parseError }}</div>

      <div class="preview-body">
        <div v-if="previewLoading" class="small muted">加载正文…</div>
        <pre v-else>{{ previewText || '（没有可预览的文本）' }}</pre>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.library {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.library.with-preview {
  grid-template-columns: 200px minmax(0, 1fr) 340px;
}

/* ---------- 左侧目录 ---------- */
.rail {
  padding: 10px;
  position: sticky;
  top: 20px;
}
.new-folder {
  width: 100%;
  padding: 6px;
  font-size: 12.5px;
  margin-bottom: 8px;
}
.rail-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: none;
  background: none;
  padding: 7px 9px;
  border-radius: 7px;
  cursor: pointer;
  color: var(--text);
  font-size: 13px;
  text-align: left;
}
.rail-item:hover {
  background: var(--hover);
}
.rail-item.on,
.rail-item-wrap.on .rail-item {
  background: var(--accent-soft);
  color: var(--accent);
}
.rail-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rail-count {
  color: var(--muted);
  font-size: 11.5px;
  flex-shrink: 0;
}
.rail-title {
  font-size: 11px;
  color: var(--muted);
  margin: 12px 9px 4px;
}
.rail-empty {
  margin: 6px 9px;
}
.rail-item-wrap {
  position: relative;
}
.rail-actions {
  display: none;
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  gap: 2px;
  background: var(--surface);
}
.rail-item-wrap:hover .rail-actions {
  display: flex;
}
.rail-item-wrap:hover .rail-count {
  visibility: hidden;
}
.mini {
  border: none;
  background: none;
  font-size: 11px;
  color: var(--muted);
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
}
.mini:hover {
  background: var(--hover);
  color: var(--accent);
}
.mini.danger:hover {
  color: var(--danger);
}

/* ---------- 中间 ---------- */
.content {
  min-width: 0;
}
.head-actions {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-shrink: 0;
}
.head-actions .btn {
  padding: 6px 10px;
  font-size: 12.5px;
}
.head-actions .btn.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}
.toolbar input {
  flex: 1;
  min-width: 160px;
}
.toolbar select,
.preview-actions select {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  font-family: inherit;
}
.dropzone {
  border: 1px dashed var(--line);
  border-radius: var(--radius);
  padding: 10px;
  text-align: center;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 14px;
  transition: all 0.12s;
}
.dropzone.over {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}
.empty {
  text-align: center;
  padding: 44px 20px;
}
.empty p {
  color: var(--muted);
  margin: 0 0 14px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(176px, 1fr));
  gap: 10px;
}
.file-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  text-align: left;
  padding: 12px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  cursor: pointer;
  transition: all 0.12s;
  font-family: inherit;
  color: var(--text);
}
.file-card:hover {
  border-color: var(--accent);
}
.file-card.sel {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-soft);
}
.file-name {
  font-size: 13px;
  font-weight: 500;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
}
.file-meta {
  font-size: 11.5px;
  color: var(--muted);
}
.file-status {
  font-size: 11.5px;
  color: var(--warn);
}
.file-status.bad {
  color: var(--danger);
}

.file-list {
  border-top: 1px solid var(--line);
}
.file-row {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr) 76px 120px 68px;
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
  border-bottom: 1px solid var(--line);
  cursor: pointer;
  font-size: 13px;
}
.file-row:hover {
  background: var(--hover);
}
.file-row.sel {
  background: var(--accent-soft);
}

/* 文件类型色块（不用图标库，纯 CSS） */
.file-badge {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  font-size: 10px;
  font-weight: 600;
  color: #fff;
  background: #5b6470;
  flex-shrink: 0;
}
.file-badge.small {
  width: 26px;
  height: 26px;
  font-size: 9px;
  border-radius: 6px;
}
.file-badge.pdf {
  background: #b4453a;
}
.file-badge.doc {
  background: #2f5fa8;
}
.file-badge.xls {
  background: #2f7d4f;
}
.file-badge.ppt {
  background: #b06a25;
}
.file-badge.txt {
  background: #5b6470;
}

.more {
  margin-top: 14px;
  text-align: center;
}
.loading {
  padding: 14px 0;
}

/* ---------- 右侧预览 ---------- */
.preview {
  position: sticky;
  top: 20px;
  max-height: calc(100vh - 40px);
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: hidden;
}
.preview-head {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.preview-title {
  flex: 1;
  min-width: 0;
}
.preview-title .name {
  font-weight: 500;
  font-size: 13.5px;
  word-break: break-all;
}
.linkish {
  border: none;
  background: none;
  color: var(--muted);
  cursor: pointer;
  font-size: 12px;
  padding: 2px;
}
.linkish:hover {
  color: var(--accent);
}
.preview-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}
.preview-actions .btn {
  padding: 5px 10px;
  font-size: 12px;
}
.parse-error {
  background: var(--warn-soft);
  color: var(--warn);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.6;
}
.preview-body {
  flex: 1;
  overflow: auto;
  border-top: 1px dashed var(--line);
  padding-top: 8px;
  min-height: 120px;
}
.preview-body pre {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.75;
  margin: 0;
  color: #33383f;
}

@media (max-width: 1080px) {
  .library,
  .library.with-preview {
    grid-template-columns: 180px minmax(0, 1fr);
  }
  .preview {
    grid-column: 1 / -1;
    position: static;
    max-height: none;
  }
}
@media (max-width: 720px) {
  .library,
  .library.with-preview {
    grid-template-columns: 1fr;
  }
  .rail {
    position: static;
  }
  .file-row {
    grid-template-columns: 30px minmax(0, 1fr) 70px;
  }
  .file-row .file-meta:nth-child(3),
  .file-row .file-meta:nth-child(4) {
    display: none;
  }
}
</style>
