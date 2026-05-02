import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api'

export interface QueryResponse {
  success: boolean
  intent: string | null
  sql: string | null
  columns: string[]
  rows: Record<string, any>[]
  row_count: number
  error: string | null
  execution_time_ms: number | null
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  sql?: string
  columns?: string[]
  rows?: Record<string, any>[]
  row_count?: number
  error?: string
  execution_time_ms?: number
  timestamp: Date
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const loading = ref(false)
  const currentDatasourceId = ref<string | null>(null)

  async function sendQuestion(question: string): Promise<void> {
    if (!currentDatasourceId.value) {
      messages.value.push({
        id: `msg-${Date.now()}`,
        role: 'assistant',
        content: '',
        error: '请先选择一个数据源',
        timestamp: new Date(),
      })
      return
    }

    // Add user message
    messages.value.push({
      id: `msg-${Date.now()}`,
      role: 'user',
      content: question,
      timestamp: new Date(),
    })

    loading.value = true
    try {
      const res = await api.post('/query', {
        question,
        datasource_id: currentDatasourceId.value,
      })
      const data: QueryResponse = res.data

      messages.value.push({
        id: `msg-${Date.now()}`,
        role: 'assistant',
        content: data.success ? '查询成功' : (data.error || '查询失败'),
        sql: data.sql || undefined,
        columns: data.columns,
        rows: data.rows,
        row_count: data.row_count,
        error: data.error || undefined,
        execution_time_ms: data.execution_time_ms || undefined,
        timestamp: new Date(),
      })
    } catch (error: any) {
      messages.value.push({
        id: `msg-${Date.now()}`,
        role: 'assistant',
        content: '',
        error: error.response?.data?.detail?.message || '请求失败',
        timestamp: new Date(),
      })
    } finally {
      loading.value = false
    }
  }

  function clearMessages() {
    messages.value = []
  }

  return {
    messages,
    loading,
    currentDatasourceId,
    sendQuestion,
    clearMessages,
  }
})
