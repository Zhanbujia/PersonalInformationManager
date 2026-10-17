<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useItemsStore } from '@/stores/items'
import ItemCard from '@/components/ItemCard.vue'

const store = useItemsStore()
const route = useRoute()

const q = ref('')
const kind = ref('')
const focus = ref<string | null>(null)
let timer: number | undefined

const kinds = [
  { value: '', label: '全部' },
  { value: 'note', label: '笔记' },
  { value: 'idea', label: '想法' },
  { value: 'link', label: '链接' },
  { value: 'todo', label: '待办' },
]

const empty = computed(() => !store.loading && store.list.length === 0)

async function load() {
  store.filters = { q: q.value || undefined, kind: kind.value || undefined }
  await store.fetch(true)
}

onMounted(async () => {
  focus.value = (route.query.focus as string) || null
  await load()
  // 只轮询还在分析中的记录，避免整页刷新造成闪烁
  timer = window.setInterval(() => store.pollPending(), 3000)
})

onUnmounted(() => window.clearInterval(timer))

watch(() => route.query.focus, (v) => (focus.value = (v as string) || null))

let debounce: number | undefined
watch(q, () => {
  window.clearTimeout(debounce)
  debounce = window.setTimeout(load, 320)
})

async function onDelete(id: string) {
  try {
    await ElMessageBox.confirm('删除后记录会从列表消失（原文保留在库里，便于恢复）。', '确认删除', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await store.remove(id)
  ElMessage.success('已删除')
}

async function onReanalyze(id: string) {
  await store.reanalyze(id)
  ElMessage.info('已重新排队分析')
}
</script>

<template>
  <div class="inbox">
    <div class="page-head">
      <div>
        <h1 class="page-title">收件箱</h1>
        <p class="page-sub">共 {{ store.total }} 条记录。点标题展开详情，高亮的是模型挑出的重点。</p>
      </div>
    </div>

    <div class="filters">
      <input v-model="q" type="search" placeholder="搜标题和正文…" />
      <div class="kinds">
        <button
          v-for="k in kinds"
          :key="k.value"
          class="kind"
          :class="{ on: kind === k.value }"
          @click="((kind = k.value), load())"
        >
          {{ k.label }}
        </button>
      </div>
    </div>

    <div v-if="empty" class="empty card">
      <p>还没有记录。</p>
      <RouterLink to="/capture" class="btn primary">去记第一笔</RouterLink>
    </div>

    <div v-else class="list">
      <ItemCard
        v-for="item in store.list"
        :key="item.id"
        :item="item"
        :expanded="item.id === focus"
        :class="{ focused: item.id === focus }"
        @delete="onDelete"
        @reanalyze="onReanalyze"
      />
    </div>

    <div v-if="store.loading" class="small muted loading">加载中…</div>
  </div>
</template>

<style scoped>
.inbox {
  max-width: 820px;
}
.filters {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  align-items: center;
  flex-wrap: wrap;
}
.filters input {
  flex: 1;
  min-width: 180px;
}
.kinds {
  display: flex;
  gap: 2px;
}
.kind {
  border: 1px solid transparent;
  background: none;
  padding: 5px 11px;
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
.focused {
  outline: 2px solid var(--accent-soft);
}
.empty {
  text-align: center;
  padding: 44px 20px;
}
.empty p {
  color: var(--muted);
  margin: 0 0 14px;
}
.loading {
  padding: 16px 0;
}
</style>
