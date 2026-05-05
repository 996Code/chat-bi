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
  const onMessageUpdate = ref<(() => void) | null>(null)

  function _serializeMessages(): any[] {
    // Save messages with query results (cap rows at 100 to avoid bloat)
    return messages.value.map(m => ({
      id: m.id,
      role: m.role,
      content: m.content,
      sql: m.sql,
      columns: m.columns,
      rows: m.rows ? m.rows.slice(0, 100) : undefined,
      row_count: m.row_count,
      error: m.error,
      execution_time_ms: m.execution_time_ms,
      chart_type: m.chart_type,
      pipelineSteps: m.pipelineSteps,
    }))
  }

  async function _saveCurrentConversation(): Promise<void> {
    if (!currentConversationId.value) {
      // Create new conversation
      const title = messages.value.find(m => m.role === 'user')?.content?.slice(0, 50) || '新对话'
      try {
        const res = await api.post('/conversations', {
          title,
          datasource_id: currentDatasourceId.value,
          messages: _serializeMessages(),
        })
        currentConversationId.value = res.data.id
      } catch {
        // Non-blocking
      }
    } else {
      // Update existing conversation
      try {
        await api.put(`/conversations/${currentConversationId.value}`, {
          messages: _serializeMessages(),
        })
      } catch {
        // Non-blocking
      }
    }
  }

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
          history: messages.value
            .filter(m => m.role === 'user' || (m.role === 'assistant' && m.sql))
            .slice(-6)
            .map(m => ({
              role: m.role,
              content: m.content,
              sql: m.sql || undefined,
            })),
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
      // Auto-save conversation after query completes
      await _saveCurrentConversation()
      await loadConversations()
    }
  }

  function handleSSEEvent(eventType: string, data: any, msg: Message): void {
    switch (eventType) {
      case 'intent':
        msg.pipelineSteps = [
          { type: 'intent', label: `意图识别: ${data.intent === 'DataQuery' ? '数据查询' : '其他'}`, status: 'done', detail: data.detail },
          { type: 'sql', label: 'Schema 选择', status: 'running' },
        ]
        if (data.intent !== 'DataQuery') {
          msg.content = data.error || '请提出数据查询相关的问题'
          msg.pipelineSteps[1].status = 'failed'
        }
        break

      case 'semantics':
        // Schema selection done (LLM two-step: table selection + column selection)
        const schemaStep = msg.pipelineSteps?.find(s => s.type === 'sql' && s.status === 'running')
        if (schemaStep) {
          schemaStep.label = 'Schema 选择'
          schemaStep.status = 'done'
          schemaStep.detail = data.detail
        }
        msg.pipelineSteps?.push({ type: 'sql', label: 'SQL 生成', status: 'running' })
        break

      case 'sql':
        msg.sql = data.sql
        // Mark SQL generation done, start execution
        const sqlGenStep = msg.pipelineSteps?.find(s => s.type === 'sql' && s.status === 'running')
        if (sqlGenStep) {
          sqlGenStep.detail = data.detail || data.sql?.slice(0, 100)
          sqlGenStep.status = 'done'
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
          if (runStep) {
            runStep.status = 'done'
            runStep.detail = data.detail
          }
        } else {
          msg.error = data.error || '执行失败'
          msg.content = msg.error || '执行失败'
          const runStep = msg.pipelineSteps?.find(s => s.status === 'running')
          if (runStep) {
            runStep.status = 'failed'
            runStep.detail = data.detail
          }
        }
        break

      case 'chart':
        msg.chart_type = data.chart_type
        // Add chart step with detail
        msg.pipelineSteps?.push({ type: 'chart', label: '图表推断', status: 'done', detail: data.detail })
        break

      case 'complete':
        // Final step - ensure all running steps are marked done
        msg.pipelineSteps?.forEach(s => {
          if (s.status === 'running') s.status = 'done'
        })
        break

      case 'error':
        msg.error = data.error || '未知错误'
        msg.content = msg.error || '未知错误'
        msg.pipelineSteps?.forEach(s => {
          if (s.status === 'running') s.status = 'failed'
        })
        break
    }

    onMessageUpdate.value?.()
  }

  function clearMessages() {
    messages.value = []
  }

  function newConversation() {
    currentConversationId.value = null
    clearMessages()
  }

  async function loadConversation(convId: string) {
    currentConversationId.value = convId
    try {
      const res = await api.get(`/conversations/${convId}`)
      const conv = res.data
      clearMessages()

      // Set datasource from conversation
      if (conv.datasource_id) {
        currentDatasourceId.value = conv.datasource_id
      }

      // Restore messages from conversation (including saved query results)
      const savedMessages: any[] = conv.messages || []
      for (const m of savedMessages) {
        if (typeof m !== 'object' || !m.role) continue
        messages.value.push({
          id: m.id || `msg-${Date.now()}-${Math.random()}`,
          role: m.role,
          content: m.content || '',
          sql: m.sql,
          columns: m.columns,
          rows: m.rows,
          row_count: m.row_count,
          error: m.error,
          execution_time_ms: m.execution_time_ms,
          chart_type: m.chart_type,
          pipelineSteps: m.pipelineSteps || (m.role === 'assistant' && m.sql
            ? [
                { type: 'intent' as const, label: '意图识别: 数据查询', status: 'done' as const },
                { type: 'sql' as const, label: 'Schema 选择', status: 'done' as const },
                { type: 'sql' as const, label: 'SQL 生成', status: 'done' as const, detail: m.sql?.slice(0, 100) },
                { type: 'data' as const, label: '执行查询', status: m.error ? 'failed' as const : 'done' as const },
              ]
            : undefined),
          timestamp: new Date(),
        })
      }
    } catch {
      clearMessages()
    }
  }

  async function loadConversations() {
    try {
      const res = await api.get('/conversations')
      const items = Array.isArray(res.data) ? res.data : []
      conversations.value = items.map((c: any) => ({
        id: c.id,
        title: c.title || '新对话',
        message_count: c.message_count || 0,
        datasource_id: c.datasource_id,
        updated_at: c.updated_at || c.created_at,
      }))
    } catch {
      conversations.value = []
    }
  }

  async function deleteConversation(convId: string) {
    await api.delete(`/conversations/${convId}`)
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
    onMessageUpdate,
    sendQuestion,
    clearMessages,
    newConversation,
    loadConversation,
    loadConversations,
    deleteConversation,
  }
})
