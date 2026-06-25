import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: '/chat',
    },
    {
      path: '/login',
      name: 'Login',
      component: () => import('../views/LoginView.vue'),
    },
    {
      path: '/chat',
      name: 'Chat',
      component: () => import('../views/ChatView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/datasources',
      name: 'DataSources',
      component: () => import('../views/DataSourceView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/dashboard',
      name: 'Dashboard',
      component: () => import('../views/DashboardView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/semantic',
      name: 'Semantic',
      component: () => import('../views/SemanticView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/observability',
      name: 'Observability',
      component: () => import('../views/ObservabilityView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/skills',
      name: 'Skills',
      component: () => import('../views/SkillsView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/memory',
      name: 'Memory',
      component: () => import('../views/MemoryView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/history',
      name: 'History',
      component: () => import('../views/HistoryView.vue'),
      meta: { requiresAuth: true },
    },
  ],
})

// 路由守卫: 没 token 跳登录; 已登录访问登录页跳对话
router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('access_token')
  if (to.meta.requiresAuth && !token) {
    next('/login')
  } else if (to.path === '/login' && token) {
    next('/chat')
  } else {
    next()
  }
})

export { router }
