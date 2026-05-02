import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api'

export interface Datasource {
  id: string
  name: string
  type: string
  host: string
  port: number
  database_name: string
  status: string
  last_health_check: string | null
  health_check_error: string | null
}

export interface DatasourceCreate {
  name: string
  type: string
  host: string
  port: number
  database_name: string
  username: string
  password: string
  extra_params?: Record<string, any>
}

export const useDatasourceStore = defineStore('datasource', () => {
  const datasources = ref<Datasource[]>([])
  const loading = ref(false)

  async function list() {
    loading.value = true
    try {
      const res = await api.get('/datasources')
      datasources.value = res.data
    } finally {
      loading.value = false
    }
  }

  async function create(data: DatasourceCreate) {
    loading.value = true
    try {
      const res = await api.post('/datasources', data)
      return { success: true, data: res.data }
    } catch (error: any) {
      return { success: false, error: error.response?.data?.detail?.message || '创建失败' }
    } finally {
      loading.value = false
    }
  }

  async function testConnection(id: string) {
    try {
      const res = await api.post(`/datasources/${id}/test`)
      return res.data
    } catch (error: any) {
      return { success: false, error: error.response?.data?.detail?.message || '连接测试失败' }
    }
  }

  async function remove(id: string) {
    try {
      await api.delete(`/datasources/${id}`)
      datasources.value = datasources.value.filter((ds) => ds.id !== id)
    } catch (error: any) {
      return error.response?.data?.detail?.message || '删除失败'
    }
  }

  return { datasources, loading, list, create, testConnection, remove }
})
