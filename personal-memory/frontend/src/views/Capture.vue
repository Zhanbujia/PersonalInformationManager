<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useItemsStore } from '@/stores/items'

const store = useItemsStore()
const router = useRouter()

const text = ref('')
const kind = ref('note')
const submitting = ref(false)

const kinds = [
  { value: 'note', label: '笔记' },
  { value: 'idea', label: '想法' },
  { value: 'link', label: '链接' },
  { value: 'todo', label: '待办' },
]

const recent = computed(() => store.list.slice(0, 5))
const canSubmit = computed(() => text.value.trim().length > 0 && !submitting.value)

onMounted(() => {
  store.fetch(true).catch(() => void 0)
})

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  try {
    const created = await store.create(text.value, kind.value)
    if (created === null) {
      // 重复内容：不报错，给一个能跳过去的入口
      ElMessage.warning('这条之前记过了，已带你去原文')
      if (store.lastDuplicate) router.push(`/inbox?focus=${store.lastDuplicate}`)
      return
    }
    text.value = ''
    ElMessage.success('已记下，正在后台整理')
  } catch (err) {
    ElMessage.error((err as Error).message)
  } finally {
    submitting.value = false
  }
}

const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const accept =
  '.pdf,.docx,.xlsx,.pptx,.txt,.md,.markdown,.csv,.tsv,.json,.log,.yaml,.yml,.html,.htm,.xml'

function pickFile() {
  if (!uploading.value) fileInput.value?.click()
}

async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // 清空，允许再次选择同一个文件
  if (!file) return
  uploading.value = true
  try {
    const created = await store.upload(file)
    if (created === null) {
      ElMessage.warning('这个文件之前上传过了，已带你去原记录')
      if (store.lastDuplicate) router.push(`/inbox?focus=${store.lastDuplicate}`)
      return
    }
    ElMessage.success('文件已上传，正在后台解析和整理')
  } catch (err) {
    ElMessage.error((err as Error).message)
  } finally {
    uploading.value = false
  }
}

function onKeydown(e: KeyboardEvent) {
  // Ctrl/Cmd + Enter 提交：记东西的人大多在键盘上
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
    e.preventDefault()
    submit()
  }
}

function statusLabel(s: string) {
  return { pending: '排队中', running: '分析中', done: '', failed: '分析失败', skipped: '' }[s] ?? s
}
</script>

<template>
  <div class="capture">
    <div class="page-head">
      <div>
        <h1 class="page-title">记一笔</h1>
        <p class="page-sub">想到什么先丢进来，分类和关联是后台的事，不用你操心。</p>
      </div>
    </div>

    <div class="card composer">
      <textarea
        v-model="text"
        rows="6"
        placeholder="一句想法、一段摘录、一个链接、一件要做的事……直接写，不用整理格式。"
        @keydown="onKeydown"
      />

      <div class="bar">
        <div class="kinds">
          <button
            v-for="k in kinds"
            :key="k.value"
            class="kind"
            :class="{ on: kind === k.value }"
            @click="kind = k.value"
          >
            {{ k.label }}
          </button>
          <button class="kind upload" type="button" :disabled="uploading" @click="pickFile">
            {{ uploading ? '上传中…' : '＋ 文件' }}
          </button>
          <input ref="fileInput" type="file" :accept="accept" hidden @change="onFileChange" />
        </div>
        <div class="right">
          <span class="hint small muted">⌘/Ctrl + Enter 提交</span>
          <button class="btn primary" :disabled="!canSubmit" @click="submit">
            {{ submitting ? '记录中…' : '记下' }}
          </button>
        </div>
      </div>

      <p class="hint small muted upload-hint">
        也可上传 PDF / Word / Excel / PPT / Markdown / TXT / CSV / HTML 等文件（≤25 MB），
        后台会自动解析成文字再整理。
      </p>
    </div>

    <section v-if="recent.length" class="recent">
      <div class="recent-head">
        <h2 class="small muted">最近记的</h2>
        <RouterLink to="/inbox" class="small">查看全部 →</RouterLink>
      </div>

      <ul class="list">
        <li v-for="item in recent" :key="item.id" class="row" @click="$router.push('/inbox?focus=' + item.id)">
          <div class="row-main">
            <div class="row-title">{{ item.title || item.bodyMd.slice(0, 40) || '（无标题）' }}</div>
            <div class="row-meta small muted">
              <span class="tag">{{ item.kind }}</span>
              {{ new Date(item.capturedAt).toLocaleString('zh-CN', { hour12: false }) }}
              <span v-if="statusLabel(item.analysisStatus)" class="pending">
                · {{ statusLabel(item.analysisStatus) }}
              </span>
            </div>
          </div>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.capture {
  max-width: 760px;
}
.composer {
  padding: 14px;
}
.composer textarea {
  border: none;
  padding: 4px;
  font-size: 15px;
  line-height: 1.75;
  resize: none;
}
.composer textarea:focus {
  border: none;
}
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
  gap: 12px;
  flex-wrap: wrap;
}
.kinds {
  display: flex;
  gap: 4px;
}
.kind {
  border: 1px solid transparent;
  background: none;
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 12.5px;
  color: var(--muted);
  cursor: pointer;
}
.kind:hover {
  background: var(--hover);
}
.kind.on {
  background: var(--accent-soft);
  color: var(--accent);
}
.kind.upload {
  border: 1px dashed var(--line);
  color: var(--muted);
}
.kind.upload:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.kind.upload:disabled {
  opacity: 0.6;
  cursor: default;
}
.upload-hint {
  margin: 8px 4px 0;
  line-height: 1.6;
}
.right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.hint {
  user-select: none;
}
.recent {
  margin-top: 30px;
}
.recent-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 8px;
}
.recent-head a {
  color: var(--muted);
  text-decoration: none;
}
.recent-head a:hover {
  color: var(--accent);
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: 1px solid var(--line);
}
.row {
  padding: 11px 6px;
  border-bottom: 1px solid var(--line);
  cursor: pointer;
}
.row:hover {
  background: var(--hover);
}
.row-title {
  font-size: 13.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row-meta {
  margin-top: 3px;
}
.pending {
  color: var(--warn);
}
</style>
