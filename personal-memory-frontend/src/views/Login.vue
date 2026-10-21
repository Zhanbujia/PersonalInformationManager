<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/api/client'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const mode = ref<'login' | 'register'>('login')
const email = ref('')
const password = ref('')
const displayName = ref('')
const error = ref('')

async function submit() {
  error.value = ''
  if (!email.value.trim() || !password.value) {
    error.value = '账号和密码都要填'
    return
  }
  try {
    if (mode.value === 'login') {
      await auth.login(email.value.trim(), password.value)
    } else {
      await auth.register(email.value.trim(), password.value, displayName.value.trim() || undefined)
    }
    ElMessage.success(mode.value === 'login' ? '欢迎回来' : '账号已创建')
    const redirect = (route.query.redirect as string) || '/capture'
    router.push(redirect)
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : (err as Error).message
  }
}
</script>

<template>
  <div class="wrap">
    <div class="intro">
      <div class="mark">拾</div>
      <h1>拾穗</h1>
      <p class="tagline">
        随手记下的东西，散在各处就没了。<br />
        放在一起，它会长出线索。
      </p>
    </div>

    <div class="panel card">
      <div class="tabs">
        <button :class="{ on: mode === 'login' }" @click="mode = 'login'">登录</button>
        <button :class="{ on: mode === 'register' }" @click="mode = 'register'">注册</button>
      </div>

      <form @submit.prevent="submit">
        <label v-if="mode === 'register'">
          <span>昵称（可选）</span>
          <input v-model="displayName" type="text" placeholder="怎么称呼你" />
        </label>
        <label>
          <span>{{ mode === 'login' ? '邮箱 / 用户名' : '邮箱' }}</span>
          <input
            v-model="email"
            :type="mode === 'login' ? 'text' : 'email'"
            :placeholder="mode === 'login' ? 'admin 或 you@example.com' : 'you@example.com'"
            autocomplete="username"
          />
        </label>
        <label>
          <span>密码</span>
          <input
            v-model="password"
            type="password"
            placeholder="至少 8 位"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
          />
        </label>

        <p v-if="error" class="err">{{ error }}</p>

        <button class="btn primary submit" type="submit" :disabled="auth.loading">
          {{ auth.loading ? '稍等…' : mode === 'login' ? '登录' : '创建账号' }}
        </button>
      </form>

      <p class="foot small muted">
        数据只属于你自己的账号。所有查询都在数据库层按用户隔离，漏了条件也读不到别人的东西。
      </p>
    </div>
  </div>
</template>

<style scoped>
.wrap {
  display: flex;
  gap: 72px;
  align-items: center;
  justify-content: center;
  min-height: 88vh;
  flex-wrap: wrap;
}
.intro {
  max-width: 320px;
}
.mark {
  width: 46px;
  height: 46px;
  border-radius: 12px;
  background: var(--accent);
  color: #fff;
  display: grid;
  place-items: center;
  font-size: 22px;
  font-weight: 600;
  margin-bottom: 16px;
}
.intro h1 {
  font-size: 26px;
  letter-spacing: 2px;
}
.tagline {
  color: var(--muted);
  font-size: 14px;
  margin-top: 10px;
  line-height: 1.8;
}
.panel {
  width: 340px;
  padding: 22px;
}
.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 18px;
  background: var(--hover);
  padding: 3px;
  border-radius: 8px;
}
.tabs button {
  flex: 1;
  border: none;
  background: none;
  padding: 6px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--muted);
}
.tabs button.on {
  background: var(--surface);
  color: var(--text);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}
label {
  display: block;
  margin-bottom: 12px;
}
label span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 5px;
}
.submit {
  width: 100%;
  margin-top: 6px;
  padding: 9px;
}
.err {
  color: var(--danger);
  font-size: 12.5px;
  margin: 4px 0 0;
}
.foot {
  margin: 16px 0 0;
  line-height: 1.7;
}
</style>
