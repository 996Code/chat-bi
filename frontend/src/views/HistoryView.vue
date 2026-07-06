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
        <!-- DSO-07: 耗时列 (慢查询标红) -->
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

    <!-- 对话历史 (点击行 → 抽屉查看每轮结果详情) -->
    <div v-if="activeTab === 'conversations'" v-loading="loading">
      <el-table
        :data="conversations" size="small" border
        highlight-current-row
        @row-click="openConversationDetail"
        row-class-name="conv-row"
      >
        <el-table-column prop="conversation_id" label="对话ID" width="120">
          <template #default="{ row }"><code>{{ (row.conversation_id || '').slice(0, 8) }}</code></template>
        </el-table-column>
        <el-table-column prop="turn_count" label="轮次" width="80" />
        <el-table-column prop="last_tables" label="涉及的表" min-width="150">
          <template #default="{ row }">{{ (row.last_tables || []).join(', ') }}</template>
        </el-table-column>
        <el-table-column prop="last_sql" label="最后SQL" min-width="200">
          <template #default="{ row }"><code style="font-size:0.8em">{{ row.last_sql }}</code></template>
        </el-table-column>
        <el-table-column prop="timestamp" label="时间" width="180">
          <template #default="{ row }">{{ formatTime(row.timestamp) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="center">
          <template #default>
            <el-button text size="small" type="primary">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 对话详情抽屉 (展示每轮的问题/SQL/结果/图表) -->
    <el-drawer
      v-model="showDetail"
      :title="`对话详情 · ${detailConvId.slice(0, 8)}`"
      direction="rtl"
      size="55%"
      @close="onDetailClose"
    >
      <div v-loading="detailLoading">
        <div v-if="!detailTurns.length && !detailLoading" class="detail-empty">
          暂无对话数据
        </div>

        <div
          v-for="(turn, ti) in detailTurns"
          :key="ti"
          class="turn-block"
        >
          <div class="turn-header">
            <el-tag size="small" type="info">第 {{ turn.turn }} 轮</el-tag>
            <span class="turn-time">{{ formatTime(turn.timestamp) }}</span>
          </div>

          <!-- 用户问题 -->
          <div v-if="turn.state?.question" class="turn-question">
            <el-icon><ChatDotRound /></el-icon>
            {{ turn.state.question }}
          </div>

          <!-- AI 回复 -->
          <div v-if="turn.state?.reply" class="turn-reply">{{ turn.state.reply }}</div>

          <!-- SQL -->
          <div v-if="turn.state?.current_sql" class="turn-section">
            <div class="section-label">SQL</div>
            <pre class="sql-code">{{ turn.state.current_sql }}</pre>
          </div>

          <!-- 结果表格 -->
          <div v-if="turn.state?.columns?.length" class="turn-section">
            <div class="section-label">
              结果 {{ turn.state?.result_summary?.row_count ?? turn.state.rows_sample.length }} 行
              <span v-if="(turn.state?.result_summary?.row_count ?? 0) > turn.state.rows_sample.length" class="sample-hint">
                (展示前 {{ turn.state.rows_sample.length }} 行)
              </span>
            </div>
            <el-table
              :data="rowsToObjs(turn.state.columns, turn.state.rows_sample)"
              size="small" stripe border
              max-height="300"
              style="width: 100%"
            >
              <el-table-column
                v-for="col in turn.state.columns"
                :key="col"
                :prop="col" :label="col" min-width="100"
                show-overflow-tooltip
              />
            </el-table>
          </div>

          <!-- 图表 -->
          <div v-if="turn.state?.chart_option" class="turn-section">
            <div class="section-label">图表</div>
            <div :ref="(el: any) => setDetailChartRef(el, ti)" class="turn-chart"></div>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { ArrowLeft, ChatDotRound } from '@element-plus/icons-vue'
import { observability } from '@/api'
import * as echarts from 'echarts'

const activeTab = ref('audit')
const loading = ref(false)
const auditLogs = ref<any[]>([])
const slowQueries = ref<any[]>([])
const conversations = ref<any[]>([])

// 对话详情抽屉
const showDetail = ref(false)
const detailLoading = ref(false)
const detailConvId = ref('')
const detailTurns = ref<any[]>([])
const detailChartRefs: Record<number, HTMLElement> = {}
const detailChartInstances: echarts.ECharts[] = []

function formatTime(iso: string): string {
  if (!iso) return ''
  try { return new Date(iso).toLocaleString('zh-CN') } catch { return iso }
}

async function loadAudit() {
  loading.value = true
  try {
    const { data } = await observability.auditLogs({ limit: 50 })
    auditLogs.value = data
  } catch (e: any) {
    ElMessage.error('加载审计日志失败: ' + (e.response?.data?.detail || e.message))
  } finally { loading.value = false }
}

async function loadSlow() {
  loading.value = true
  try {
    const { data } = await observability.slowQueries(50)
    slowQueries.value = data
  } catch (e: any) {
    ElMessage.error('加载慢查询失败: ' + (e.response?.data?.detail || e.message))
  } finally { loading.value = false }
}

async function loadConversations() {
  loading.value = true
  try {
    const { data } = await observability.conversations()
    conversations.value = data
  } catch (e: any) {
    ElMessage.error('加载对话列表失败: ' + (e.response?.data?.detail || e.message))
  } finally { loading.value = false }
}

// rows: any[][] → Record<string, any> (表格行展示)
function rowsToObjs(columns: string[], rows: any[][]): Record<string, any>[] {
  if (!columns || !rows) return []
  return rows.map(row => {
    const obj: Record<string, any> = {}
    columns.forEach((col, i) => { obj[col] = row[i] ?? '' })
    return obj
  })
}

function setDetailChartRef(el: any, idx: number) {
  if (el) detailChartRefs[idx] = el
}

// 点击对话行 → 打开抽屉加载详情
async function openConversationDetail(row: any) {
  detailConvId.value = row.conversation_id
  showDetail.value = true
  detailLoading.value = true
  detailTurns.value = []
  // 清理旧图表实例
  detailChartInstances.forEach(c => c.dispose())
  detailChartInstances.length = 0
  Object.keys(detailChartRefs).forEach(k => delete detailChartRefs[Number(k)])

  try {
    const { data } = await observability.conversationDetail(row.conversation_id)
    detailTurns.value = data
    // 渲染图表
    await nextTick()
    for (let i = 0; i < detailTurns.value.length; i++) {
      const chartOpt = detailTurns.value[i]?.state?.chart_option
      if (chartOpt && detailChartRefs[i]) {
        const chart = echarts.init(detailChartRefs[i])
        chart.setOption(chartOpt)
        detailChartInstances.push(chart)
      }
    }
  } catch {
    detailTurns.value = []
  } finally {
    detailLoading.value = false
  }
}

function onDetailClose() {
  detailChartInstances.forEach(c => c.dispose())
  detailChartInstances.length = 0
  Object.keys(detailChartRefs).forEach(k => delete detailChartRefs[Number(k)])
}

watch(activeTab, (tab) => {
  if (tab === 'audit') loadAudit()
  else if (tab === 'slow') loadSlow()
  else loadConversations()
})

onMounted(loadAudit)

onBeforeUnmount(() => {
  // 释放 ECharts 实例, 防止内存泄漏
  detailChartInstances.forEach(c => { try { c.dispose() } catch { /* ignore */ } })
  detailChartInstances.length = 0
})
</script>

<style scoped>
.history-view { padding: 24px; }
.page-header { display: flex; align-items: center; gap: 8px; margin-bottom: 20px; }
.title { font-size: 1.3rem; font-weight: bold; }

/* 对话行可点击 */
:deep(.conv-row) { cursor: pointer; }
:deep(.conv-row:hover) { background: #f5f7ff !important; }

/* 抽屉内容 */
.detail-empty { text-align: center; color: #909399; padding: 40px 0; }
.turn-block {
  margin-bottom: 24px;
  padding: 16px;
  background: #fafbfc;
  border-radius: 8px;
  border: 1px solid #ebeef5;
}
.turn-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.turn-time { font-size: 0.75rem; color: #c0c4cc; }
.turn-question {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 0.9rem;
  color: #303133;
  margin-bottom: 8px;
  line-height: 1.5;
}
.turn-reply {
  font-size: 0.85rem;
  color: #606266;
  margin-bottom: 8px;
  line-height: 1.6;
  white-space: pre-wrap;
}
.turn-section { margin-top: 10px; }
.section-label {
  font-size: 0.75rem;
  font-weight: 600;
  color: #909399;
  margin-bottom: 6px;
}
.sample-hint { font-weight: normal; color: #c0c4cc; }
.sql-code {
  margin: 0;
  padding: 10px 14px;
  background: #1e1e1e;
  color: #a5d6ff;
  font-size: 0.8rem;
  font-family: 'SF Mono', 'Fira Code', 'Menlo', monospace;
  border-radius: 6px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.5;
}
.turn-chart { width: 100%; height: 280px; }
</style>
