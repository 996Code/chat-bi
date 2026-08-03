/**
 * HTTP 客户端 — Axios 实例与 JWT 拦截器
 *
 * 架构职责:
 *   1. 创建 Axios 实例, 统一配置 baseURL、超时、请求头
 *   2. 请求拦截器: 自动注入 JWT Bearer token
 *   3. 响应拦截器: 401 自动刷新 token, 刷新失败跳转登录页
 *
 * JWT 认证流程 (AUTH-01~03):
 *   ┌─────────────────────────────────────────────────────────┐
 *   │  请求 → 请求拦截器注入 access_token                         │
 *   │  → 响应 401 → 响应拦截器拦截                                │
 *   │    → 有 refresh_token → 调用 /auth/refresh                │
 *   │      → 成功 → 重试原请求                                   │
 *   │      → 失败 → 清除 token, 跳转 /login                    │
 *   │    → 无 refresh_token → 直接跳转 /login                   │
 *   └─────────────────────────────────────────────────────────┘
 *
 * 设计决策:
 *   - 不直接使用 axios 的 interceptors 做 token 刷新, 因为
 *     axios 拦截器队列是异步的, 需要手动管理并发刷新请求
 *   - 使用 _refreshPromise 单例模式: 多个请求同时 401 时,
 *     只发一次 refresh 请求, 所有等待的请求共享同一个 Promise
 *   - 刷新端点直接用 axios.post 而非 apiClient, 避免触发
 *     自身拦截器造成死循环 (循环引用)
 *   - router 延迟导入: 避免循环依赖 (api 模块在 router 之前加载)
 */
import axios from 'axios'
import { clearToken, setToken } from '@/composables/useAuth'

/**
 * 创建 Axios 实例
 *
 * baseURL: '/chat-bi/api/v1' — 与后端约定的一致前缀
 * timeout: 30000ms — 常规请求超时
 *   注意: 聊天问答接口 (POST /chat) 在调用处单独设置 120000ms 超时
 * Content-Type: application/json — 默认 JSON 格式
 *   FormData 上传时需手动覆盖此 header
 */
const apiClient = axios.create({
  baseURL: '/chat-bi/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

/**
 * 请求拦截器: 自动注入 JWT token
 *
 * 从 localStorage 读取 access_token,
 * 如果存在则注入 Authorization: Bearer <token> 请求头。
 * 不做 token 有效性检查 (过期由响应拦截器处理),
 * 保持请求拦截器轻量快速。
 */
// Request interceptor: attach JWT token
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

/**
 * 响应拦截器: 401 自动刷新, 刷新失败跳转登录
 *
 * 核心逻辑:
 *   1. 收到 401 响应后, 检查是否已重试过 (_retried 标记)
 *      以及是否已经是刷新端点本身 (避免循环)
 *   2. 调用 _tryRefresh() 尝试刷新 token
 *   3. 刷新成功 → 重试原请求 (携带新 token)
 *   4. 刷新失败 → 清除所有 token, 跳转登录页
 *
 * 并发安全: _refreshPromise 单例模式确保多个 401 请求
 * 只触发一次 refresh 调用, 所有请求等待同一个 Promise 结果。
 * 这是对标 V1 经验教训 #17: token 刷新竞态问题。
 */
// Response interceptor: 401 时尝试用 refresh_token 刷新, 失败跳登录页 (AUTH-03 闭环)
let _refreshPromise: Promise<string | null> | null = null

/**
 * 尝试刷新 token (单例模式, 并发安全)
 *
 * 流程:
 *   1. 如果已有正在进行的刷新请求, 直接返回该 Promise
 *   2. 从 localStorage 读取 refresh_token
 *   3. 调用 POST /auth/refresh 获取新 token 对
 *   4. 更新 access_token 和 refresh_token 到 localStorage
 *   5. 返回新的 access_token
 *   6. 任何异常 → 返回 null (刷新失败)
 *
 * 注意: 使用 axios.post 而非 apiClient.post,
 * 避免触发自身响应拦截器造成无限循环。
 */
// 并发刷新合并: 多个请求同时 401 时只发一次 refresh
async function _tryRefresh(): Promise<string | null> {
  // 并发刷新合并: 多个请求同时 401 时只发一次 refresh
  if (_refreshPromise) return _refreshPromise
  _refreshPromise = (async () => {
    const refreshToken = localStorage.getItem('refresh_token')
    if (!refreshToken) return null
    try {
      // 直接用 axios 避免触发自身拦截器循环
      const res = await axios.post('/chat-bi/api/v1/auth/refresh', { refresh_token: refreshToken })
      const { access_token, refresh_token: newRefresh } = res.data
      setToken(access_token)
      localStorage.setItem('refresh_token', newRefresh)
      return access_token
    } catch {
      return null
    } finally {
      _refreshPromise = null
    }
  })()
  return _refreshPromise
}

/**
 * 延迟导入 router 并跳转登录页
 *
 * 为什么延迟导入:
 *   - api/client.ts 在 main.ts 中先于 router 被 import
 *   - 直接 import router 会造成循环依赖 (router → view → api → client → router)
 *   - 动态 import() 在运行时执行, 此时 router 已经初始化完成
 *
 * 跳转时携带 redirect 参数:
 *   - 记录当前页面路径, 登录后可以跳转回来
 *   - 但当前登录页登录后默认跳转 /chat, 不处理 redirect
 *   - 这是简化设计, 未来可扩展
 */
// 延迟导入 router, 避免循环依赖 (api 模块在 router 之前加载)
async function _goToLogin() {
  try {
    const { router } = await import('@/router')
    if (router.currentRoute.value.path !== '/login') {
      router.push({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
    }
  } catch { /* router 未就绪时静默降级 */ }
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config
    // 401 且未重试过 且非刷新端点自身 → 尝试刷新
    if (
      error.response?.status === 401 &&
      !originalRequest._retried &&
      !originalRequest.url?.includes('/auth/refresh')
    ) {
      const newToken = await _tryRefresh()
      if (newToken) {
        // 标记已重试, 避免无限循环
        originalRequest._retried = true
        originalRequest.headers.Authorization = `Bearer ${newToken}`
        // 使用更新后的 token 重试原请求
        return apiClient(originalRequest)
      }
      // 刷新失败 → 注销并跳转登录页
      clearToken()
      localStorage.removeItem('refresh_token')
      try {
        const { ElMessage } = await import('element-plus')
        ElMessage.warning('登录已过期，请重新登录')
      } catch { /* element-plus 未就绪时静默降级 */ }
      _goToLogin()
    }
    // 非 401 错误或刷新失败后的错误, 继续向上抛, 由调用方处理
    return Promise.reject(error)
  },
)

export default apiClient
