<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '@/api/client'
import { useTodosStore } from '@/stores/todos'
import MarkdownText from '@/components/MarkdownText.vue'
import type { Digest } from '@/types'

const todos = useTodosStore()

const kind = ref<'weekly' | 'monthly'>('weekly')
const range = ref<{ periodStart: string; periodEnd: string } | null>(null)
const generating = ref(false)
const list = ref<Digest[]>([])
const current = ref<Digest | null>(null)
const jobId = ref<number | null>(null)

onMounted(async () => {
  await Promise.all([loadRange(), loadList()])
})

async function loadRange() {
  range.value = await http.get(`/digests/range/preview?kind=${kind.value}`)
}

async function loadList() {
  list.value = await http.get<Digest[]>('/digests?limit=20')
  if (!current.value && list.value.length) current.value = list.value[0]
}

async function generate() {
  generating.value = true
  try {
    const res = await http.post<{ jobId: number; estimatedSeconds: number }>('/digests', {
      kind: kind.value,
      periodStart: range.value?.periodStart,
      periodEnd: range.value?.periodEnd,
    })
    jobId.value = res.jobId
    ElMessage.info('正在生成，大概半分钟')
    await poll(res.jobId)
  } catch (err) {
    ElMessage.error((err as Error).message)
  } finally {
    generating.value = false
  }
}

async function poll(id: number) {
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 2000))
    const s = await http.get<{ status: string; digestId?: string; error?: string }>(
      `/digests/jobs/${id}`,
    )
    if (s.status === 'done' && s.digestId) {
      const d = await http.get<Digest>(`/digests/${s.digestId}`)
      await loadList()
      current.value = d
      ElMessage.success('生成好了')
      return
    }
    if (s.status === 'failed') {
      ElMessage.error(s.error || '生成失败')
      return
    }
  }
  ElMessage.warning('等太久了，稍后刷新看看')
}

async function adopt(digest: Digest, index: number) {
  const item = digest.actionItems[index]
  try {
    await todos.create(item.text)
    ElMessage.success('已加进待办')
  } catch (err) {
    ElMessage.error((err as Error).message)
  }
}

function switchKind(k: 'weekly' | 'monthly') {
  kind.value = k
  loadRange()
}
</script>

<template>
  <div class="digest">
    <div class="page-head">
      <div>
        <h1 class="page-title">回顾</h1>
        <p class="page-sub">
          正文里的每个数字都来自数据库统计，模型只负责把它们串起来——所以点开统计能看到原值。
        </p>
      </div>
    </div>

    <div class="card generator">
      <div class="seg">
        <button :class="{ on: kind === 'weekly' }" @click="switchKind('weekly')">周总结</button>
        <button :class="{ on: kind === 'monthly' }" @click="switchKind('monthly')">月总结</button>
      </div>
      <div class="range small muted">
        {{ range?.periodStart }} → {{ range?.periodEnd }}
      </div>
      <button class="btn primary" :disabled="generating" @click="generate">
        {{ generating ? '生成中…' : '生成' }}
      </button>
    </div>

    <div class="layout">
      <aside v-if="list.length > 1" class="history">
        <div class="small muted">历史</div>
        <button
          v-for="d in list"
          :key="d.id"
          class="history-item"
          :class="{ on: current?.id === d.id }"
          @click="current = d"
        >
          <span>{{ d.kind === 'weekly' ? '周' : d.kind === 'monthly' ? '月' : '临时' }}</span>
          <span class="small">{{ d.periodStart }}</span>
          <span v-if="d.stale" class="stale small">已过期</span>
        </button>
      </aside>

      <section class="panel">
        <div v-if="!current" class="card empty">
          <p class="muted">还没有总结。先记几天东西，再来生成。</p>
        </div>

        <article v-else class="card content">
          <div v-if="current.stale" class="stale-bar small">
            部分依据的记录后来被重新分析过，这份总结的结论可能已经不准，建议重新生成。
          </div>

          <header class="content-head">
            <h2>{{ current.periodStart }} — {{ current.periodEnd }}</h2>
            <span class="small muted">{{ current.model || '无模型' }}</span>
          </header>

          <MarkdownText :source="current.contentMd" />

          <div v-if="current.actionItems.length" class="actions">
            <h3 class="small muted">可以接着做的事</h3>
            <div v-for="(a, i) in current.actionItems" :key="i" class="action">
              <div class="action-text">{{ a.text }}</div>
              <div class="small muted">{{ a.reason }}</div>
              <button class="btn tiny" @click="adopt(current, i)">加进待办</button>
            </div>
          </div>

          <details v-if="Object.keys(current.stats).length" class="stats">
            <summary class="small muted">统计原值（正文里的数字都来自这里）</summary>
            <pre class="small">{{ JSON.stringify(current.stats, null, 2) }}</pre>
          </details>
        </article>
      </section>
    </div>
  </div>
</template>

<style scoped>
.digest {
  max-width: 900px;
}
.generator {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}
.seg {
  display: flex;
  background: var(--hover);
  border-radius: 8px;
  padding: 3px;
}
.seg button {
  border: none;
  background: none;
  padding: 5px 14px;
  border-radius: 6px;
  font-size: 12.5px;
  color: var(--muted);
  cursor: pointer;
}
.seg button.on {
  background: var(--surface);
  color: var(--text);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}
.range {
  flex: 1;
}
.layout {
  display: flex;
  gap: 18px;
  align-items: flex-start;
}
.history {
  width: 116px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.history-item {
  display: flex;
  gap: 6px;
  align-items: center;
  border: none;
  background: none;
  padding: 5px 7px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  color: var(--text);
  text-align: left;
}
.history-item:hover {
  background: var(--hover);
}
.history-item.on {
  background: var(--accent-soft);
}
.stale {
  color: var(--warn);
}
.panel {
  flex: 1;
  min-width: 0;
}
.content {
  padding: 20px 22px;
}
.content-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 12px;
}
.content-head h2 {
  font-size: 15px;
}
.stale-bar {
  background: var(--warn-soft);
  border: 1px solid var(--warn);
  color: var(--warn);
  border-radius: 7px;
  padding: 7px 10px;
  margin-bottom: 14px;
}
.actions {
  margin-top: 20px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}
.action {
  padding: 10px 0;
  border-bottom: 1px dashed var(--line);
}
.action:last-child {
  border-bottom: none;
}
.action-text {
  font-size: 13.5px;
}
.btn.tiny {
  margin-top: 6px;
  padding: 3px 10px;
  font-size: 12px;
}
.stats {
  margin-top: 18px;
}
.stats summary {
  cursor: pointer;
}
.stats pre {
  background: var(--hover);
  padding: 10px;
  border-radius: 8px;
  overflow-x: auto;
  max-height: 260px;
}
.empty {
  text-align: center;
  padding: 40px;
}
</style>
