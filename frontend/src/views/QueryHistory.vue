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
            <el-button size="small" text @click="loadConversation(conv)">
              <el-icon><View /></el-icon> 查看
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
        <!-- Preview: first user message -->
        <div v-if="conv.first_question" class="card-preview">
          {{ conv.first_question }}
        </div>
      </div>
    </div>

    <!-- Conversation detail dialog -->
    <el-dialog v-model="showDetail" :title="selectedConv?.title || '对话详情'" width="720px">
      <div v-if="selectedConv" class="conv-detail">
        <div class="msg-list">
          <div
            v-for="(msg, idx) in selectedConv.messages"
            :key="idx"
            :class="['msg-item', msg.role]"
          >
            <div class="msg-role">{{ msg.role === 'user' ? '你' : '助手' }}</div>
            <div class="msg-content">
              <div class="msg-text">{{ msg.content }}</div>
              <pre v-if="msg.sql" class="msg-sql">{{ msg.sql }}</pre>
            </div>
          </div>
          <div v-if="!selectedConv.messages?.length" class="empty-msg">
            暂无消息
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, Search, Delete, View } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useDatasourceStore } from '@/stores/datasourceStore'
import api from '@/api'

const router = useRouter()
const datasourceStore = useDatasourceStore()

const loading = ref(false)
const filterDsId = ref('')
const searchText = ref('')
const conversations = ref<any[]>([])
const showDetail = ref(false)
const selectedConv = ref<any>(null)

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

async function loadConversation(conv: any) {
  try {
    const res = await api.get(`/conversations/${conv.id}`)
    selectedConv.value = res.data
    showDetail.value = true
  } catch {
    ElMessage.error('加载对话详情失败')
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

.conv-detail {
  max-height: 60vh;
  overflow-y: auto;
}

.msg-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.msg-item {
  display: flex;
  gap: 12px;
}

.msg-item.user {
  flex-direction: row-reverse;
}

.msg-role {
  font-size: 12px;
  color: #94a3b8;
  flex-shrink: 0;
  padding-top: 4px;
}

.msg-content {
  max-width: 80%;
  padding: 10px 14px;
  border-radius: 10px;
  background: #f1f5f9;
}

.msg-item.user .msg-content {
  background: #6366f1;
  color: white;
}

.msg-text {
  font-size: 14px;
  line-height: 1.5;
}

.msg-sql {
  margin-top: 6px;
  padding: 6px 10px;
  background: #1e1e1e;
  color: #a5d6ff;
  font-size: 12px;
  font-family: 'SF Mono', monospace;
  border-radius: 6px;
  overflow-x: auto;
  white-space: pre-wrap;
}

.empty-msg {
  text-align: center;
  color: #94a3b8;
  padding: 20px;
}
</style>
