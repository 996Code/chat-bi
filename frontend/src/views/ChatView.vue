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
        <el-button text @click="router.push('/query-history')">查询历史</el-button>
        <el-button text @click="showDict = !showDict">
          {{ showDict ? '收起' : '数据字典' }}
        </el-button>
      </div>
      <div class="header-right">
        <el-button text @click="showPipelineDialog = true">
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
              <el-button text @click="askSuggestion('各VIP等级的用户数量')">各VIP等级的用户数量</el-button>
              <el-button text @click="askSuggestion('订单总金额是多少')">订单总金额是多少</el-button>
              <el-button text @click="askSuggestion('各城市的已发货订单数量')">各城市已发货订单数量</el-button>
              <el-button text @click="askSuggestion('哪个商品分类卖得最好')">哪个商品分类卖得最好</el-button>
            </div>
          </div>

          <div v-for="msg in chatStore.messages" :key="msg.id" :class="['message', msg.role]">
            <div class="message-content">
              <div class="message-text">{{ msg.content }}</div>

              <!-- Pipeline Steps (Dify-like) -->
              <div v-if="msg.pipelineSteps && msg.pipelineSteps.length > 0" class="pipeline">
                <div class="pipeline-header-row">
                  <span class="pipeline-title">查询流程</span>
                  <el-button size="small" text @click="showPipelineDialog = true">
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
                  :chart-type="msg.chart_type || 'table'"
                  :columns="msg.columns || []"
                  :rows="msg.rows"
                />
                <div class="table-footer">
                  共 {{ msg.row_count }} 条结果
                  <span v-if="msg.execution_time_ms">（耗时 {{ msg.execution_time_ms }}ms）</span>
                  <span v-if="msg.traceId" class="trace-id">trace: {{ msg.traceId }}</span>
                </div>
                <div class="feedback-actions">
                  <el-button size="small" text @click="submitFeedback(msg, 'up')">
                    <el-icon><CircleCheckFilled /></el-icon> 有用
                  </el-button>
                  <el-button size="small" text @click="submitFeedback(msg, 'down')">
                    <el-icon><CircleCloseFilled /></el-icon> 不准
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

      <!-- Data Dictionary Sidebar -->
      <DataDictionary
        v-if="showDict"
        :datasource-id="chatStore.currentDatasourceId"
        @close="showDict = false"
      />
    </div>

    <!-- First Use Guide -->
    <FirstUseGuide />

    <!-- Pipeline Flow Dialog -->
    <el-dialog v-model="showPipelineDialog" title="AI 查询流程" width="720px">
      <PipelineVisual ref="pipelineRef" :steps="[]" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '@/stores/chatStore'
import { useDatasourceStore } from '@/stores/datasourceStore'
import { useAuthStore } from '@/stores/authStore'
import { ChatDotRound, ChatLineSquare, Loading, CircleCheckFilled, CircleCloseFilled, CircleCheck, CircleClose, Plus, Delete, QuestionFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import ChartRenderer from '@/components/ChartRenderer.vue'
import DataDictionary from '@/components/DataDictionary.vue'
import FirstUseGuide from '@/components/FirstUseGuide.vue'
import PipelineVisual from '@/components/PipelineVisual.vue'

const router = useRouter()
const chatStore = useChatStore()
const datasourceStore = useDatasourceStore()
const authStore = useAuthStore()

const inputText = ref('')
const messagesRef = ref<HTMLElement>()
const showDict = ref(false)
const showConvSidebar = ref(true)
const showPipelineDialog = ref(false)

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

async function submitFeedback(msg: any, rating: 'up' | 'down') {
  try {
    await api.post('/feedback', {
      query_id: msg.id,
      rating,
    })
    ElMessage.success(rating === 'up' ? '感谢反馈！' : '已记录，我们会持续改进')
  } catch {
    // Don't block UX on feedback failure
  }
}

function handleLogout() {
  authStore.logout()
  router.push('/login')
}

function onDatasourceChange() {
  chatStore.clearMessages()
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
  // Load conversation list
  await chatStore.loadConversations()
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
  gap: 12px;
  align-items: center;
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

.feedback-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
</style>
