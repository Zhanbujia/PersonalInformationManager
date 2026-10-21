<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAskStore } from '@/stores/ask'
import { fetchBlobUrl } from '@/api/client'
import CitationBadge from '@/components/CitationBadge.vue'
import MarkdownText from '@/components/MarkdownText.vue'
import type { ChatAttachment, ChatMessage, ChatSession, Citation } from '@/types'

const store = useAskStore()
const input = ref('')
const scroller = ref<HTMLElement | null>(null)
const textareaEl = ref<HTMLTextAreaElement | null>(null)
const active = ref<Citation | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const imageUrls = ref<Record<string, string>>({})
const previewOpen = ref(false)
const previewUrl = ref('')

const accept =
  '.pdf,.docx,.xlsx,.pptx,.txt,.md,.markdown,.csv,.tsv,.json,.log,.yaml,.yml,.html,.htm,.xml,.png,.jpg,.jpeg,.webp,.gif'

const examples = [
  '我上次看的那篇讲召回的文章说了什么',
  '这周我记了哪些跟产品有关的东西',
  '有哪些待办拖了比较久',
]

onMounted(async () => {
  try {
    await store.loadSessions()
    if (store.sessions.length) {
      await openSession(store.sessions[0].id)
    }
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
})

onUnmounted(() => {
  Object.values(imageUrls.value).forEach((u) => URL.revokeObjectURL(u))
})

async function openSession(id: string) {
  try {
    await store.openSession(id)
    await ensureImageUrls()
    scrollDown()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function send() {
  const text = input.value.trim()
  if ((!text && !store.pending.length) || store.streaming) return
  await ensureImageUrls()
  input.value = ''
  if (textareaEl.value) textareaEl.value.style.height = 'auto'
  await store.send(text)
  await ensureImageUrls()
  scrollDown()
}

function scrollDown() {
  nextTick(() => {
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  })
}

watch(
  () => store.messages.length,
  () => {
    scrollDown()
    ensureImageUrls()
  },
)
watch(
  () => store.messages.map((m) => m.content.length).join(','),
  () => scrollDown(),
)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

function autoGrow(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}

function askExample(ex: string) {
  if (store.streaming) return
  input.value = ex
  send()
}

function pickFiles() {
  if (!store.uploading) fileInput.value?.click()
}

async function onFileChange(e: Event) {
  const el = e.target as HTMLInputElement
  const files = Array.from(el.files || [])
  el.value = ''
  for (const f of files) {
    try {
      await store.uploadAttachment(f)
    } catch (err) {
      ElMessage.error((err as Error).message)
    }
  }
  await ensureImageUrls()
}

async function ensureImageUrls() {
  const atts: ChatAttachment[] = [
    ...store.pending,
    ...store.messages.flatMap((m) => m.attachments ?? []),
  ]
  for (const a of atts) {
    if (a.kind !== 'image' || imageUrls.value[a.id]) continue
    try {
      imageUrls.value[a.id] = await fetchBlobUrl(`/ask/attachments/${a.id}`)
    } catch {
      /* 单张失败不影响其它 */
    }
  }
}

function previewImage(a: ChatAttachment) {
  const url = imageUrls.value[a.id]
  if (url) {
    previewUrl.value = url
    previewOpen.value = true
  }
}

async function copy(m: ChatMessage) {
  if (!m.content) return
  try {
    await navigator.clipboard.writeText(m.content)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}

async function renameSession(s: ChatSession) {
  try {
    const { value } = await ElMessageBox.prompt('新的会话标题', '重命名会话', {
      inputValue: s.title,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    })
    const title = (value || '').trim()
    if (!title || title === s.title) return
    await store.renameSession(s.id, title)
    ElMessage.success('已重命名')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error((e as Error).message)
  }
}

async function removeSession(s: ChatSession) {
  try {
    await ElMessageBox.confirm(`删除会话「${s.title}」？对话记录会一并删除。`, '删除会话', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await store.deleteSession(s.id)
    ElMessage.success('已删除')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error((e as Error).message)
  }
}

const stageLabel = (s?: string) =>
  ({ retrieving: '正在检索你的记录…', reading: '正在读原文…', answering: '' })[s ?? ''] ?? ''

function extLabel(name: string) {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1).toUpperCase().slice(0, 4) : 'FILE'
}

function fmtTime(s: string) {
  const t = new Date(s).getTime()
  const diff = (Date.now() - t) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return new Date(s).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}
</script>

<template>
  <div class="ask">
    <!-- 左侧：历史会话 -->
    <aside class="rail card">
      <button class="btn primary new-chat" @click="store.newSession()">＋ 新对话</button>
      <div class="session-list">
        <div
          v-for="s in store.sessions"
          :key="s.id"
          class="session"
          :class="{ on: s.id === store.sessionId }"
          @click="openSession(s.id)"
        >
          <div class="s-body">
            <div class="s-title">{{ s.title || '新对话' }}</div>
            <div class="s-meta">{{ s.messageCount }} 条 · {{ fmtTime(s.updatedAt) }}</div>
          </div>
          <div class="s-actions">
            <button class="mini" @click.stop="renameSession(s)">改名</button>
            <button class="mini danger" @click.stop="removeSession(s)">删除</button>
          </div>
        </div>
        <p v-if="!store.sessions.length" class="small muted empty-sessions">还没有历史对话</p>
      </div>
    </aside>

    <!-- 右侧：对话 -->
    <section class="chat">
      <div class="page-head">
        <div>
          <h1 class="page-title">问一句</h1>
          <p class="page-sub">只基于你自己的记录回答；支持上传文档和图片一起问。</p>
        </div>
      </div>

      <div ref="scroller" class="thread">
        <div v-if="!store.messages.length" class="intro card">
          <p class="lead">试试这样问：</p>
          <button v-for="ex in examples" :key="ex" class="example" @click="askExample(ex)">
            {{ ex }}
          </button>
          <p class="small muted note">
            回答里的每个 [1] [2] 角标都对应一段真实检索到的原文。也可以点输入框左侧的「＋」
            上传 PDF / Word / Excel / PPT / 文本或图片，再一起提问。
          </p>
        </div>

        <div
          v-for="m in store.messages"
          :key="m.id"
          class="msg"
          :class="m.role === 'user' ? 'user' : 'assistant'"
        >
          <template v-if="m.role === 'user'">
            <div class="user-wrap">
              <div v-if="m.content" class="bubble user-bubble">{{ m.content }}</div>
              <div v-if="m.attachments?.length" class="att-row">
                <span
                  v-for="a in m.attachments"
                  :key="a.id"
                  class="att-chip"
                  :class="{ bad: a.status === 'failed' }"
                  :title="a.error || a.fileName"
                >
                  <img
                    v-if="a.kind === 'image' && imageUrls[a.id]"
                    :src="imageUrls[a.id]"
                    class="att-thumb"
                    @click="previewImage(a)"
                  />
                  <template v-else>
                    <span class="att-badge">{{ a.kind === 'image' ? 'IMG' : extLabel(a.fileName) }}</span>
                    <span class="att-name">{{ a.fileName }}</span>
                  </template>
                </span>
              </div>
            </div>
          </template>

          <template v-else>
            <div class="bot">
              <div class="bot-head">
                <span class="small muted">拾穗</span>
                <button v-if="m.content && !m.streaming" class="mini" @click="copy(m)">复制</button>
              </div>
              <div v-if="m.stage && !m.content" class="thinking">
                <span class="dot" /><span class="dot" /><span class="dot" />
                {{ stageLabel(m.stage) }}
              </div>
              <MarkdownText v-if="m.content" :source="m.content" />
              <CitationBadge :citations="m.citations" @open="active = $event" />
              <div v-if="m.tokens && !m.streaming" class="small muted token-line">
                输入 {{ m.tokens.in }} / 输出 {{ m.tokens.out }} tokens
              </div>
            </div>
          </template>
        </div>

        <p v-if="store.error" class="err small">{{ store.error }}</p>
      </div>

      <div class="composer">
        <div v-if="store.pending.length" class="pending">
          <div
            v-for="a in store.pending"
            :key="a.id"
            class="chip"
            :class="{ bad: a.status === 'failed' }"
            :title="a.error || a.fileName"
          >
            <img
              v-if="a.kind === 'image' && imageUrls[a.id]"
              :src="imageUrls[a.id]"
              class="chip-thumb"
              @click="previewImage(a)"
            />
            <span v-else class="att-badge">{{ a.kind === 'image' ? 'IMG' : extLabel(a.fileName) }}</span>
            <span class="chip-name">{{ a.fileName }}</span>
            <span v-if="a.status === 'failed'" class="chip-err">解析失败</span>
            <button class="chip-x" @click="store.removePending(a.id)">×</button>
          </div>
        </div>

        <div class="input-row">
          <button class="btn attach" :disabled="store.uploading" title="添加附件" @click="pickFiles">
            {{ store.uploading ? '…' : '＋' }}
          </button>
          <input ref="fileInput" type="file" multiple :accept="accept" hidden @change="onFileChange" />
          <textarea
            ref="textareaEl"
            v-model="input"
            rows="1"
            placeholder="问点什么…（Enter 发送，Shift+Enter 换行）"
            :disabled="store.streaming"
            @keydown="onKeydown"
            @input="autoGrow"
          />
          <button v-if="store.streaming" class="btn" @click="store.stop()">停止</button>
          <button
            v-else
            class="btn primary"
            :disabled="!input.trim() && !store.pending.length"
            @click="send"
          >
            发送
          </button>
        </div>
      </div>
    </section>

    <el-dialog v-model="active" :title="`引用 [${active?.n ?? ''}]`" width="520px">
      <p class="muted small" style="margin-top: 0">{{ active?.title || '来源记录' }}</p>
      <blockquote class="quote">{{ active?.quote || '（无摘录）' }}</blockquote>
      <template #footer>
        <RouterLink
          v-if="active?.itemId"
          :to="`/inbox?focus=${active.itemId}`"
          class="btn"
          @click="active = null"
        >
          去看原文
        </RouterLink>
      </template>
    </el-dialog>

    <el-dialog v-model="previewOpen" width="640px">
      <img v-if="previewUrl" :src="previewUrl" class="preview-img" />
    </el-dialog>
  </div>
</template>

<style scoped>
.ask {
  display: grid;
  grid-template-columns: 210px minmax(0, 1fr);
  gap: 16px;
  height: calc(100vh - 90px);
}

/* ---------- 历史会话 ---------- */
.rail {
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: hidden;
}
.new-chat {
  width: 100%;
  padding: 7px;
  font-size: 12.5px;
}
.session-list {
  flex: 1;
  overflow-y: auto;
}
.session {
  position: relative;
  padding: 8px 9px;
  border-radius: 7px;
  cursor: pointer;
  margin-bottom: 2px;
}
.session:hover {
  background: var(--hover);
}
.session.on {
  background: var(--accent-soft);
}
.s-title {
  font-size: 12.5px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.s-meta {
  font-size: 11px;
  color: var(--muted);
  margin-top: 2px;
}
.s-actions {
  display: none;
  position: absolute;
  right: 6px;
  top: 6px;
  gap: 2px;
  background: var(--surface);
  border-radius: 5px;
}
.session:hover .s-actions {
  display: flex;
}
.mini {
  border: none;
  background: none;
  color: var(--muted);
  cursor: pointer;
  font-size: 11px;
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
.empty-sessions {
  margin: 10px 6px;
}

/* ---------- 对话区 ---------- */
.chat {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.thread {
  flex: 1;
  overflow-y: auto;
  padding-right: 6px;
  scroll-behavior: smooth;
}
.intro {
  padding: 20px;
}
.lead {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--muted);
}
.example {
  display: block;
  width: 100%;
  text-align: left;
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 8px;
  padding: 9px 12px;
  margin-bottom: 8px;
  font-size: 13px;
  cursor: pointer;
  color: var(--text);
}
.example:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.note {
  margin: 12px 0 0;
  line-height: 1.7;
}
.msg {
  margin-bottom: 16px;
  display: flex;
}
.msg.user {
  justify-content: flex-end;
}
.user-wrap {
  max-width: 88%;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}
.bubble {
  border-radius: 12px;
  padding: 10px 14px;
  font-size: 13.5px;
  line-height: 1.8;
}
.user-bubble {
  background: var(--accent);
  color: #fff;
  white-space: pre-wrap;
}
.assistant {
  width: 100%;
}
.bot {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 10px 14px;
  width: 100%;
  font-size: 13.5px;
  line-height: 1.8;
}
.bot-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.thinking {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--muted);
  font-size: 12.5px;
}
.dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--muted);
  animation: blink 1.2s infinite;
}
.dot:nth-child(2) {
  animation-delay: 0.2s;
}
.dot:nth-child(3) {
  animation-delay: 0.4s;
}
@keyframes blink {
  0%,
  80%,
  100% {
    opacity: 0.25;
  }
  40% {
    opacity: 1;
  }
}
.token-line {
  margin-top: 6px;
}

/* ---------- 附件 ---------- */
.att-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.att-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 8px;
  padding: 3px 7px;
  font-size: 11.5px;
  max-width: 240px;
}
.att-chip.bad {
  border-color: var(--warn);
  background: var(--warn-soft);
}
.att-thumb {
  width: 64px;
  height: 64px;
  object-fit: cover;
  border-radius: 6px;
  cursor: zoom-in;
}
.att-badge {
  font-size: 9px;
  font-weight: 600;
  color: #fff;
  background: #5b6470;
  border-radius: 4px;
  padding: 2px 4px;
}
.att-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.chip {
  display: flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 3px 6px;
  font-size: 11.5px;
  max-width: 240px;
  background: var(--surface);
}
.chip.bad {
  border-color: var(--warn);
  background: var(--warn-soft);
}
.chip-thumb {
  width: 28px;
  height: 28px;
  object-fit: cover;
  border-radius: 5px;
  cursor: zoom-in;
}
.chip-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chip-err {
  color: var(--warn);
  font-size: 10.5px;
}
.chip-x {
  border: none;
  background: none;
  cursor: pointer;
  color: var(--muted);
  font-size: 14px;
  line-height: 1;
  padding: 0 2px;
}
.chip-x:hover {
  color: var(--danger);
}

/* ---------- 输入区 ---------- */
.composer {
  padding-top: 10px;
  border-top: 1px solid var(--line);
}
.input-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.attach {
  padding: 8px 12px;
  font-size: 16px;
  line-height: 1;
  flex-shrink: 0;
}
.input-row textarea {
  resize: none;
  min-height: 40px;
  max-height: 160px;
  line-height: 1.6;
}
.err {
  color: var(--danger);
}
.quote {
  margin: 8px 0 0;
  padding: 10px 12px;
  border-left: 3px solid var(--accent);
  background: var(--hover);
  border-radius: 0 6px 6px 0;
  font-size: 13px;
  line-height: 1.8;
}
.preview-img {
  max-width: 100%;
  border-radius: 8px;
  display: block;
  margin: 0 auto;
}

@media (max-width: 820px) {
  .ask {
    grid-template-columns: 1fr;
    height: auto;
  }
  .rail {
    max-height: 180px;
  }
  .session-list {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    overflow-y: hidden;
  }
  .session {
    min-width: 150px;
  }
  .thread {
    max-height: 60vh;
  }
}
</style>
