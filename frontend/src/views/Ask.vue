<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useAskStore } from '@/stores/ask'
import CitationBadge from '@/components/CitationBadge.vue'
import MarkdownText from '@/components/MarkdownText.vue'
import type { Citation } from '@/types'

const store = useAskStore()
const input = ref('')
const scroller = ref<HTMLElement | null>(null)
const active = ref<Citation | null>(null)

const examples = [
  '我上次看的那篇讲召回的文章说了什么',
  '这周我记了哪些跟产品有关的东西',
  '有哪些待办拖了比较久',
]

async function send() {
  const text = input.value.trim()
  if (!text || store.streaming) return
  input.value = ''
  await store.send(text)
  scrollDown()
}

function scrollDown() {
  nextTick(() => {
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  })
}

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

const stageLabel = (s?: string) =>
  ({ retrieving: '正在检索你的记录…', reading: '正在读原文…', answering: '' })[s ?? ''] ?? ''
</script>

<template>
  <div class="ask">
    <div class="page-head">
      <div>
        <h1 class="page-title">问一句</h1>
        <p class="page-sub">只基于你自己的记录回答，每条结论后面都能点开看到原文。</p>
      </div>
      <button v-if="store.messages.length" class="btn" @click="store.reset()">新对话</button>
    </div>

    <div ref="scroller" class="thread">
      <div v-if="!store.messages.length" class="intro card">
        <p class="lead">试试这样问：</p>
        <button v-for="ex in examples" :key="ex" class="example" @click="((input = ex), send())">
          {{ ex }}
        </button>
        <p class="small muted note">
          回答里的每个 [1] [2] 角标都对应一段真实检索到的原文。核对不了的角标会被降级成"来源未核实"。
        </p>
      </div>

      <div
        v-for="m in store.messages"
        :key="m.id"
        class="msg"
        :class="m.role === 'user' ? 'user' : 'assistant'"
      >
        <div v-if="m.role === 'user'" class="bubble user-bubble">{{ m.content }}</div>

        <div v-else class="bubble bot">
          <div v-if="m.stage && !m.content" class="thinking">
            <span class="dot" /><span class="dot" /><span class="dot" />
            {{ stageLabel(m.stage) }}
          </div>
          <MarkdownText v-if="m.content" :source="m.content" />
          <CitationBadge :citations="m.citations" @open="active = $event" />
        </div>
      </div>

      <p v-if="store.error" class="err small">{{ store.error }}</p>
    </div>

    <div class="composer">
      <textarea
        v-model="input"
        rows="2"
        placeholder="问点什么…（Enter 发送，Shift+Enter 换行）"
        :disabled="store.streaming"
        @keydown="onKeydown"
      />
      <button v-if="store.streaming" class="btn" @click="store.stop()">停止</button>
      <button v-else class="btn primary" :disabled="!input.trim()" @click="send">发送</button>
    </div>

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
  </div>
</template>

<style scoped>
.ask {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 90px);
  max-width: 820px;
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
.bubble {
  max-width: 88%;
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
.bot {
  background: var(--surface);
  border: 1px solid var(--line);
  width: 100%;
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
.composer {
  display: flex;
  gap: 10px;
  align-items: flex-end;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}
.composer textarea {
  resize: none;
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
</style>
