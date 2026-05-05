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
        <el-button text @click="router.push('/datasources')">管理数据源</el-button>
        <el-button text @click="router.push('/data-models')">数据模型</el-button>
      </div>
      <div class="header-right">
        <el-button text @click="pipelineTraceSteps = []; showPipelineDialog = true">
          <el-icon><QuestionFilled /></el-icon> 查询流程
        </el-button>
        <span class="user-email">{{ authStore.user?.email }}</span>
        <el-button text @click="handleLogout">退出</el-button>
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
                  <el-button size="small" text @click="pipelineTraceSteps = msg.pipelineSteps || []; showPipelineDialog = true">
                    <el-icon><QuestionFilled /></el-icon> 完整流程
                  </el-button>
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
                  <el-button size="small" text class="export-btn" @click="exportExcel(msg)">
                    <el-icon><Download /></el-icon> 导出 Excel
                  </el-button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Input -->
        <div class="input-area">
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
    <el-dialog v-model="showPipelineDialog" :title="pipelineTraceSteps.length > 0 ? '查询执行记录' : '查询流程说明'" width="720px">
      <!-- Per-query trace (from message button) -->
      <div v-if="pipelineTraceSteps.length > 0" class="trace-table">
        <div class="trace-header">
          <span>本次查询的完整执行记录</span>
        </div>
        <el-table :data="pipelineTraceSteps" stripe size="small" style="width: 100%">
          <el-table-column label="#" width="50" align="center">
            <template #default="{ $index }">{{ $index + 1 }}</template>
          </el-table-column>
          <el-table-column label="步骤" width="140" prop="label" />
          <el-table-column label="状态" width="80" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.status === 'done'" type="success" size="small">完成</el-tag>
              <el-tag v-else-if="row.status === 'failed'" type="danger" size="small">失败</el-tag>
              <el-tag v-else type="info" size="small">进行中</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="执行详情" min-width="300">
            <template #default="{ row }">
              <div v-if="row.type === 'sql' && row.detail" class="trace-sql">
                <pre>{{ row.detail }}</pre>
              </div>
              <span v-else>{{ row.detail || '-' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <!-- Static flow description (from header button) -->
      <div v-else class="flow-desc">
        <div class="flow-step-list">
          <div class="flow-step-item">
            <div class="flow-step-num">1</div>
            <div class="flow-step-body">
              <div class="flow-step-title">意图识别</div>
              <div class="flow-step-text">判断用户问题是否为数据查询意图</div>
            </div>
          </div>
          <div class="flow-step-item">
            <div class="flow-step-num">2</div>
            <div class="flow-step-body">
              <div class="flow-step-title">Schema 选择</div>
              <div class="flow-step-text">LLM 两步选择：先选相关表，再选相关列，构建精简 Schema 上下文</div>
            </div>
          </div>
          <div class="flow-step-item">
            <div class="flow-step-num">3</div>
            <div class="flow-step-body">
              <div class="flow-step-title">SQL 生成</div>
              <div class="flow-step-text">基于 Schema 上下文和用户问题，生成 SQL 查询语句</div>
            </div>
          </div>
          <div class="flow-step-item">
            <div class="flow-step-num">4</div>
            <div class="flow-step-body">
              <div class="flow-step-title">执行查询</div>
              <div class="flow-step-text">在数据源上执行生成的 SQL，返回查询结果</div>
            </div>
          </div>
          <div class="flow-step-item">
            <div class="flow-step-num">5</div>
            <div class="flow-step-body">
              <div class="flow-step-title">SQL 自愈（失败时）</div>
              <div class="flow-step-text">若执行失败，LLM 分析错误原因并修正 SQL，最多重试 2 轮</div>
            </div>
          </div>
          <div class="flow-step-item">
            <div class="flow-step-num">6</div>
            <div class="flow-step-body">
              <div class="flow-step-title">图表推断</div>
              <div class="flow-step-text">根据返回的列名和数据特征，推荐最佳可视化图表类型</div>
            </div>
          </div>
        </div>
        <div class="flow-note">
          点击查询结果卡片上的「完整流程」按钮，可查看该次查询的实际执行记录。
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '@/stores/chatStore'
import { useDatasourceStore } from '@/stores/datasourceStore'
import { useAuthStore } from '@/stores/authStore'
import { ChatDotRound, ChatLineSquare, Loading, CircleCheck, CircleClose, Plus, Delete, QuestionFilled, Download } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import ChartRenderer from '@/components/ChartRenderer.vue'
import FirstUseGuide from '@/components/FirstUseGuide.vue'

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

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || chatStore.loading) return
  inputText.value = ''
  await chatStore.sendQuestion(text)
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

onMounted(async () => {
  authStore.initFromStorage()
  await datasourceStore.list()
  if (datasourceStore.datasources.length > 0 && !chatStore.currentDatasourceId) {
    chatStore.currentDatasourceId = datasourceStore.datasources[0].id
  }
  // Load conversation list and suggested questions
  await chatStore.loadConversations()
  await loadSuggestedQuestions()
  // Auto-scroll on SSE updates
  chatStore.onMessageUpdate = () => {
    nextTick(() => scrollToBottom())
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
  padding: 16px 20px;
  border-top: 1px solid #e4e7ed;
  background: white;
}

.input-hint {
  margin-top: 8px;
  color: #909399;
  font-size: 12px;
  text-align: center;
}

/* Pipeline trace table */
.trace-table {
  padding: 8px 0;
}

.trace-header {
  margin-bottom: 12px;
  font-size: 14px;
  color: #606266;
}

.trace-sql pre {
  margin: 0;
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

.trace-empty {
  padding: 40px 0;
}

/* Static flow description */
.flow-step-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.flow-step-item {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}

.flow-step-num {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #6366f1;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 14px;
}

.flow-step-body {
  flex: 1;
  padding-top: 4px;
}

.flow-step-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.flow-step-text {
  font-size: 13px;
  color: #909399;
  margin-top: 2px;
  line-height: 1.5;
}

.flow-note {
  margin-top: 20px;
  padding: 10px 14px;
  background: #f0f5ff;
  border-radius: 8px;
  border: 1px solid #d0e0ff;
  font-size: 13px;
  color: #409eff;
}
</style>
