import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      // 开发时把 /api 转到 FastAPI。
      // SSE 的流式响应在这里能正常工作，因为 Vite 不缓冲代理响应。
      // 用 127.0.0.1 而不是 localhost：Windows 上 localhost 可能先解析到 ::1，
      // 而 uvicorn 默认只监听 IPv4 回环，会连不上。
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
