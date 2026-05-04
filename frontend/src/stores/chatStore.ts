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
  chart_type: string
}

export interface PipelineStep {
  type: 'intent' | 'sql' | 'data' | 'chart' | 'complete'
  label: string
  status: 'running' | 'done' | 'failed'
  detail?: string
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
  chart_type?: string
  pipelineSteps?: PipelineStep[]
  timestamp: Date
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const loading = ref(false)
  const currentDatasourceId = ref<string | null>(null)
  const currentConversationId = ref<string | null>(null)
  const conversations = ref<any[]>([])

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

    // Create assistant message placeholder with pipeline steps
    const assistantId = `msg-${Date.now()}`
    const assistantMsg: Message = {
      id: assistantId,
      role: 'assistant',
      content: '处理中...',
      pipelineSteps: [],
      timestamp: new Date(),
    }
    messages.value.push(assistantMsg)
    loading.value = true

    try {
      const baseURL = (api.defaults.baseURL || '').replace(/\/$/, '')
      const token = localStorage.getItem('access_token')

      const response = await fetch(`${baseURL}/query/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({
          question,
          datasource_id: currentDatasourceId.value,
        }),
      })

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}))
        throw new Error(errData.detail?.message || '请求失败')
      }

      const reader = response.body?.getReader()
      if (!reader) throw new Error('Stream not available')

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        let i = 0
        while (i < lines.length) {
          if (lines[i].startsWith('event: ')) {
            const eventType = lines[i].slice(7).trim()
            i++
            if (i < lines.length && lines[i].startsWith('data: ')) {
              try {
                const data = JSON.parse(lines[i].slice(6))
                handleSSEEvent(eventType, data, assistantMsg)
              } catch {
                // Skip malformed JSON
              }
            }
          }
          i++
        }
      }

      if (assistantMsg.error) {
        assistantMsg.content = assistantMsg.error
        assistantMsg.pipelineSteps = assistantMsg.pipelineSteps?.map(s =>
          s.status === 'running' ? { ...s, status: 'failed' as const } : s
        )
      }
    } catch (error: any) {
      assistantMsg.content = ''
      assistantMsg.error = error.message || '请求失败'
      assistantMsg.pipelineSteps = []
    } finally {
      loading.value = false
    }
  }

  function handleSSEEvent(eventType: string, data: any, msg: Message): void {
    switch (eventType) {
      case 'intent':
        msg.pipelineSteps = [
          { type: 'intent', label: `意图识别: ${data.intent === 'DataQuery' ? '数据查询' : '其他'}`, status: 'done' },
          { type: 'sql', label: '检索表结构', status: 'running' },
        ]
        if (data.intent !== 'DataQuery') {
          msg.content = data.error || '请提出数据查询相关的问题'
          msg.pipelineSteps[1].status = 'failed'
        }
        break

      case 'semantics':
        // Semantic parsing done
        const sqlStep = msg.pipelineSteps?.find(s => s.type === 'sql')
        if (sqlStep) sqlStep.status = 'done'
        msg.pipelineSteps?.push({ type: 'data', label: '生成 SQL', status: 'running' })
        break

      case 'sql':
        msg.sql = data.sql
        // Mark SQL generation done, start execution
        const execStep = msg.pipelineSteps?.find(s => s.type === 'data' && s.status === 'running')
        if (execStep) {
          execStep.type = 'sql'
          execStep.detail = data.sql?.slice(0, 100)
          execStep.status = 'done'
        }
        msg.pipelineSteps?.push({ type: 'data', label: '执行查询', status: 'running' })
        break

      case 'data':
        if (data.success) {
          msg.columns = data.columns || []
          msg.rows = data.rows || []
          msg.row_count = data.row_count || 0
          msg.content = '查询成功'
          const runStep = msg.pipelineSteps?.find(s => s.type === 'data' && s.status === 'running')
          if (runStep) runStep.status = 'done'
        } else {
          msg.error = data.error || '执行失败'
          msg.content = msg.error
          const runStep = msg.pipelineSteps?.find(s => s.status === 'running')
          if (runStep) runStep.status = 'failed'
        }
        break

      case 'chart':
        msg.chart_type = data.chart_type
        break

      case 'complete':
        // Final step - ensure all running steps are marked done
        msg.pipelineSteps?.forEach(s => {
          if (s.status === 'running') s.status = 'done'
        })
        break

      case 'error':
        msg.error = data.error || '未知错误'
        msg.content = msg.error
        msg.pipelineSteps?.forEach(s => {
          if (s.status === 'running') s.status = 'failed'
        })
        break
    }
  }

  function clearMessages() {
    messages.value = []
  }

  function newConversation() {
    currentConversationId.value = null
    clearMessages()
  }

  async function loadConversation(convId: string) {
    // For now just clear — full implementation loads from saved queries
    currentConversationId.value = convId
    clearMessages()
  }

  async function loadConversations() {
    try {
      const res = await api.get('/queries', { params: { page_size: 50 } })
      const data = res.data
      const items = Array.isArray(data) ? data : (data.data || [])
      conversations.value = items.map((q: any) => ({
        id: q.id,
        title: q.name || q.query_text?.slice(0, 30) || '未命名',
        message_count: 1,
        updated_at: q.created_at,
      }))
    } catch {
      conversations.value = []
    }
  }

  async function deleteConversation(convId: string) {
    await api.delete(`/queries/${convId}`)
    conversations.value = conversations.value.filter(c => c.id !== convId)
    if (currentConversationId.value === convId) {
      currentConversationId.value = null
      clearMessages()
    }
  }

  return {
    messages,
    loading,
    currentDatasourceId,
    currentConversationId,
    conversations,
    sendQuestion,
    clearMessages,
    newConversation,
    loadConversation,
    loadConversations,
    deleteConversation,
  }
})
