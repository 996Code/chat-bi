import axios from 'axios'
import type { AxiosInstance } from 'axios'

const api: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Request interceptor: attach token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Response interceptor: handle 401
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Try refresh token
      const refreshToken = localStorage.getItem('refresh_token')
      if (refreshToken) {
        return api.post('/auth/refresh', { refresh_token: refreshToken })
          .then((res) => {
            localStorage.setItem('access_token', res.data.access_token)
            localStorage.setItem('refresh_token', res.data.refresh_token)
            // Retry original request
            error.config.headers.Authorization = `Bearer ${res.data.access_token}`
            return api.request(error.config)
          })
          .catch(() => {
            localStorage.removeItem('access_token')
            localStorage.removeItem('refresh_token')
            window.location.href = '/login'
            return Promise.reject(error)
          })
      } else {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  },
)

export default api
