import axios from 'axios'
import type { AxiosInstance } from 'axios'

const API_PREFIX = import.meta.env.VITE_API_PREFIX || '/chat-bi/api/v1'
const BASE_PATH = import.meta.env.VITE_BASE_PATH || '/chat-bi/'

const api: AxiosInstance = axios.create({
  baseURL: API_PREFIX,
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
      const refreshToken = localStorage.getItem('refresh_token')
      if (refreshToken) {
        return api.post('/auth/refresh', { refresh_token: refreshToken })
          .then((res) => {
            localStorage.setItem('access_token', res.data.access_token)
            localStorage.setItem('refresh_token', res.data.refresh_token)
            error.config.headers.Authorization = `Bearer ${res.data.access_token}`
            return api.request(error.config)
          })
          .catch(() => {
            localStorage.removeItem('access_token')
            localStorage.removeItem('refresh_token')
            window.location.href = `${BASE_PATH}login`
            return Promise.reject(error)
          })
      } else {
        window.location.href = `${BASE_PATH}login`
      }
    }
    return Promise.reject(error)
  },
)

export default api
