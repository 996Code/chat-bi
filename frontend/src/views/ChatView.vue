<template>
  <div class="chat-view">
    <!-- Header -->
    <div class="chat-header">
      <div class="header-left">
        <el-button text class="sidebar-toggle" @click="showConvSidebar = !showConvSidebar">
          <el-icon :size="18"><ChatLineSquare /></el-icon>
        </el-button>
        <el-select v-model="chatStore.currentDatasourceId" placeholder="选择数据源" style="width: 200px" @change="onDatasourceChange">
          <el-option v-for="ds in datasourceStore.datasources" :key="ds.id" :label="ds.name" :value="ds.id" />
        </el-select>
      </div>
      <div class="header-right">
        <el-popover trigger="click" placement="bottom-end" :width="180">
          <template #reference>
            <span class="nav-dropdown-trigger">
              <el-icon><UserFilled /></el-icon> {{ authStore.user?.email }}
              <el-icon><ArrowDown /></el-icon>
            </span>
          </template>
          <div class="header-menu">
            <div class="header-menu-item" @click="goTo('/datasources')">
              <el-icon><Connection /></el-icon> 数据源管理
            </div>
            <div class="header-menu-item" @click="goTo('/data-models')">
              <el-icon><Grid /></el-icon> 数据模型
            </div>
            <div class="header-menu-divider"></div>
            <div class="header-menu-item" @click="goTo('/monitoring')">
              <el-icon><Monitor /></el-icon> 监控面板
            </div>
            <div class="header-menu-item" @click="goTo('/evaluation')">
              <el-icon><Histogram /></el-icon> 评估测试
            </div>
            <div class="header-menu-divider"></div>
            <div class="header-menu-item" @click="goTo('/query-history')">
              <el-icon><Clock /></el-icon> 查询历史
            </div>
            <div class="header-menu-item" @click="goTo('/dashboards')">
              <el-icon><DataBoard /></el-icon> 看板
            </div>
            <div class="header-menu-item" @click="showPipelineDialog = true">
              <el-icon><Help /></el-icon> 查询流程
            </div>
            <div class="header-menu-divider"></div>
            <div class="header-menu-item danger" @click="handleLogout">
              <el-icon><SwitchButton /></el-icon> 退出登录
            </div>
          </div>
        </el-popover>
      </div>
    </div>

    <!-- Body: conversation sidebar + chat + dictionary -->
    <div class="chat-body">
      <!-- Conversation Sidebar -->
      <div v-if="showConvSidebar" class="conv-sidebar">
        <div class="conv-sidebar-header">
          <span>对话历史</span>
          <el-button size="small" type="primary" @click="handleNewConversation">
            <el-icon><Plus /></el-icon> 新建
          </el-button>
        </div>
        <div class="conv-list">
          <div
            v-for="conv in chatStore.conversations"
            :key="conv.id"
            :class="['conv-item', { active: chatStore.currentConversationId === conv.id }]"
            @click="handleLoadConversation(conv)"
          >
            <div class="conv-item-content">
              <div class="conv-title">{{ conv.title || '新对话' }}</div>
              <div class="conv-meta">
                {{ conv.message_count }} 条消息 · {{ formatDate(conv.updated_at) }}
              </div>
            </div>
            <el-button
              size="small"
              text
              class="conv-delete"
              @click.stop="handleDeleteConversation(conv)"
            >
              <el-icon><Delete /></el-icon>
            </el-button>
          </div>
          <div v-if="chatStore.conversations.length === 0" class="conv-empty">
            暂无对话历史
          </div>
        </div>
      </div>

      <!-- Main chat area -->
      <div class="chat-main">
        <!-- Messages -->
        <div class="messages" ref="messagesRef">
          <div v-if="chatStore.messages.length === 0" class="empty-state">
            <el-empty description="开始你的数据查询之旅吧！">
              <template #image>
                <el-icon :size="80" color="#c0c4cc"><ChatDotRound /></el-icon>
              </template>
            </el-empty>
            <div class="suggestions">
              <el-button v-for="q in suggestedQuestions" :key="q" text @click="askSuggestion(q)">{{ q }}</el-button>
              <el-button v-if="suggestedQuestions.length === 0" text @click="askSuggestion('各VIP等级的用户数量')">各VIP等级的用户数量</el-button>
            </div>
          </div>

          <div v-for="msg in chatStore.messages" :key="msg.id" :class="['message', msg.role]">
            <div class="message-content">
              <div class="message-text">{{ msg.content }}</div>

              <!-- Pipeline Steps (Dify-like) -->
              <div v-if="msg.pipelineSteps && msg.pipelineSteps.length > 0" class="pipeline">
                <div class="pipeline-header-row">
                  <span class="pipeline-title">查询流程</span>
                  <div class="pipeline-header-actions">
                    <!-- Async cancel button -->
                    <el-button v-if="isAsyncRunning(msg.id)" size="small" type="danger" text @click="cancelTask(msg.id)">
                      <el-icon><Close /></el-icon> 取消查询
                    </el-button>
                    <el-button size="small" text @click="pipelineTraceSteps = msg.pipelineSteps || []; showPipelineDialog = true">
                      <el-icon><QuestionFilled /></el-icon> 完整流程
                    </el-button>
                  </div>
                </div>
                <div
                  v-for="(step, idx) in msg.pipelineSteps"
                  :key="idx"
                  :class="['pipeline-step', step.status]"
                >
                  <div class="step-icon">
                    <el-icon v-if="step.status === 'running'" class="is-loading"><Loading /></el-icon>
                    <el-icon v-else-if="step.status === 'done'" class="step-done"><CircleCheck /></el-icon>
                    <el-icon v-else class="step-failed"><CircleClose /></el-icon>
                  </div>
                  <div class="step-content">
                    <div class="step-label">{{ step.label }}</div>
                    <div v-if="step.detail" class="step-detail">
                      <!-- SQL detail -->
                      <pre v-if="step.type === 'sql' && msg.sql" class="sql-inline">{{ msg.sql }}</pre>
                      <span v-else>{{ step.detail }}</span>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Error (without pipeline steps) -->
              <div v-if="msg.error && (!msg.pipelineSteps || msg.pipelineSteps.length === 0)" class="error-text">
                {{ msg.error }}
              </div>

              <!-- Data table / chart -->
              <div v-if="msg.rows && msg.rows.length > 0" class="data-table">
                <ChartRenderer
                  :ref="(el: any) => { if (el) chartRendererMap[msg.id] = el }"
                  :chart-type="msg.chart_type || 'table'"
                  :columns="msg.columns || []"
                  :rows="msg.rows"
                />
                <div class="table-footer">
                  <span>共 {{ msg.row_count }} 条结果<span v-if="msg.execution_time_ms">（耗时 {{ msg.execution_time_ms }}ms）</span></span>
                  <div class="table-footer-actions">
                    <el-button size="small" text class="save-dashboard-btn" @click="openSaveToDashboard(msg)">
                      <el-icon><DataBoard /></el-icon> 保存到看板
                    </el-button>
                    <el-button size="small" text class="export-btn" @click="exportExcel(msg)">
                      <el-icon><Download /></el-icon> 导出 Excel
                    </el-button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Input -->
        <div class="input-area">
          <div class="input-controls">
            <el-checkbox v-model="useAsyncMode" size="small" border>
              <el-icon><Clock /></el-icon> 后台运行（适合大查询）
            </el-checkbox>
            <span v-if="activeAsyncCount > 0" class="async-badge">
              <el-icon class="is-loading"><Loading /></el-icon> {{ activeAsyncCount }} 个查询正在运行
            </span>
          </div>
          <el-input
            v-model="inputText"
            placeholder="用自然语言提问，例如：各VIP等级的用户数量"
            size="large"
            @keyup.enter="handleSend"
            :disabled="chatStore.loading || !chatStore.currentDatasourceId"
          >
            <template #append>
              <el-button type="primary" @click="handleSend" :disabled="chatStore.loading || !inputText.trim() || !chatStore.currentDatasourceId">
                发送
              </el-button>
            </template>
          </el-input>
          <div v-if="!chatStore.currentDatasourceId" class="input-hint">
            请先在上方选择数据源
          </div>
        </div>
      </div>

          </div>

    <!-- First Use Guide -->
    <FirstUseGuide />

    <!-- Pipeline Dialog -->
    <PipelineTraceDialog v-model="showPipelineDialog" :steps="pipelineTraceSteps" />

    <!-- Save to Dashboard Dialog -->
    <el-dialog v-model="showSaveDialog" title="保存到看板" width="400px">
      <div v-if="dashboardsForSave.length === 0" class="save-empty">
        <p>还没有看板，请先创建一个</p>
        <el-button type="primary" size="small" @click="showCreateDashDialog = true">新建看板</el-button>
      </div>
      <el-select v-else v-model="selectedDashboardId" placeholder="选择看板" style="width: 100%">
        <el-option v-for="d in dashboardsForSave" :key="d.id" :label="d.name" :value="d.id" />
      </el-select>
      <template #footer>
        <el-button @click="showSaveDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!selectedDashboardId" @click="saveToDashboard">保存</el-button>
      </template>
    </el-dialog>

    <!-- Create Dashboard Dialog (from save flow) -->
    <el-dialog v-model="showCreateDashDialog" title="新建看板" width="400px">
      <el-input v-model="newDashName" placeholder="输入看板名称" @keyup.enter="createDashFromSave" />
      <template #footer>
        <el-button @click="showCreateDashDialog = false">取消</el-button>
        <el-button type="primary" @click="createDashFromSave">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '@/stores/chatStore'
import { useDatasourceStore } from '@/stores/datasourceStore'
import { useAuthStore } from '@/stores/authStore'
import { ChatDotRound, ChatLineSquare, Loading, CircleCheck, CircleClose, Plus, Delete, QuestionFilled, Download, UserFilled, ArrowDown, Connection, Grid, Monitor, Histogram, Clock, Help, SwitchButton, Close, View, DataBoard } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import ChartRenderer from '@/components/ChartRenderer.vue'
import FirstUseGuide from '@/components/FirstUseGuide.vue'
import PipelineTraceDialog from '@/components/PipelineTraceDialog.vue'

const router = useRouter()
const chatStore = useChatStore()
const datasourceStore = useDatasourceStore()
const authStore = useAuthStore()

const inputText = ref('')
const messagesRef = ref<HTMLElement>()
const showConvSidebar = ref(true)
const showPipelineDialog = ref(false)
const pipelineTraceSteps = ref<any[]>([])
const suggestedQuestions = ref<string[]>([])
const chartRendererMap = ref<Record<string, any>>({})

// Save to dashboard
const showSaveDialog = ref(false)
const showCreateDashDialog = ref(false)
const dashboardsForSave = ref<any[]>([])
const selectedDashboardId = ref<string>('')
const newDashName = ref('')
const pendingSaveMsg = ref<any>(null)

// Async query mode
const useAsyncMode = ref(false)

const activeAsyncCount = computed(() => {
  return chatStore.activeAsyncTasks.size
})

function findTaskIdByMessageId(messageId: string): string | null {
  for (const [taskId, task] of chatStore.activeAsyncTasks) {
    if (task.messageId === messageId) return taskId
  }
  return null
}

function isAsyncRunning(messageId: string): boolean {
  return !!findTaskIdByMessageId(messageId)
}

function cancelTask(messageId: string) {
  const taskId = findTaskIdByMessageId(messageId)
  if (taskId) {
    chatStore.cancelAsyncQuery(taskId)
  }
}

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || chatStore.loading) return
  inputText.value = ''

  if (useAsyncMode.value) {
    try {
      await chatStore.sendAsyncQuestion(text)
    } catch (e: any) {
      ElMessage.error(e.message || '提交失败')
    }
  } else {
    await chatStore.sendQuestion(text)
  }
  await nextTick()
  scrollToBottom()
}

function askSuggestion(text: string) {
  inputText.value = text
  handleSend()
}

function scrollToBottom() {
  if (messagesRef.value) {
    messagesRef.value.scrollTop = messagesRef.value.scrollHeight
  }
}

function copySql(sql: string) {
  navigator.clipboard.writeText(sql)
  ElMessage.success('SQL 已复制')
}

async function exportExcel(msg: any) {
  const ExcelJS = await import('exceljs')
  const columns: string[] = msg.columns || []
  const rows: Record<string, any>[] = msg.rows || []

  const workbook = new ExcelJS.Workbook()
  const ws1 = workbook.addWorksheet('数据')

  // Header row with styling
  const headerRow = ws1.addRow(columns)
  headerRow.eachCell((cell: any) => {
    cell.font = { bold: true, color: { argb: 'FFFFFFFF' } }
    cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF4472C4' } }
    cell.alignment = { horizontal: 'center' }
    cell.border = {
      top: { style: 'thin' }, bottom: { style: 'thin' },
      left: { style: 'thin' }, right: { style: 'thin' },
    }
  })

  // Data rows
  for (const r of rows) {
    ws1.addRow(columns.map((c: string) => r[c] ?? ''))
  }

  // Auto-fit column widths
  ws1.columns.forEach((col: any, i: number) => {
    const maxLen = Math.max(
      columns[i].length * 2,
      ...rows.slice(0, 50).map((r: Record<string, any>) => String(r[columns[i]] ?? '').length)
    )
    col.width = Math.min(Math.max(maxLen + 2, 8), 40)
  })

  // Add chart screenshot as second sheet if available
  const chartType = msg.chart_type || 'table'
  if (chartType !== 'table' && chartType !== 'metric') {
    await nextTick()
    const renderer = chartRendererMap.value[msg.id]
    const dataURL = renderer?.getChartDataURL?.()
    if (dataURL) {
      const ws2 = workbook.addWorksheet('图表')
      const base64 = dataURL.split(',')[1]
      const imageId = workbook.addImage({ base64, extension: 'png' })
      // Use actual chart dimensions from the DOM
      const chartEl = renderer?.$el?.querySelector?.('.echarts-wrapper')
      const width = chartEl?.offsetWidth || 600
      const height = chartEl?.offsetHeight || 320
      ws2.addImage(imageId, {
        tl: { col: 0, row: 0 },
        ext: { width: width * 1.2, height: height * 1.2 },
      })
    }
  }

  // Download
  const buffer = await workbook.xlsx.writeBuffer()
  const blob = new Blob([buffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  const now = new Date()
  const ts = `${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}${String(now.getDate()).padStart(2,'0')}_${String(now.getHours()).padStart(2,'0')}${String(now.getMinutes()).padStart(2,'0')}${String(now.getSeconds()).padStart(2,'0')}`
  link.download = `chat-bi导出_${ts}.xlsx`
  link.click()
  URL.revokeObjectURL(link.href)
  ElMessage.success('导出成功')
}

function handleLogout() {
  authStore.logout()
  router.push('/login')
}

function goTo(path: string) {
  router.push(path)
}

function onDatasourceChange() {
  chatStore.clearMessages()
  loadSuggestedQuestions()
}

async function loadSuggestedQuestions() {
  if (!chatStore.currentDatasourceId) {
    suggestedQuestions.value = []
    return
  }
  try {
    const res = await api.get(`/data-models/${chatStore.currentDatasourceId}`)
    const config = res.data.config
    suggestedQuestions.value = config.suggested_questions || []
  } catch {
    suggestedQuestions.value = []
  }
}

function handleNewConversation() {
  chatStore.newConversation()
}

async function handleLoadConversation(conv: any) {
  if (conv.id === chatStore.currentConversationId) return
  await chatStore.loadConversation(conv.id)
  await nextTick()
  scrollToBottom()
}

async function handleDeleteConversation(conv: any) {
  try {
    await ElMessageBox.confirm('确定要删除这个对话吗？', '确认删除', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await chatStore.deleteConversation(conv.id)
    ElMessage.success('已删除')
  } catch {
    // User cancelled
  }
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin} 分钟前`
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour} 小时前`
  const diffDay = Math.floor(diffHour / 24)
  if (diffDay < 7) return `${diffDay} 天前`
  return d.toLocaleDateString('zh-CN')
}

async function openSaveToDashboard(msg: any) {
  pendingSaveMsg.value = msg
  try {
    const res = await api.get('/dashboards')
    dashboardsForSave.value = res.data.data || []
    if (dashboardsForSave.value.length === 0) {
      selectedDashboardId.value = ''
    } else {
      selectedDashboardId.value = dashboardsForSave.value[0].id
    }
    showSaveDialog.value = true
  } catch {
    ElMessage.error('获取看板列表失败')
  }
}

async function createDashFromSave() {
  if (!newDashName.value.trim()) {
    ElMessage.warning('请输入看板名称')
    return
  }
  try {
    const res = await api.post('/dashboards', { name: newDashName.value.trim() })
    dashboardsForSave.value.push(res.data)
    selectedDashboardId.value = res.data.id
    newDashName.value = ''
    showCreateDashDialog.value = false
    ElMessage.success('看板创建成功')
  } catch {
    ElMessage.error('创建看板失败')
  }
}

async function saveToDashboard() {
  if (!selectedDashboardId.value || !pendingSaveMsg.value) return
  const msg = pendingSaveMsg.value
  try {
    await api.post(`/dashboards/${selectedDashboardId.value}/widgets`, {
      question: msg.content,
      datasource_id: chatStore.currentDatasourceId,
      query_sql: msg.sql || '',
      chart_type: msg.chart_type || 'table',
      columns: msg.columns || [],
      rows: msg.rows || [],
      row_count: msg.row_count || 0,
      position_x: 0,
      position_y: 0,
      width: 1,
      height: 1,
    })
    showSaveDialog.value = false
    pendingSaveMsg.value = null
    ElMessage.success('已保存到看板')
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail?.message || e?.message || '未知错误'))
  }
}

onMounted(async () => {
  authStore.initFromStorage()
  await datasourceStore.list()
  if (datasourceStore.datasources.length > 0 && !chatStore.currentDatasourceId) {
    chatStore.currentDatasourceId = datasourceStore.datasources[0].id
  }
  // Load conversation list and suggested questions
  await chatStore.loadConversations()
  await loadSuggestedQuestions()

  // Handle jump from slow query trace
  const convId = router.currentRoute.value.query.conv as string
  if (convId) {
    const conv = chatStore.conversations.find(c => c.id === convId)
    if (conv) {
      await handleLoadConversation(conv)
    }
  }

  // Auto-scroll on SSE updates
  chatStore.onMessageUpdate = () => {
    nextTick(() => scrollToBottom())
  }
})

// Cleanup: abort all async queries when leaving the page
onUnmounted(() => {
  for (const taskId of chatStore.activeAsyncTasks.keys()) {
    chatStore.cancelAsyncQuery(taskId)
  }
})
</script>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

.chat-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* Conversation Sidebar */
.conv-sidebar {
  width: 300px;
  min-width: 300px;
  border-right: 1px solid #e4e7ed;
  background: #fafafa;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.conv-sidebar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  font-weight: 600;
  font-size: 14px;
  color: #303133;
  border-bottom: 1px solid #e4e7ed;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}

.conv-item {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  cursor: pointer;
  transition: background 0.15s;
  gap: 8px;
}

.conv-item:hover {
  background: #e8e8e8;
}

.conv-item.active {
  background: #e8edf3;
  border-right: 3px solid #667eea;
}

.conv-item-content {
  flex: 1;
  min-width: 0;
}

.conv-title {
  font-size: 13px;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-meta {
  font-size: 11px;
  color: #909399;
  margin-top: 2px;
}

.conv-delete {
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s;
  color: #909399;
}

.conv-item:hover .conv-delete {
  opacity: 1;
}

.conv-delete:hover {
  color: #f56c6c;
}

.conv-empty {
  padding: 20px 16px;
  text-align: center;
  color: #909399;
  font-size: 13px;
}

.sidebar-toggle {
  padding: 4px;
}

/* Main chat area */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-bottom: 1px solid #e4e7ed;
  background: white;
  gap: 12px;
}

.header-left,
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: nowrap;
}

.header-left {
  flex: 1;
  min-width: 0;
}

.user-email {
  color: #606266;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 150px;
}

.nav-dropdown-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  color: #606266;
  font-size: 13px;
  padding: 0 10px;
  border-radius: 4px;
  transition: color 0.2s;
  user-select: none;
  max-width: 200px;
}
.nav-dropdown-trigger:hover {
  color: #409eff;
}

.admin-menu-list, .header-menu {
  display: flex;
  flex-direction: column;
  gap: 0;
  padding: 4px;
}
.admin-menu-item, .header-menu-item {
  padding: 7px 8px;
  font-size: 14px;
  color: #606266;
  cursor: pointer;
  border-radius: 4px;
  transition: background 0.15s;
  text-align: left;
  width: 100%;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 8px;
}
.admin-menu-item:hover, .header-menu-item:hover {
  background: #f0f2f5;
  color: #409eff;
}
.header-menu-item .el-icon {
  flex-shrink: 0;
  font-size: 15px;
}
.header-menu-item.danger {
  color: #ef4444;
}
.header-menu-item.danger:hover {
  color: #dc2626;
  background: #fef2f2;
}
.header-menu-divider {
  height: 1px;
  background: #e4e7ed;
  margin: 4px 4px;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background: #f5f7fa;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #909399;
}

.suggestions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: 16px;
}

.message {
  display: flex;
  margin-bottom: 16px;
}

.message.user {
  justify-content: flex-end;
}

.message.assistant {
  justify-content: flex-start;
}

.message-content {
  max-width: 80%;
  padding: 12px 16px;
  border-radius: 12px;
  background: white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.message.user .message-content {
  background: #667eea;
  color: white;
}

.message.assistant .message-content {
  width: 80%;
}

/* Metric card: small centered card */
.message.assistant .message-content:has(.metric-card) {
  width: auto !important;
  min-width: 260px;
  max-width: 380px;
  text-align: center;
}

.message-text {
  margin-bottom: 8px;
  line-height: 1.5;
}

/* Pipeline Steps - Dify style */
.pipeline {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 10px;
  padding: 10px 12px;
  background: #f8f9fa;
  border-radius: 8px;
  border-left: 3px solid #e0e0e0;
}

.pipeline-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.pipeline-title {
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
}

.pipeline-step {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
  transition: all 0.3s ease;
}

.pipeline-step.running {
  opacity: 1;
}

.pipeline-step.done {
  opacity: 1;
}

.pipeline-step.failed {
  opacity: 0.7;
}

.step-icon {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}

.step-done {
  color: #67c23a;
  font-size: 16px;
}

.step-failed {
  color: #f56c6c;
  font-size: 16px;
}

.step-content {
  flex: 1;
  min-width: 0;
}

.step-label {
  font-weight: 500;
  color: #303133;
  line-height: 1.4;
}

.step-detail {
  color: #909399;
  font-size: 12px;
  margin-top: 2px;
  line-height: 1.4;
  word-break: break-all;
}

.sql-inline {
  margin: 4px 0 0;
  padding: 6px 10px;
  background: #1e1e1e;
  color: #a5d6ff;
  font-size: 12px;
  font-family: 'SF Mono', 'Fira Code', monospace;
  border-radius: 6px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.5;
}

.error-text {
  color: #f56c6c;
  font-size: 14px;
  margin-top: 4px;
}

.data-table {
  margin-top: 8px;
  width: 100%;
}

.table-footer {
  margin-top: 8px;
  color: #909399;
  font-size: 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.table-footer-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.save-dashboard-btn {
  color: #67c23a;
  font-size: 12px;
}

.export-btn {
  color: #409eff;
  font-size: 12px;
}

.trace-id {
  color: #c0c4cc;
  font-family: 'SF Mono', monospace;
  font-size: 11px;
}

.loading-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #909399;
}

.input-area {
  padding: 12px 20px 16px;
  border-top: 1px solid #e4e7ed;
  background: white;
}

.input-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.async-badge {
  font-size: 12px;
  color: #e6a23c;
  display: flex;
  align-items: center;
  gap: 4px;
}

.pipeline-header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.input-hint {
  margin-top: 8px;
  color: #909399;
  font-size: 12px;
  text-align: center;
}

.save-empty {
  text-align: center;
  padding: 20px 0;
  color: #909399;
}

.save-empty p {
  margin-bottom: 12px;
}

</style>
