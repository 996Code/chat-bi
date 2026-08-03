/**
 * ChatBI v2 路由配置 — 页面导航与访问控制
 *
 * 架构职责:
 *   1. 定义所有前端路由路径与对应的视图组件
 *   2. 通过路由守卫 (beforeEach) 实现未登录重定向
 *   3. 使用懒加载 (dynamic import) 减小首屏打包体积
 *
 * 路由设计:
 *   - 根路径 / 重定向到 /chat (对话页是默认首页)
 *   - /login 单独路由, 不要求认证, 已登录用户访问时自动跳转到 /chat
 *   - 所有业务页面 (chat, datasources, dashboard 等) 都有 requiresAuth 元信息
 *   - 无嵌套路由, 所有页面都是平级视图, 通过 App.vue 的导航栏切换
 *
 * 懒加载策略:
 *   - 每个视图组件使用 () => import(...) 动态导入
 *   - Webpack/Vite 会自动为每个视图生成独立的 chunk
 *   - 首屏只加载 LoginView 或 ChatView 对应的 chunk
 *   - 其他页面按需加载, 用户访问时才下载对应代码
 *   - 命名 chunk 有助于调试 (通过魔法注释 /* webpackChunkName * /)
 *
 * 数据流:
 *   路由守卫检查 localStorage 中的 access_token,
 *   不需要额外调用 API 验证 token 有效性 (由 API 层的 401 拦截器处理)
 */
import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  // createWebHistory: 使用 HTML5 History 模式, URL 不含 #, 更美观
  // 需要后端配合做 fallback (所有前端路由返回 index.html)
  history: createWebHistory(),

  // 路由表: 每个路由包含 path, name, component, meta
  // component 使用动态 import 实现懒加载
  // meta.requiresAuth 控制路由守卫的访问检查
  routes: [
    {
      // 根路径: 重定向到默认首页 /chat
      path: '/',
      redirect: '/chat',
    },
    {
      // 登录页: 不需要认证 (requiresAuth 为 false)
      // 如果用户已登录, 路由守卫会自动跳转到 /chat
      path: '/login',
      name: 'Login',
      component: () => import('../views/LoginView.vue'),
    },
    {
      // 对话页: 核心页面, 自然语言问答交互
      path: '/chat',
      name: 'Chat',
      component: () => import('../views/ChatView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 数据源管理: 数据库连接配置、扫描、健康检查
      path: '/datasources',
      name: 'DataSources',
      component: () => import('../views/DataSourceView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 看板: 仪表盘, 保存的图表 widget 集合
      path: '/dashboard',
      name: 'Dashboard',
      component: () => import('../views/DashboardView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 语义层: 表/列/指标/关系的语义标注
      path: '/semantic',
      name: 'Semantic',
      component: () => import('../views/SemanticView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 可观测性: 系统状态、审计日志、慢查询
      path: '/observability',
      name: 'Observability',
      component: () => import('../views/ObservabilityView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 规则管理: SQL 生成规则 (Skills)
      path: '/skills',
      name: 'Skills',
      component: () => import('../views/SkillsView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 记忆管理: Agent 长期记忆, 知识沉淀
      path: '/memory',
      name: 'Memory',
      component: () => import('../views/MemoryView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 历史记录: 历史对话列表
      path: '/history',
      name: 'History',
      component: () => import('../views/HistoryView.vue'),
      meta: { requiresAuth: true },
    },
  ],
})

/**
 * 路由前置守卫 — 认证检查与重定向
 *
 * 逻辑流程:
 *   1. 目标页面需要认证 (requiresAuth) 且无 token → 跳转到 /login
 *   2. 用户已登录但访问 /login 页面 → 跳转到 /chat (避免看到登录页)
 *   3. 其他情况 → 正常放行
 *
 * 设计决策:
 *   - 直接从 localStorage 读取 token, 不依赖响应式状态
 *     因为路由守卫在导航时执行, 不需要响应式追踪
 *   - 只检查 token 是否存在, 不验证 token 有效性
 *     如果 token 过期, API 层的 401 拦截器会处理刷新或跳转
 *   - 不保存 redirect 参数 (简化处理, 登录后默认回到 /chat)
 *     如果需要登录后回到之前页面, 可以在 URL query 中保存 redirect
 */
router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('access_token')
  if (to.meta.requiresAuth && !token) {
    // 未登录用户访问需要认证的页面 → 重定向到登录页
    next('/login')
  } else if (to.path === '/login' && token) {
    // 已登录用户访问登录页 → 重定向到默认首页
    next('/chat')
  } else {
    // 正常放行
    next()
  }
})

export { router }
