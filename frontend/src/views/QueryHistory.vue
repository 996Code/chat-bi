<template>
  <div class="query-history-view">
    <div class="view-header">
      <div class="header-left">
        <el-button text @click="router.push('/')">
          <el-icon><ArrowLeft /></el-icon>
        </el-button>
        <h2>查询历史</h2>
      </div>
      <div class="header-right">
        <el-select v-model="filterDsId" placeholder="全部数据源" clearable style="width: 200px" @change="loadHistory">
          <el-option v-for="ds in datasourceStore.datasources" :key="ds.id" :label="ds.name" :value="ds.id" />
        </el-select>
        <el-input v-model="searchText" placeholder="搜索问题..." clearable style="width: 200px" @input="filterConversations">
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
      </div>
    </div>

    <div class="history-list" v-loading="loading">
      <div v-if="filteredConvs.length === 0 && !loading" class="empty-hint">
        <el-empty description="暂无查询历史" />
      </div>

      <div
        v-for="conv in filteredConvs"
        :key="conv.id"
        class="history-card"
      >
        <div class="card-header">
          <span class="card-title">{{ conv.title || '新对话' }}</span>
          <div class="card-actions">
            <el-button size="small" type="primary" @click="jumpToConversation(conv)">
              <el-icon><ChatDotRound /></el-icon> 打开对话
            </el-button>
            <el-button size="small" text @click="loadConvQueries(conv)">
              <el-icon><View /></el-icon> 查询记录
            </el-button>
            <el-button size="small" text type="danger" @click="deleteConv(conv)">
              <el-icon><Delete /></el-icon>
            </el-button>
          </div>
        </div>
        <div class="card-meta">
          <el-tag size="small" type="info">{{ conv.datasource_name || '未知数据源' }}</el-tag>
          <span>{{ conv.message_count }} 条消息</span>
          <span class="card-time">{{ formatDate(conv.updated_at) }}</span>
        </div>
        <div v-if="conv.first_question" class="card-preview">
          {{ conv.first_question }}
        </div>
      </div>
    </div>

    <!-- Query records dialog with trace -->
    <el-dialog v-model="showQueries" :title="selectedConv?.title || '查询记录'" width="800px">
      <div v-if="selectedConv" class="query-list">
        <div v-if="queryMessages.length === 0" class="empty-msg">暂无查询记录</div>
        <div
          v-for="(msg, idx) in queryMessages"
          :key="msg.id"
          class="query-card"
        >
          <div class="query-card-header">
            <span class="query-num">#{{ idx + 1 }}</span>
            <span class="query-question">{{ msg.content }}</span>
            <div class="query-actions">
              <el-button size="small" type="primary" link @click="showTrace(msg)" :disabled="!msg.pipelineSteps?.length">
                完整流程
              </el-button>
            </div>
          </div>
          <div class="query-card-body" v-if="msg.sql">
            <div class="query-sql"><pre>{{ msg.sql }}</pre></div>
            <div class="query-meta">
              <el-tag v-if="msg.error" type="danger" size="small">失败</el-tag>
              <el-tag v-else type="success" size="small">成功 {{ msg.row_count }} 行</el-tag>
              <span v-if="msg.execution_time_ms">{{ msg.execution_time_ms }}ms</span>
            </div>
          </div>
          <div class="query-card-body" v-else-if="msg.error">
            <div class="query-error">{{ msg.error }}</div>
          </div>
        </div>
      </div>
    </el-dialog>

    <!-- Pipeline trace dialog -->
    <PipelineTraceDialog v-model="showTraceDialog" :steps="traceSteps" title="查询执行记录" />

    <!-- Jump to conversation confirmation -->
    <el-dialog v-model="showJumpConfirm" title="跳转确认" width="480px">
      <p>将跳转到对话：<b>{{ jumpTarget?.title }}</b></p>
      <p class="text-muted">当前对话的未保存内容将丢失</p>
      <template #footer>
        <el-button @click="showJumpConfirm = false">取消</el-button>
        <el-button type="primary" @click="doJump">确认跳转</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, Search, Delete, View, ChatDotRound } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useDatasourceStore } from '@/stores/datasourceStore'
import { useChatStore } from '@/stores/chatStore'
import api from '@/api'
import PipelineTraceDialog from '@/components/PipelineTraceDialog.vue'

const router = useRouter()
const datasourceStore = useDatasourceStore()
const chatStore = useChatStore()

const loading = ref(false)
const filterDsId = ref('')
const searchText = ref('')
const conversations = ref<any[]>([])
const showQueries = ref(false)
const selectedConv = ref<any>(null)
const queryMessages = ref<any[]>([])
const showTraceDialog = ref(false)
const traceSteps = ref<any[]>([])
const showJumpConfirm = ref(false)
const jumpTarget = ref<any>(null)

const filteredConvs = computed(() => {
  let list = conversations.value
  if (filterDsId.value) {
    list = list.filter(c => c.datasource_id === filterDsId.value)
  }
  if (searchText.value) {
    const q = searchText.value.toLowerCase()
    list = list.filter(c =>
      c.title?.toLowerCase().includes(q) || c.first_question?.toLowerCase().includes(q)
    )
  }
  return list
})

function filterConversations() {
  // Computed handles it
}

async function loadHistory() {
  loading.value = true
  try {
    const params: Record<string, string> = {}
    if (filterDsId.value) params.datasource_id = filterDsId.value
    const res = await api.get('/conversations', { params })
    // Enrich with datasource name and first question
    const dsMap: Record<string, string> = {}
    datasourceStore.datasources.forEach(ds => { dsMap[ds.id] = ds.name })
    conversations.value = res.data.map((conv: any) => {
      let firstQuestion = ''
      try {
        // The API doesn't return messages, so we store a preview in title or metadata
        // For now, extract from title if possible
        firstQuestion = conv.title?.startsWith('新对话') ? '' : conv.title || ''
      } catch { /* ignore */ }
      return {
        ...conv,
        datasource_name: dsMap[conv.datasource_id] || '未知',
        first_question: firstQuestion,
      }
    })
  } catch {
    ElMessage.error('加载查询历史失败')
  } finally {
    loading.value = false
  }
}

async function loadConvQueries(conv: any) {
  try {
    const res = await api.get(`/conversations/${conv.id}`)
    selectedConv.value = res.data
    // Extract assistant messages with query info
    queryMessages.value = (res.data.messages || [])
      .filter((m: any) => m.role === 'assistant' && (m.sql || m.error || m.pipelineSteps?.length))
    showQueries.value = true
  } catch {
    ElMessage.error('加载查询记录失败')
  }
}

function showTrace(msg: any) {
  traceSteps.value = msg.pipelineSteps || []
  showTraceDialog.value = true
}

function jumpToConversation(conv: any) {
  jumpTarget.value = conv
  showJumpConfirm.value = true
}

async function doJump() {
  showJumpConfirm.value = false
  try {
    await chatStore.loadConversation(jumpTarget.value.id)
    router.push('/')
  } catch {
    ElMessage.error('加载对话失败')
  }
}

async function deleteConv(conv: any) {
  try {
    await ElMessageBox.confirm('确定要删除这个对话吗？', '确认删除', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await api.delete(`/conversations/${conv.id}`)
    ElMessage.success('已删除')
    await loadHistory()
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
  await datasourceStore.list()
  await loadHistory()
})
</script>

<style scoped>
.query-history-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #f8fafc;
}

.view-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  background: white;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-left h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
}

.header-right {
  display: flex;
  gap: 8px;
  align-items: center;
}

.history-list {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.empty-hint {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.history-card {
  background: white;
  border-radius: 8px;
  padding: 14px 16px;
  border: 1px solid #e2e8f0;
  transition: box-shadow 0.15s;
}

.history-card:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  margin-right: 8px;
}

.card-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.card-meta {
  display: flex;
  gap: 12px;
  align-items: center;
  font-size: 12px;
  color: #64748b;
  margin-bottom: 6px;
}

.card-time {
  margin-left: auto;
}

.card-preview {
  font-size: 13px;
  color: #94a3b8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.text-muted {
  color: #94a3b8;
  font-size: 13px;
}

/* Query records dialog */
.query-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 60vh;
  overflow-y: auto;
}

.query-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
}

.query-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
}

.query-num {
  font-weight: 700;
  color: #6366f1;
  flex-shrink: 0;
}

.query-question {
  flex: 1;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.query-actions {
  flex-shrink: 0;
}

.query-card-body {
  padding: 10px 14px;
}

.query-sql pre {
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

.query-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
}

.query-error {
  color: #f56c6c;
  font-size: 13px;
}

.empty-msg {
  text-align: center;
  color: #94a3b8;
  padding: 20px;
}
</style>
