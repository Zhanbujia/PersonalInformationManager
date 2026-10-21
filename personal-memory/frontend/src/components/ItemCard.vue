<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Item } from '@/types'

const props = defineProps<{ item: Item; expanded?: boolean }>()
const emit = defineEmits<{ (e: 'delete', id: string): void; (e: 'reanalyze', id: string): void }>()

const open = ref(props.expanded ?? false)

const statusText = computed(
  () =>
    ({
      pending: '排队中',
      running: '分析中',
      failed: '分析失败',
      done: '',
      skipped: '',
    })[props.item.analysisStatus] ?? '',
)

/** 用后端算好的 start/end 切出高亮片段。
 *  偏移量来自服务端，前端不再自己找字符串——两边各算一次必然对不齐。 */
const bodySegments = computed(() => {
  const spans = props.item.analysis?.keyPoints ?? []
  const text = props.item.bodyMd
  if (!spans.length || !text) return [{ text, mark: false }]

  const valid = spans
    .filter((s) => s.start >= 0 && s.end <= text.length && s.start < s.end)
    .sort((a, b) => a.start - b.start)

  const segments: { text: string; mark: boolean }[] = []
  let cursor = 0
  for (const s of valid) {
    if (s.start < cursor) continue // 重叠片段跳过，避免切乱
    if (s.start > cursor) segments.push({ text: text.slice(cursor, s.start), mark: false })
    segments.push({ text: text.slice(s.start, s.end), mark: true })
    cursor = s.end
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), mark: false })
  return segments
})

const capturedAt = computed(() =>
  new Date(props.item.capturedAt).toLocaleString('zh-CN', { hour12: false }),
)
</script>

<template>
  <article class="item card" :class="{ failed: item.analysisStatus === 'failed' }">
    <header class="head" @click="open = !open">
      <div class="left">
        <h3 class="title">{{ item.title || item.bodyMd.slice(0, 30) || '（无标题）' }}</h3>
        <div class="meta small muted">
          <span class="tag">{{ item.kind }}</span>
          {{ capturedAt }}
          <span v-if="statusText" class="status">· {{ statusText }}</span>
          <span v-if="item.userPinned" class="status">· 已手动修改</span>
        </div>
      </div>
      <span class="chev" :class="{ open }">›</span>
    </header>

    <p v-if="item.analysis?.summary" class="summary">{{ item.analysis.summary }}</p>

    <div v-if="open" class="detail">
      <div class="body">
        <template v-for="(seg, i) in bodySegments" :key="i">
          <mark v-if="seg.mark">{{ seg.text }}</mark>
          <span v-else>{{ seg.text }}</span>
        </template>
      </div>

      <div v-if="item.tags.length" class="tags">
        <span v-for="t in item.tags" :key="t" class="tag">{{ t }}</span>
      </div>

      <div v-if="item.links?.length" class="links">
        <div class="small muted">相关记录</div>
        <ul>
          <li v-for="l in item.links" :key="l.itemId">
            <RouterLink :to="`/inbox?focus=${l.itemId}`">{{ l.title || l.itemId.slice(0, 8) }}</RouterLink>
            <span class="small muted"> · 相似度 {{ (l.score * 100).toFixed(0) }}%</span>
          </li>
        </ul>
      </div>

      <div class="actions">
        <button class="btn" @click="emit('reanalyze', item.id)">重新分析</button>
        <button class="btn danger" @click="emit('delete', item.id)">删除</button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.item {
  padding: 14px 16px;
  margin-bottom: 10px;
}
.item.failed {
  border-color: var(--warn);
  background: var(--warn-soft);
}
.head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  cursor: pointer;
}
.title {
  font-size: 14px;
  font-weight: 500;
}
.meta {
  margin-top: 4px;
}
.status {
  color: var(--warn);
}
.chev {
  color: var(--muted);
  transition: transform 0.15s;
  font-size: 18px;
  line-height: 1;
}
.chev.open {
  transform: rotate(90deg);
}
.summary {
  margin: 8px 0 0;
  color: #3d4148;
  font-size: 13px;
}
.detail {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--line);
}
.body {
  white-space: pre-wrap;
  font-size: 13.5px;
  line-height: 1.8;
  color: #33383f;
}
.body mark {
  background: #f6edc8;
  padding: 0 1px;
  border-radius: 2px;
}
.tags {
  margin-top: 10px;
}
.links {
  margin-top: 12px;
}
.links ul {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 13px;
}
.links a {
  color: var(--accent);
  text-decoration: none;
}
.actions {
  margin-top: 14px;
  display: flex;
  gap: 8px;
}
.btn.danger:hover {
  border-color: var(--danger);
  color: var(--danger);
}
</style>
