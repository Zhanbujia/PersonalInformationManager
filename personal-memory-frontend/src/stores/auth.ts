import { defineStore } from 'pinia'
import { http, setToken, getToken } from '@/api/client'
import type { User } from '@/types'

interface AuthState {
  user: User | null
  loading: boolean
  ready: boolean
}

interface TokenResponse {
  accessToken: string
  user: User
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({ user: null, loading: false, ready: false }),

  getters: {
    isLoggedIn: (s) => !!s.user,
  },

  actions: {
    async login(email: string, password: string) {
      this.loading = true
      try {
        const res = await http.post<TokenResponse>('/auth/login', { email, password })
        setToken(res.accessToken)
        this.user = res.user
      } finally {
        this.loading = false
      }
    },

    async register(email: string, password: string, displayName?: string) {
      this.loading = true
      try {
        const res = await http.post<TokenResponse>('/auth/register', {
          email,
          password,
          displayName,
        })
        setToken(res.accessToken)
        this.user = res.user
      } finally {
        this.loading = false
      }
    },

    /** 刷新页面后恢复登录态：有 token 就试着换回用户信息，失败就当未登录。 */
    async bootstrap() {
      if (this.ready) return
      if (!getToken()) {
        this.ready = true
        return
      }
      try {
        this.user = await http.get<User>('/auth/me')
      } catch {
        setToken(null)
        this.user = null
      } finally {
        this.ready = true
      }
    },

    logout() {
      setToken(null)
      this.user = null
    },
  },
})
