import { defineStore } from 'pinia'
import { http } from '@/api/client'
import type { Todo, TodoGroup } from '@/types'

interface TodosState {
  open: Todo[]
  groups: TodoGroup[]
  loading: boolean
}

/** 幂等键由前端生成：连点两次只会建出一条待办。约束在数据库层，不靠前端防抖。 */
function clientId(): string {
  return `web-${crypto.randomUUID?.() ?? Date.now()}`
}

export const useTodosStore = defineStore('todos', {
  state: (): TodosState => ({ open: [], groups: [], loading: false }),

  actions: {
    async fetch() {
      this.loading = true
      try {
        const [open, groups] = await Promise.all([
          http.get<Todo[]>('/todos?status=open'),
          http.get<TodoGroup[]>('/todos/grouped'),
        ])
        this.open = open
        this.groups = groups
      } finally {
        this.loading = false
      }
    },

    async create(text: string, dueDate?: string | null) {
      const todo = await http.post<Todo>('/todos', {
        text,
        dueDate: dueDate || null,
        clientId: clientId(),
      })
      this.open = [todo, ...this.open]
      await this.fetch()
      return todo
    },

    async toggle(todo: Todo) {
      const next = todo.status === 'done' ? 'open' : 'done'
      const updated = await http.patch<Todo>(`/todos/${todo.id}`, { status: next })
      this.open = this.open.filter((t) => t.id !== todo.id)
      if (next === 'open') this.open.push(updated)
      await this.fetch()
    },
  },
})
