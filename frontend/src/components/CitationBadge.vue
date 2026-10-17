<script setup lang="ts">
import type { Citation } from '@/types'

defineProps<{ citations: Citation[] }>()
const emit = defineEmits<{ (e: 'open', c: Citation): void }>()
</script>

<template>
  <div v-if="citations.length" class="cites">
    <button
      v-for="c in citations"
      :key="c.n"
      class="badge"
      :title="c.quote || ''"
      @click="emit('open', c)"
    >
      {{ c.n }}
    </button>
  </div>
</template>

<style scoped>
.cites {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--line);
}
.badge {
  min-width: 22px;
  height: 22px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--muted);
  font-size: 11.5px;
  cursor: pointer;
  padding: 0 6px;
  transition: all 0.12s;
}
.badge:hover {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}
</style>
