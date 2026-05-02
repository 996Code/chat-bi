<template>
  <div class="chat-view">
    <!-- Header -->
    <div class="chat-header">
      <div class="header-left">
        <el-select v-model="chatStore.currentDatasourceId" placeholder="选择数据源" style="width: 200px" @change="onDatasourceChange">
          <el-option v-for="ds in datasourceStore.datasources" :key="ds.id" :label="ds.name" :value="ds.id" />
        </el-select>
        <el-button text @click="router.push('/datasources')">管理数据源</el-button>
      </div>
      <div class="header-right">
        <span class="user-email">{{ authStore.user?.email }}</span>
        <el-button text @click="handleLogout">退出</el-button>
      </div>
    </div>

    <!-- Messages -->
    <div class="messages" ref="messagesRef">
      <div v-if="chatStore.messages.length === 0" class="empty-state">
        <el-empty description="开始你的数据查询之旅吧！">
          <template #image>
            <el-icon :size="80" color="#c0c4cc"><ChatDotRound /></el-icon>
          </template>
        </el-empty>
        <div class="suggestions">
          <el-button text @click="askSuggestion('上个月的销售总额是多少？')">上个月的销售总额是多少？</el-button>
          <el-button text @click="askSuggestion('用户数量统计')">用户数量统计</el-button>
          <el-button text @click="askSuggestion('最近的10条订单')">最近的10条订单</el-button>
        </div>
      </div>

      <div v-for="msg in chatStore.messages" :key="msg.id" :class="['message', msg.role]">
        <div class="message-content">
          <div class="message-text">{{ msg.content }}</div>
          <div v-if="msg.sql" class="sql-block">
            <div class="sql-header">
              <span class="sql-label">生成的 SQL</span>
              <el-button size="small" text @click="copySql(msg.sql!)">复制</el-button>
            </div>
            <pre>{{ msg.sql }}</pre>
          </div>
          <div v-if="msg.error && !msg.rows" class="error-text">
            {{ msg.error }}
          </div>
          <div v-if="msg.rows && msg.rows.length > 0" class="data-table">
            <el-table :data="msg.rows" border size="small" max-height="400">
              <el-table-column v-for="col in msg.columns" :key="col" :prop="col" :label="col" />
            </el-table>
            <div class="table-footer">
              共 {{ msg.row_count }} 条结果
              <span v-if="msg.execution_time_ms">（耗时 {{ msg.execution_time_ms }}ms）</span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="chatStore.loading" class="message assistant">
        <div class="message-content">
          <div class="loading-indicator">
            <el-icon class="is-loading"><Loading /></el-icon>
            <span>正在查询...</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Input -->
    <div class="input-area">
      <el-input
        v-model="inputText"
        placeholder="用自然语言提问，例如：上个月的销售总额是多少？"
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
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '@/stores/chatStore'
import { useDatasourceStore } from '@/stores/datasourceStore'
import { useAuthStore } from '@/stores/authStore'
import { ChatDotRound, Loading } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const router = useRouter()
const chatStore = useChatStore()
const datasourceStore = useDatasourceStore()
const authStore = useAuthStore()

const inputText = ref('')
const messagesRef = ref<HTMLElement>()

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

function handleLogout() {
  authStore.logout()
  router.push('/login')
}

function onDatasourceChange() {
  chatStore.clearMessages()
}

onMounted(async () => {
  authStore.initFromStorage()
  await datasourceStore.list()
  // Auto-select first datasource if available
  if (datasourceStore.datasources.length > 0 && !chatStore.currentDatasourceId) {
    chatStore.currentDatasourceId = datasourceStore.datasources[0].id
  }
})
</script>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-bottom: 1px solid #e4e7ed;
  background: white;
}

.header-left,
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-email {
  color: #606266;
  font-size: 14px;
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
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 12px;
  background: white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.message.user .message-content {
  background: #667eea;
  color: white;
}

.message-text {
  margin-bottom: 8px;
  line-height: 1.5;
}

.sql-block {
  background: #1e1e1e;
  border-radius: 8px;
  padding: 12px;
  margin-top: 8px;
}

.sql-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.sql-label {
  color: #909399;
  font-size: 12px;
}

.sql-block pre {
  margin: 0;
  color: #e0e0e0;
  font-size: 13px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.error-text {
  color: #f56c6c;
  font-size: 14px;
  margin-top: 4px;
}

.data-table {
  margin-top: 8px;
}

.table-footer {
  margin-top: 8px;
  color: #909399;
  font-size: 12px;
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
</style>
