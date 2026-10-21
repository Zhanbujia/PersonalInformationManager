<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useTodosStore } from '@/stores/todos'

const store = useTodosStore()
const text = ref('')
const due = ref('')

onMounted(() => store.fetch().catch(() => void 0))

async function add() {
  const t = text.value.trim()
  if (!t) return
  try {
    await store.create(t, due.value || null)
    text.value = ''
    due.value = ''
  } catch (err) {
    ElMessage.error((err as Error).message)
  }
}

async function toggle(id: string) {
  const todo = store.groups.flatMap((g) => g.todos).find((t) => t.id === id)
  const found = todo ?? store.open.find((t) => t.id === id)
  if (!found) return
  await store.toggle(found)
}

function overdue(d?: string | null) {
  if (!d) return false
  const today = new Date().toISOString().slice(0, 10)
  return d < today
}
</script>

<template>
  <div class="todos">
    <div class="page-head">
      <div>
        <h1 class="page-title">待办</h1>
        <p class="page-sub">分组和排序都是按日期算出来的，不是模型排的——同样的输入永远得到同样的顺序。</p>
      </div>
    </div>

    <div class="card add">
      <input v-model="text" type="text" placeholder="要做的事…" @keydown.enter="add" />
      <input v-model="due" type="date" class="due" />
      <button class="btn primary" :disabled="!text.trim()" @click="add">添加</button>
    </div>

    <div v-for="g in store.groups" :key="g.bucket" class="group">
      <h2 class="group-head small muted">
        {{ g.label }}
        <span v-if="g.todos.length" class="count">{{ g.todos.length }}</span>
      </h2>
      <p v-if="!g.todos.length" class="small muted none">这里空着。</p>
      <ul v-else class="list">
        <li v-for="t in g.todos" :key="t.id" class="row">
          <button class="check" @click="toggle(t.id)" title="标记完成" />
          <span class="text">{{ t.text }}</span>
          <span v-if="t.dueDate" class="small" :class="overdue(t.dueDate) ? 'late' : 'muted'">
            {{ t.dueDate }}
          </span>
          <span class="pri small muted">P{{ t.priority }}</span>
        </li>
      </ul>
    </div>

    <p v-if="store.loading" class="small muted">加载中…</p>
  </div>
</template>

<style scoped>
.todos {
  max-width: 760px;
}
.add {
  display: flex;
  gap: 10px;
  margin-bottom: 24px;
  padding: 12px;
}
.add input[type='text'] {
  flex: 1;
}
.due {
  width: 148px;
  font-family: inherit;
  font-size: 13px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
  background: var(--surface);
  color: var(--text);
}
.group {
  margin-bottom: 22px;
}
.group-head {
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.count {
  background: var(--hover);
  border-radius: 10px;
  padding: 0 6px;
  font-size: 11px;
}
.none {
  margin: 0;
  padding-left: 2px;
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: 1px solid var(--line);
}
.row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 4px;
  border-bottom: 1px solid var(--line);
}
.check {
  width: 16px;
  height: 16px;
  border-radius: 5px;
  border: 1.5px solid var(--line);
  background: var(--surface);
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.12s;
}
.check:hover {
  border-color: var(--accent);
}
.text {
  flex: 1;
  font-size: 13.5px;
}
.late {
  color: var(--danger);
}
.pri {
  width: 22px;
  text-align: right;
}
</style>
