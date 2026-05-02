import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/api'

export interface UserInfo {
  sub: string
  email: string
  tenant_id: string
  role: string
  exp: number
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserInfo | null>(null)
  const loading = ref(false)

  const isAuthenticated = computed(() => !!user.value)

  function initFromStorage() {
    if (!user.value) {
      const token = localStorage.getItem('access_token')
      if (token) {
        try {
          const payload = JSON.parse(atob(token.split('.')[1]))
          user.value = payload as UserInfo
        } catch {
          localStorage.removeItem('access_token')
          localStorage.removeItem('refresh_token')
        }
      }
    }
  }

  async function login(email: string, password: string) {
    loading.value = true
    try {
      const res = await api.post('/auth/login', { email, password })
      localStorage.setItem('access_token', res.data.access_token)
      localStorage.setItem('refresh_token', res.data.refresh_token)
      user.value = JSON.parse(atob(res.data.access_token.split('.')[1])) as UserInfo
      return true
    } catch (error: any) {
      return error.response?.data?.detail?.message || '登录失败'
    } finally {
      loading.value = false
    }
  }

  async function register(email: string, password: string) {
    loading.value = true
    try {
      await api.post('/auth/register', { email, password })
      return true
    } catch (error: any) {
      return error.response?.data?.detail?.message || '注册失败'
    } finally {
      loading.value = false
    }
  }

  function logout() {
    user.value = null
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
  }

  return { user, loading, isAuthenticated, initFromStorage, login, register, logout }
})
