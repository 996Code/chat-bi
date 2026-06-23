import axios from 'axios'

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

// Response interceptor: handle 401 token refresh
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401) {
      // TODO: Phase 1 — implement token refresh
      localStorage.removeItem('access_token')
    }
    return Promise.reject(error)
  },
)

export default apiClient
