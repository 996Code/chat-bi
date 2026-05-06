<template>
  <div class="page-container">
    <div class="header">
      <h2>监控面板</h2>
      <div style="display: flex; gap: 12px; align-items: center;">
        <el-button @click="router.push('/')">返回对话</el-button>
        <el-button type="primary" :loading="loading" @click="refreshAll">
          <el-icon><Refresh /></el-icon> 刷新
        </el-button>
      </div>
    </div>

    <!-- Stats cards -->
    <el-row :gutter="16" style="margin-bottom: 24px">
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header>慢查询数</template>
          <div class="stat-value danger">{{ stats.total_slow_queries ?? '—' }}</div>
          <div class="stat-sub">阈值 {{ stats.threshold_ms }}ms / {{ stats.window_hours }}h</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header>平均耗时</template>
          <div class="stat-value">{{ stats.avg_execution_time_ms ? stats.avg_execution_time_ms + 'ms' : '—' }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header>最慢查询</template>
          <div class="stat-value danger">{{ stats.max_execution_time_ms ? stats.max_execution_time_ms + 'ms' : '—' }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <template #header>数据源总数</template>
          <div class="stat-value">{{ datasourceStore.datasources.length }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Slow queries -->
    <el-card style="margin-bottom: 24px">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span>慢查询记录</span>
          <el-tag v-if="slowQueries.length" type="warning">{{ slowQueries.length }} 条</el-tag>
        </div>
      </template>
      <el-table :data="slowQueries" v-loading="slowLoading" empty-text="暂无慢查询">
        <el-table-column label="数据源" width="120">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ row.datasource_name || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="用户问题" min-width="200">
          <template #default="{ row }">
            <span class="question-text">{{ row.user_question || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="SQL" min-width="250">
          <template #default="{ row }">
            <code class="sql-text">{{ truncate(row.sql_text, 80) }}</code>
          </template>
        </el-table-column>
        <el-table-column label="总耗时" width="100" align="right">
          <template #default="{ row }">
            <el-tag type="danger" size="small">{{ row.total_time_ms }}ms</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="SQL耗时" width="90" align="right">
          <template #default="{ row }">
            <el-tag v-if="row.sql_time_ms" type="warning" size="small">{{ row.sql_time_ms }}ms</el-tag>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.error_message" type="danger" size="small">错误</el-tag>
            <el-tag v-else type="success" size="small">成功</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="追溯" width="80" align="center">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="traceToQuery(row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- Slow query trace dialog -->
    <PipelineTraceDialog v-model="showTraceDialog" :steps="reconstructedPipeline" title="慢查询追溯" :query-info="traceQueryInfo">
      <template #action>
        <span v-if="traceRow?.conversation_id">
          <b>对话：</b>
          <el-button size="small" type="primary" link @click="jumpToConversation(traceRow.conversation_id)">
            跳转到对话
          </el-button>
        </span>
        <span v-else-if="isEvalQuery(traceRow)">
          <b>来源：</b>
          <el-tag type="warning" size="small">评估测试</el-tag>
          <span class="text-muted" style="margin-left: 8px">评估查询不支持跳转</span>
        </span>
      </template>
    </PipelineTraceDialog>

    <!-- Datasource health -->
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span>数据源健康</span>
          <el-button size="small" @click="checkHealth">检测</el-button>
        </div>
      </template>
      <el-table :data="healthResults" v-loading="healthLoading" empty-text="点击检测查看状态">
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="type" label="类型" width="80" />
        <el-table-column prop="host" label="主机" />
        <el-table-column prop="database_name" label="数据库" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.healthy ? 'success' : 'danger'" size="small">
              {{ row.healthy ? '正常' : '异常' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="错误" min-width="200">
          <template #default="{ row }">
            <span v-if="!row.healthy" class="error-text">{{ row.error }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useDatasourceStore } from '@/stores/datasourceStore'
import api from '@/api'
import PipelineTraceDialog from '@/components/PipelineTraceDialog.vue'

const router = useRouter()
const datasourceStore = useDatasourceStore()
const loading = ref(false)
const slowLoading = ref(false)
const healthLoading = ref(false)

const stats = ref<any>({})
const slowQueries = ref<any[]>([])
const healthResults = ref<any[]>([])

function formatTime(ts: string | number) {
  if (!ts) return '—'
  const d = new Date(typeof ts === 'number' ? ts * 1000 : ts)
  return d.toLocaleString('zh-CN')
}

function truncate(s: string, len: number) {
  if (!s) return ''
  return s.length > len ? s.slice(0, len) + '...' : s
}

async function refreshAll() {
  loading.value = true
  try {
    await Promise.all([loadStats(), loadSlowQueries()])
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try {
    const res = await api.get('/analytics/slow-queries/stats')
    stats.value = res.data
  } catch {
    stats.value = {}
  }
}

async function loadSlowQueries() {
  slowLoading.value = true
  try {
    const res = await api.get('/analytics/slow-queries', { params: { limit: 50 } })
    slowQueries.value = res.data || []
  } catch {
    slowQueries.value = []
  } finally {
    slowLoading.value = false
  }
}

async function checkHealth() {
  healthLoading.value = true
  try {
    const dsRes = await api.get('/datasources')
    const datasources = dsRes.data || []
    const results: any[] = []
    for (const ds of datasources) {
      try {
        const healthRes = await api.get(`/datasources/${ds.id}/health`)
        results.push({
          name: ds.name,
          type: ds.db_type || ds.type,
          host: ds.host,
          database_name: ds.database_name,
          healthy: healthRes.data.healthy,
          error: healthRes.data.error,
        })
      } catch (e: any) {
        results.push({
          name: ds.name,
          type: ds.db_type || ds.type,
          host: ds.host,
          database_name: ds.database_name,
          healthy: false,
          error: e.response?.data?.detail?.message || '检测超时',
        })
      }
    }
    healthResults.value = results
  } catch {
    healthResults.value = []
  } finally {
    healthLoading.value = false
  }
}

const showTraceDialog = ref(false)
const traceRow = ref<any>(null)

function isEvalQuery(row: any) {
  return row?.action === 'EVAL_QUERY'
}

function jumpToConversation(convId: string) {
  showTraceDialog.value = false
  router.push({ path: '/', query: { conv: convId } })
}

// Query info for trace dialog
const traceQueryInfo = computed(() => {
  if (!traceRow.value) return undefined
  const row = traceRow.value
  return {
    datasource: row.datasource_name || '—',
    question: row.user_question || '—',
    totalTime: row.total_time_ms,
    sqlTime: row.sql_time_ms,
    status: (row.error_message ? 'error' : 'success') as 'success' | 'error',
    error: row.error_message,
  }
})

// Reconstruct pipeline steps from slow query data
const reconstructedPipeline = computed(() => {
  if (!traceRow.value) return []
  const row = traceRow.value
  const steps: any[] = []

  // Step 1: Intent
  const question = row.user_question || ''
  steps.push({
    type: 'intent',
    label: '意图识别',
    status: 'done',
    detail: question ? `识别为数据查询意图: "${question.substring(0, 80)}${question.length > 80 ? '...' : ''}"` : '数据查询意图',
  })

  // Step 2: Schema (try to extract from details)
  steps.push({
    type: 'semantics',
    label: 'Schema 选择',
    status: 'done',
    detail: row.datasource_name ? `数据源: ${row.datasource_name}` : '未找到相关表',
    tables: row.datasource_name ? [row.datasource_name] : [],
    columns: {},
  })

  // Step 3: SQL generation
  const sqlStatus = row.error_message ? 'failed' : 'done'
  steps.push({
    type: 'sql',
    label: 'SQL 生成',
    status: sqlStatus,
    sql: row.sql_text,
    detail: row.sql_text ? `生成 SQL (${row.sql_text.length} 字符)` : '未生成 SQL',
  })

  // Step 4: Execution
  if (row.sql_time_ms) {
    steps.push({
      type: 'data',
      label: 'SQL 执行',
      status: sqlStatus,
      detail: row.error_message
        ? `执行失败: ${row.error_message}`
        : `查询成功，SQL 执行耗时 ${row.sql_time_ms}ms`,
      duration_ms: row.sql_time_ms,
    })
  }

  // Step 5: Total pipeline
  steps.push({
    type: 'complete',
    label: '总耗时',
    status: 'done',
    detail: `整个查询管线总耗时 ${row.total_time_ms}ms`,
    duration_ms: row.total_time_ms,
  })

  return steps
})

function traceToQuery(row: any) {
  traceRow.value = row
  showTraceDialog.value = true
}

onMounted(async () => {
  await datasourceStore.list()
  refreshAll()
  checkHealth()
})
</script>

<style scoped>
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}
.header h2 {
  margin: 0;
}
.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #1e293b;
  text-align: center;
  padding: 8px 0;
}
.stat-value.danger {
  color: #ef4444;
}
.stat-sub {
  font-size: 12px;
  color: #94a3b8;
  text-align: center;
  margin-top: 4px;
}
.question-text {
  font-size: 13px;
  color: #334155;
}
.sql-text {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 12px;
  color: #64748b;
}
.error-text {
  color: #ef4444;
  font-size: 12px;
}
.text-muted {
  color: #c0c4cc;
  font-size: 12px;
}
</style>
