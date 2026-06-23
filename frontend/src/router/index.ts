import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: '/datasources',
    },
    {
      path: '/login',
      name: 'Login',
      component: () => import('../views/LoginView.vue'),
    },
    {
      path: '/datasources',
      name: 'DataSources',
      component: () => import('../views/DataSourceView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/semantic',
      name: 'Semantic',
      component: () => import('../views/SemanticView.vue'),
      meta: { requiresAuth: true },
    },
  ],
})

// 路由守卫: 没 token 跳登录
router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('access_token')
  if (to.meta.requiresAuth && !token) {
    next('/login')
  } else {
    next()
  }
})

export { router }
