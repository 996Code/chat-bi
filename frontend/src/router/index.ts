import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/authStore'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/LoginView.vue'),
    meta: { guest: true },
  },
  {
    path: '/register',
    name: 'Register',
    component: () => import('@/views/RegisterView.vue'),
    meta: { guest: true },
  },
  {
    path: '/reset-password',
    name: 'ResetPassword',
    component: () => import('@/views/ResetPasswordView.vue'),
    meta: { guest: true },
  },
  {
    path: '/verify-email',
    name: 'VerifyEmail',
    component: () => import('@/views/VerifyEmailView.vue'),
    meta: { guest: true },
  },
  {
    path: '/',
    name: 'Chat',
    component: () => import('@/views/ChatView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/datasources',
    name: 'DataSources',
    component: () => import('@/views/DataSourceListView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/data-models',
    name: 'DataModels',
    component: () => import('@/views/DataModelView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/monitoring',
    name: 'Monitoring',
    component: () => import('@/views/MonitoringView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/evaluation',
    name: 'Evaluation',
    component: () => import('@/views/EvaluationView.vue'),
    meta: { requiresAuth: true, adminOnly: true },
  },
  {
    path: '/query-history',
    name: 'QueryHistory',
    component: () => import('@/views/QueryHistory.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/dashboards',
    name: 'Dashboards',
    component: () => import('@/views/DashboardView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/dashboards/share/:token',
    name: 'SharedDashboard',
    component: () => import('@/views/SharedDashboardView.vue'),
    meta: { public: true },
  },
  {
    path: '/guide',
    name: 'DocGuide',
    component: () => import('@/views/DocGuideView.vue'),
    meta: { public: true },
  },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.VITE_BASE_PATH || '/chat-bi/'),
  routes,
})

router.beforeEach((to, _from) => {
  const authStore = useAuthStore()
  authStore.initFromStorage()

  // Public routes: no auth check at all (shared dashboards, etc.)
  if (to.meta.public) return true

  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    return { name: 'Login', query: { redirect: to.fullPath } }
  }
  if (to.meta.guest && authStore.isAuthenticated) {
    return { name: 'Chat' }
  }
  // Admin-only routes: check from decoded JWT in store
  if (to.meta.adminOnly && authStore.user?.role !== 'admin') {
    return { name: 'Chat' }
  }
})

export default router
