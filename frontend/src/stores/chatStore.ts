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
  type: 'intent' | 'semantics' | 'sql' | 'data' | 'chart' | 'complete' | 'cache'
  label: string
  status: 'running' | 'done' | 'failed'
  detail?: string
  duration_ms?: number
  tables?: string[]
  columns?: Record<string, string[]>
  sql?: string
  attempt?: number
  validation?: { table_fixes?: string[]; column_fixes?: string[] }
  error_code?: string
  retry?: number
  cached?: boolean
  cache_type?: 'exact' | 'semantic'
  is_slow?: boolean
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

  // Async query task tracking
  const activeAsyncTasks = ref<Map<string, { taskId: string; messageId: string; abort: () => void }>>(new Map())

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
    const userId = `user-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    messages.value.push({
      id: userId,
      role: 'user',
      content: question,
      timestamp: new Date(),
    })

    // Create assistant message placeholder with pipeline steps
    const assistantId = `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
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

  // Replace message in array to trigger Vue 3 reactivity
  function replaceMsgInArray(msg: Message): void {
    const idx = messages.value.findIndex(m => m.id === msg.id)
    if (idx !== -1) messages.value[idx] = { ...msg }
  }

  function handleSSEEvent(eventType: string, data: any, msg: Message): void {
    switch (eventType) {
      case 'cache':
        // Cache check step
        if (data.hit) {
          const cacheLabel = data.type === 'semantic' ? '语义缓存命中' : '精确缓存命中'
          msg.pipelineSteps = [{
            type: 'intent', label: cacheLabel, status: 'done',
            detail: `缓存命中，${data.duration_ms}ms 返回`,
            duration_ms: data.duration_ms,
            cached: true,
            cache_type: data.type,
          }]
        } else {
          msg.pipelineSteps = [{
            type: 'intent', label: '缓存检查', status: 'done',
            detail: '缓存未命中，进入 AI 查询管线',
            duration_ms: data.duration_ms,
            cached: false,
          }]
        }
        break

      case 'intent':
        // If cache was hit, we already have the pipelineSteps array with cache step
        if (msg.pipelineSteps?.length === 0 || !msg.pipelineSteps?.some(s => s.cached)) {
          msg.pipelineSteps = [
            { type: 'intent', label: `意图识别: ${data.intent === 'DataQuery' ? '数据查询' : '其他'}`, status: 'done', detail: data.detail, duration_ms: data.duration_ms },
            { type: 'semantics', label: 'Schema 选择', status: 'running' },
          ]
        } else {
          // After cache hit, add intent step + running semantics step
          msg.pipelineSteps.push({
            type: 'intent', label: `意图识别: ${data.intent === 'DataQuery' ? '数据查询' : '其他'}`, status: 'done', detail: data.detail, duration_ms: data.duration_ms
          })
          msg.pipelineSteps.push({ type: 'semantics', label: 'Schema 选择', status: 'running' })
        }
        if (data.intent !== 'DataQuery') {
          msg.content = data.error || '请提出数据查询相关的问题'
          msg.pipelineSteps[msg.pipelineSteps.length - 1].status = 'failed'
        }
        break

      case 'semantics':
        let schemaStep = msg.pipelineSteps?.find(s => s.type === 'semantics' && s.status === 'running')
        if (schemaStep) {
          schemaStep.label = 'Schema 选择'
          schemaStep.status = 'done'
          schemaStep.detail = data.detail
          schemaStep.duration_ms = data.duration_ms
          schemaStep.tables = data.tables
          schemaStep.columns = data.columns
        } else {
          // Cache hit path: no running semantics step, create and mark done
          msg.pipelineSteps?.push({
            type: 'semantics', label: 'Schema 选择', status: 'done',
            detail: data.detail, duration_ms: data.duration_ms,
            tables: data.tables, columns: data.columns,
          })
        }
        // Only add SQL step if not already present
        if (!msg.pipelineSteps?.some(s => s.type === 'sql')) {
          msg.pipelineSteps?.push({ type: 'sql', label: 'SQL 生成', status: 'running' })
        }
        break

      case 'sql':
        msg.sql = data.sql
        let sqlGenStep = msg.pipelineSteps?.find(s => s.type === 'sql' && s.status === 'running')
        if (sqlGenStep) {
          sqlGenStep.detail = data.detail || data.sql?.slice(0, 100)
          sqlGenStep.status = 'done'
          sqlGenStep.sql = data.sql
          sqlGenStep.duration_ms = data.duration_ms
          sqlGenStep.attempt = data.attempt
          sqlGenStep.validation = data.validation
          sqlGenStep.error_code = data.error_code
          sqlGenStep.retry = data.retry
        } else {
          // No running SQL step (cache hit or self-heal): create one
          msg.pipelineSteps?.push({
            type: 'sql', label: 'SQL 生成', status: 'done',
            detail: data.detail || data.sql?.slice(0, 100),
            sql: data.sql, duration_ms: data.duration_ms,
            attempt: data.attempt, validation: data.validation,
            error_code: data.error_code, retry: data.retry,
          })
        }
        // Only push execution step if there isn't one already (avoid duplicate on self-heal)
        if (!msg.pipelineSteps?.some(s => s.type === 'data')) {
          msg.pipelineSteps?.push({ type: 'data', label: '执行查询', status: 'running' })
        }
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
            runStep.duration_ms = data.duration_ms
          }
        } else {
          msg.error = data.error || '执行失败'
          msg.content = msg.error || '执行失败'
          const runStep = msg.pipelineSteps?.find(s => s.type === 'data' && s.status === 'running')
          if (runStep) {
            runStep.status = 'failed'
            runStep.detail = data.detail
            runStep.duration_ms = data.duration_ms
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
        // Add slow query warning
        if (data.is_slow) {
          msg.pipelineSteps?.push({
            type: 'complete', label: '慢查询', status: 'done',
            detail: '总耗时超过慢查询阈值',
            is_slow: true,
          })
        }
        break

      case 'error':
        msg.error = data.error || '未知错误'
        msg.content = msg.error || '未知错误'
        msg.pipelineSteps?.forEach(s => {
          if (s.status === 'running') s.status = 'failed'
        })
        break
    }

    // Replace message in array to trigger Vue 3 reactivity
    replaceMsgInArray(msg)
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

  // ==================== Async Query (PERF-03) ====================

  async function sendAsyncQuestion(question: string): Promise<string> {
    if (!currentDatasourceId.value) {
      throw new Error('请先选择一个数据源')
    }

    // Add user message
    const userId = `user-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    messages.value.push({
      id: userId,
      role: 'user',
      content: question,
      timestamp: new Date(),
    })

    // Create assistant placeholder
    const assistantId = `async-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    const assistantMsg: Message = {
      id: assistantId,
      role: 'assistant',
      content: '后台查询已提交，正在处理中...',
      pipelineSteps: [
        { type: 'intent', label: '后台查询', status: 'running', detail: '正在执行，请耐心等待' },
      ],
      timestamp: new Date(),
    }
    messages.value.push(assistantMsg)

    try {
      const baseURL = (api.defaults.baseURL || '').replace(/\/$/, '')
      const token = localStorage.getItem('access_token')

      const res = await fetch(`${baseURL}/query/async`, {
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

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        throw new Error(errData.detail?.message || '提交失败')
      }

      const data = await res.json()
      const taskId: string = data.task_id

      // Start polling in background
      const abortController = new AbortController()
      activeAsyncTasks.value.set(taskId, { taskId, messageId: assistantId, abort: () => abortController.abort() })

      pollAsyncTask(taskId, assistantMsg, abortController.signal)

      return taskId
    } catch (error: any) {
      assistantMsg.content = ''
      assistantMsg.error = error.message || '提交失败'
      assistantMsg.pipelineSteps = [{ type: 'intent', label: '后台查询', status: 'failed', detail: error.message }]
      return ''
    }
  }

  async function pollAsyncTask(taskId: string, msg: Message, signal: AbortSignal): Promise<void> {
    const baseURL = (api.defaults.baseURL || '').replace(/\/$/, '')
    const token = localStorage.getItem('access_token')
    const maxPolls = 120 // 10 minutes at 5s interval
    let polls = 0

    while (!signal.aborted && polls < maxPolls) {
      try {
        const res = await fetch(`${baseURL}/query/async/${taskId}`, {
          headers: { 'Authorization': `Bearer ${token}` },
        })

        if (!res.ok) {
          if (res.status === 404) {
            msg.error = '查询任务已过期'
            msg.content = msg.error
            msg.pipelineSteps = [{ type: 'intent', label: '后台查询', status: 'failed', detail: '任务已过期' }]
            activeAsyncTasks.value.delete(taskId)
            replaceMsgInArray(msg)
            onMessageUpdate.value?.()
            return
          }
          polls++
          await sleep(5000)
          continue
        }

        const data = await res.json()

        if (data.status === 'done') {
          // Success
          msg.columns = data.columns || []
          msg.rows = data.rows || []
          msg.row_count = data.row_count || 0
          msg.sql = data.sql
          msg.chart_type = data.chart_type || 'table'
          msg.execution_time_ms = data.execution_time_ms
          msg.content = '查询成功'
          msg.pipelineSteps = [
            { type: 'intent', label: '后台查询', status: 'done', detail: `查询完成，${data.row_count} 行结果` },
            { type: 'data', label: '执行查询', status: 'done', detail: `返回 ${data.row_count} 行`, duration_ms: data.execution_time_ms },
          ]
          activeAsyncTasks.value.delete(taskId)
          replaceMsgInArray(msg)
          onMessageUpdate.value?.()
          await _saveCurrentConversation()
          await loadConversations()
          return
        } else if (data.status === 'failed') {
          const errMsg = data.error || '查询失败'
          msg.error = errMsg
          msg.content = errMsg
          msg.pipelineSteps = [{ type: 'intent', label: '后台查询', status: 'failed', detail: errMsg }]
          activeAsyncTasks.value.delete(taskId)
          replaceMsgInArray(msg)
          onMessageUpdate.value?.()
          return
        } else if (data.status === 'cancelled') {
          msg.content = '查询已取消'
          msg.pipelineSteps = [{ type: 'intent', label: '后台查询', status: 'failed', detail: '用户已取消' }]
          activeAsyncTasks.value.delete(taskId)
          replaceMsgInArray(msg)
          onMessageUpdate.value?.()
          return
        }
        // status === 'running' or 'pending' — keep polling
        const statusLabel = data.status === 'running' ? '正在执行' : '等待中'
        msg.pipelineSteps = [{ type: 'intent', label: '后台查询', status: 'running', detail: statusLabel }]
        replaceMsgInArray(msg)
        onMessageUpdate.value?.()
      } catch {
        // Network error — retry
      }

      polls++
      await sleep(5000)
    }

    // Timeout
    if (!signal.aborted) {
      msg.error = '查询超时'
      msg.content = msg.error
      msg.pipelineSteps = [{ type: 'intent', label: '后台查询', status: 'failed', detail: '查询超时（10分钟限制）' }]
      activeAsyncTasks.value.delete(taskId)
      replaceMsgInArray(msg)
      onMessageUpdate.value?.()
    }
  }

  async function cancelAsyncQuery(taskId: string): Promise<void> {
    const task = activeAsyncTasks.value.get(taskId)
    if (!task) return

    // Abort local polling first
    task.abort()
    activeAsyncTasks.value.delete(taskId)

    // Notify backend
    try {
      const baseURL = (api.defaults.baseURL || '').replace(/\/$/, '')
      const token = localStorage.getItem('access_token')
      await fetch(`${baseURL}/query/async/${taskId}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${token}` },
      })
    } catch {
      // Non-critical
    }

    // Update message status
    const msg = messages.value.find(m => m.id === task.messageId)
    if (msg) {
      msg.content = '查询已取消'
      msg.pipelineSteps = msg.pipelineSteps?.map(s => ({
        ...s,
        status: 'failed' as const,
        detail: s.status === 'running' ? '用户已取消' : s.detail,
      }))
    }
  }

  function sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms))
  }

  return {
    messages,
    loading,
    currentDatasourceId,
    currentConversationId,
    conversations,
    activeAsyncTasks,
    onMessageUpdate,
    sendQuestion,
    sendAsyncQuestion,
    cancelAsyncQuery,
    clearMessages,
    newConversation,
    loadConversation,
    loadConversations,
    deleteConversation,
  }
})
