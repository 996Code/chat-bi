<template>
  <div class="history-view">
    <div class="page-header">
      <el-button :icon="ArrowLeft" text @click="$router.push('/chat')">返回</el-button>
      <span class="title">查询历史 & 审计</span>
      <el-radio-group v-model="activeTab" size="small" style="margin-left: auto">
        <el-radio-button value="audit">审计日志</el-radio-button>
        <el-radio-button value="slow">慢查询 🔥</el-radio-button>
        <el-radio-button value="conversations">对话历史</el-radio-button>
      </el-radio-group>
    </div>

    <!-- 审计日志 -->
    <div v-if="activeTab === 'audit'" v-loading="loading">
      <el-table :data="auditLogs" size="small" border>
        <el-table-column prop="created_at" label="时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="user_id" label="用户" width="100" />
        <el-table-column prop="resource_type" label="资源" width="100" />
        <el-table-column prop="action" label="操作" width="80" />
        <el-table-column prop="status" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.is_slow" type="danger" size="small">🔥 {{ row.duration_ms }}ms</el-tag>
            <span v-else-if="row.duration_ms" style="color:#909399;font-size:0.8em">{{ row.duration_ms }}ms</span>
            <span v-else style="color:#c0c4cc">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="sql_text" label="SQL / 错误" min-width="200">
          <template #default="{ row }">
            <code style="font-size:0.8em">{{ (row.sql_text || row.error_message || '').slice(0, 80) }}</code>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- DSO-07: 慢查询列表 -->
    <div v-if="activeTab === 'slow'" v-loading="loading">
      <el-alert v-if="!slowQueries.length && !loading" type="info" :closable="false"
        title="暂无慢查询" description="超过阈值 (默认 10s) 的查询会显示在这里" show-icon />
      <el-table :data="slowQueries" size="small" border>
        <el-table-column prop="created_at" label="时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="耗时" width="110">
          <template #default="{ row }">
            <el-tag type="danger" size="small">🔥 {{ row.duration_ms }}ms ({{ (row.duration_ms / 1000).toFixed(1) }}s)</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="user_id" label="用户" width="100" />
        <el-table-column label="SQL" min-width="300">
          <template #default="{ row }"><code style="font-size:0.8em">{{ row.sql_text }}</code></template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 对话历史 -->
    <div v-if="activeTab === 'conversations'" v-loading="loading">
      <el-table
        :data="conversations" size="small" border
        highlight-current-row
        @row-click="openConversationDetail"
        row-class-name="conv-row"
      >
        <el-table-column prop="title" label="标题" min-width="160">
          <template #default="{ row }">{{ row.title || (row.conversation_id || '').slice(0, 8) }}</template>
        </el-table-column>
        <el-table-column prop="turn_count" label="轮次" width="70" />
        <el-table-column label="涉及表" min-width="140">
          <template #default="{ row }">{{ (row.last_tables || []).join(', ') || '—' }}</template>
        </el-table-column>
        <el-table-column label="Token" width="90">
          <template #default="{ row }">
            <span v-if="row.total_tokens > 0" class="token-badge">{{ formatTokens(row.total_tokens) }}</span>
            <span v-else style="color:#c0c4cc">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="timestamp" label="时间" width="160">
          <template #default="{ row }">{{ formatTime(row.timestamp) }}</template>
        </el-table-column>
        <el-table-column label="" width="60" align="center">
          <template #default>
            <el-button text size="small" type="primary">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 对话详情抽屉 (共用组件) -->
    <ConversationDetailDrawer
      v-model="showDetail"
      :conversation-id="detailConvId"
      :title="detailTitle"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { observability } from '@/api'
import { extractErrorDetail } from '@/utils/error'
import ConversationDetailDrawer from '@/components/ConversationDetailDrawer.vue'

const activeTab = ref('audit')
const loading = ref(false)
const auditLogs = ref<any[]>([])
const slowQueries = ref<any[]>([])
const conversations = ref<any[]>([])

// 对话详情
const showDetail = ref(false)
const detailConvId = ref('')
const detailTitle = ref('')

function formatTime(iso: string): string {
  if (!iso) return ''
  try { return new Date(iso).toLocaleString('zh-CN') } catch { return iso }
}

function formatTokens(n: number): string {
  if (!n) return '0'
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

async function loadAudit() {
  loading.value = true
  try {
    const { data } = await observability.auditLogs({ limit: 50 })
    auditLogs.value = data
  } catch (e: any) {
    ElMessage.error('加载审计日志失败: ' + (extractErrorDetail(e)))
  } finally { loading.value = false }
}

async function loadSlow() {
  loading.value = true
  try {
    const { data } = await observability.slowQueries(50)
    slowQueries.value = data
  } catch (e: any) {
    ElMessage.error('加载慢查询失败: ' + (extractErrorDetail(e)))
  } finally { loading.value = false }
}

async function loadConversations() {
  loading.value = true
  try {
    const { data } = await observability.conversations()
    conversations.value = data
  } catch (e: any) {
    ElMessage.error('加载对话列表失败: ' + (extractErrorDetail(e)))
  } finally { loading.value = false }
}

function openConversationDetail(row: any) {
  detailConvId.value = row.conversation_id
  detailTitle.value = row.title || ''
  showDetail.value = true
}

watch(activeTab, (tab) => {
  if (tab === 'audit') loadAudit()
  else if (tab === 'slow') loadSlow()
  else loadConversations()
})

onMounted(loadAudit)
</script>

<style scoped>
.history-view { padding: 24px; }
.page-header { display: flex; align-items: center; gap: 8px; margin-bottom: 20px; }
.title { font-size: 1.3rem; font-weight: bold; }

/* 对话行可点击 */
:deep(.conv-row) { cursor: pointer; }
:deep(.conv-row:hover) { background: #f5f7ff !important; }

/* Token badge */
.token-badge {
  font-size: 0.78rem; color: #667eea; background: #f0f2ff;
  padding: 1px 6px; border-radius: 4px;
}
</style>
