import axios from 'axios'
import { clearToken, setToken } from '@/composables/useAuth'

const apiClient = axios.create({
  baseURL: '/chat-bi/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Request interceptor: attach JWT token
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Response interceptor: 401 时尝试用 refresh_token 刷新, 失败才注销 (AUTH-03 闭环)
let _isRefreshing = false
let _refreshPromise: Promise<string | null> | null = null

async function _tryRefresh(): Promise<string | null> {
  // 并发刷新合并: 多个请求同时 401 时只发一次 refresh
  if (_refreshPromise) return _refreshPromise
  _isRefreshing = true
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
      _isRefreshing = false
      _refreshPromise = null
    }
  })()
  return _refreshPromise
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
        originalRequest._retried = true
        originalRequest.headers.Authorization = `Bearer ${newToken}`
        return apiClient(originalRequest)
      }
      // 刷新失败 → 注销
      clearToken()
      localStorage.removeItem('refresh_token')
    }
    return Promise.reject(error)
  },
)

export default apiClient
