import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from '@/api/client'

const routes = [
  { path: '/', redirect: '/capture' },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/capture',
    name: 'capture',
    component: () => import('@/views/Capture.vue'),
    meta: { title: '记一笔' },
  },
  {
    path: '/inbox',
    name: 'inbox',
    component: () => import('@/views/Inbox.vue'),
    meta: { title: '收件箱' },
  },
  {
    path: '/ask',
    name: 'ask',
    component: () => import('@/views/Ask.vue'),
    meta: { title: '问一句' },
  },
  {
    path: '/todos',
    name: 'todos',
    component: () => import('@/views/Todos.vue'),
    meta: { title: '待办' },
  },
  {
    path: '/digest',
    name: 'digest',
    component: () => import('@/views/Digest.vue'),
    meta: { title: '回顾' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  if (to.meta.public) return true
  if (!getToken()) return { name: 'login', query: { redirect: to.fullPath } }
  return true
})

export default router
