<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

onMounted(() => auth.bootstrap())

const nav = [
  { name: 'capture', label: '记一笔', hint: '想到什么先丢进来' },
  { name: 'library', label: '资料库', hint: '文件与资料归档' },
  { name: 'inbox', label: '收件箱', hint: '所有记录与分类' },
  { name: 'ask', label: '问一句', hint: '基于你自己的记录回答' },
  { name: 'todos', label: '待办', hint: '整理出来的要做的事' },
  { name: 'digest', label: '回顾', hint: '阶段总结与行动建议' },
]

function logout() {
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <div class="shell">
    <aside v-if="auth.isLoggedIn" class="sidebar">
      <div class="brand">
        <span class="brand-mark">拾</span>
        <div>
          <div class="brand-name">拾穗</div>
          <div class="brand-sub">个人信息管家</div>
        </div>
      </div>

      <nav class="nav">
        <RouterLink
          v-for="item in nav"
          :key="item.name"
          :to="{ name: item.name }"
          class="nav-item"
          :class="{ active: route.name === item.name }"
        >
          <span class="nav-label">{{ item.label }}</span>
          <span class="nav-hint">{{ item.hint }}</span>
        </RouterLink>
      </nav>

      <div class="sidebar-foot">
        <div class="who">{{ auth.user?.displayName || auth.user?.email }}</div>
        <button class="linkish" @click="logout">退出</button>
      </div>
    </aside>

    <main class="main">
      <RouterView v-if="auth.ready" />
      <div v-else class="loading">正在恢复登录状态…</div>
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
}

.sidebar {
  width: 216px;
  flex-shrink: 0;
  border-right: 1px solid var(--line);
  padding: 20px 14px;
  display: flex;
  flex-direction: column;
  gap: 22px;
  background: var(--surface);
}

.brand {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 0 6px;
}
.brand-mark {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: var(--accent);
  color: #fff;
  display: grid;
  place-items: center;
  font-weight: 600;
  font-size: 17px;
}
.brand-name {
  font-weight: 600;
  font-size: 15px;
}
.brand-sub {
  font-size: 11px;
  color: var(--muted);
  margin-top: 1px;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nav-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border-radius: 8px;
  color: var(--text);
  text-decoration: none;
  transition: background 0.12s;
}
.nav-item:hover {
  background: var(--hover);
}
.nav-item.active {
  background: var(--accent-soft);
}
.nav-label {
  font-size: 13.5px;
  font-weight: 500;
}
.nav-hint {
  font-size: 11px;
  color: var(--muted);
}

.sidebar-foot {
  margin-top: auto;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 6px;
  font-size: 12px;
  color: var(--muted);
}
.who {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 130px;
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

.main {
  flex: 1;
  min-width: 0;
  padding: 28px 32px 60px;
}
.loading {
  color: var(--muted);
  font-size: 13px;
}

@media (max-width: 760px) {
  .shell {
    flex-direction: column;
  }
  .sidebar {
    width: auto;
    border-right: none;
    border-bottom: 1px solid var(--line);
  }
  .nav {
    flex-direction: row;
    overflow-x: auto;
  }
  .nav-hint {
    display: none;
  }
  .main {
    padding: 18px 16px 48px;
  }
}
</style>
