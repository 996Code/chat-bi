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

    <!-- 对话详情抽屉 -->
    <el-drawer
      v-model="showDetail"
      :title="`对话详情 · ${detailTitle || detailConvId.slice(0, 8)}`"
      direction="rtl"
      size="58%"
      @close="onDetailClose"
    >
      <div v-loading="detailLoading" class="detail-scroll">
        <div v-if="!detailTurns.length && !detailLoading" class="detail-empty">
          暂无对话数据
        </div>

        <div
          v-for="(turn, ti) in detailTurns"
          :key="ti"
          class="turn-block"
        >
          <!-- 轮次头部: 轮次 + 意图 + token + 耗时 -->
          <div class="turn-header" @click="toggleTurn(ti)">
            <el-tag size="small" type="info">第 {{ turn.turn }} 轮</el-tag>
            <el-tag v-if="turn.state?.intent" size="small" :type="intentTagType(turn.state.intent)">{{ turn.state.intent }}</el-tag>
            <el-tag v-if="turn.state?.token_usage?.total_tokens" size="small" type="warning">{{ formatTokens(turn.state.token_usage.total_tokens) }} tokens</el-tag>
            <span v-if="turn.state?.sql_duration_ms" class="turn-meta">{{ turn.state.sql_duration_ms }}ms</span>
            <span v-if="turn.state?.error" class="turn-meta" style="color:#f56c6c">❌ 失败</span>
            <span v-if="turn.state?.self_heal_rounds > 0" class="turn-meta" style="color:#e6a23c">🔧 自愈{{ turn.state.self_heal_rounds }}轮</span>
            <span class="turn-time">{{ formatTime(turn.timestamp) }}</span>
            <el-icon class="turn-toggle" style="margin-left:auto"><ArrowDown v-if="!openTurns[ti]" /><ArrowUp v-else /></el-icon>
          </div>

          <div v-if="openTurns[ti]" class="turn-body">
            <!-- 用户问题 -->
            <div v-if="turn.state?.question" class="turn-question">
              <el-icon><ChatDotRound /></el-icon>
              {{ turn.state.question }}
            </div>

            <!-- AI 回复 -->
            <div v-if="turn.state?.reply" class="turn-reply">{{ turn.state.reply }}</div>

            <!-- 预思考 -->
            <div v-if="turn.state?.thinking" class="turn-section">
              <div class="section-label" @click="toggleThinking(ti)">
                🧠 预思考
                <el-icon style="margin-left:4px"><ArrowDown v-if="!openThinking[ti]" /><ArrowUp v-else /></el-icon>
              </div>
              <div v-if="openThinking[ti]" class="thinking-content">
                <div v-if="turn.state.thinking.tables?.length" class="think-row">
                  <span class="think-key">选表:</span>
                  <span>{{ turn.state.thinking.tables.join(', ') }}</span>
                </div>
                <div v-if="turn.state.thinking.aggregation" class="think-row">
                  <span class="think-key">聚合:</span>
                  <span>{{ turn.state.thinking.aggregation }}</span>
                </div>
                <div v-if="turn.state.thinking.caveats?.length" class="think-row">
                  <span class="think-key">陷阱:</span>
                  <span>{{ turn.state.thinking.caveats.join('; ') }}</span>
                </div>
                <div v-if="turn.state.thinking.prev_sql_review" class="think-row">
                  <span class="think-key">上轮SQL审视:</span>
                  <span>{{ turn.state.thinking.prev_sql_review }}</span>
                </div>
              </div>
            </div>

            <!-- SQL + 自愈对比 -->
            <div v-if="turn.state?.current_sql" class="turn-section">
              <div class="section-label">SQL<span v-if="turn.state?.sql_duration_ms" style="margin-left:8px;color:#909399;font-weight:normal">耗时 {{ turn.state.sql_duration_ms }}ms</span></div>
              <pre class="sql-code">{{ turn.state.current_sql }}</pre>
              <div v-if="turn.state?.heal_before_sql" class="heal-diff">
                <div class="heal-diff-label">🔧 自愈前的 SQL</div>
                <pre class="sql-code sql-before">{{ turn.state.heal_before_sql }}</pre>
              </div>
            </div>

            <!-- 错误信息 -->
            <div v-if="turn.state?.error" class="turn-section">
              <el-alert type="error" :closable="false" show-icon :title="turn.state.error" />
            </div>

            <!-- Token 用量明细 -->
            <div v-if="turn.state?.token_usage" class="turn-section">
              <div class="section-label">Token 用量</div>
              <div class="token-detail">
                <span>输入 {{ turn.state.token_usage.prompt_tokens || 0 }}</span>
                <span>输出 {{ turn.state.token_usage.completion_tokens || 0 }}</span>
                <span>合计 {{ turn.state.token_usage.total_tokens || 0 }}</span>
                <span v-if="turn.state.token_usage.nodes">· {{ Object.keys(turn.state.token_usage.nodes).length }} 个节点</span>
              </div>
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
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowLeft, ArrowDown, ArrowUp, ChatDotRound } from '@element-plus/icons-vue'
import { observability } from '@/api'
import * as echarts from 'echarts'

const activeTab = ref('audit')
const loading = ref(false)
const auditLogs = ref<any[]>([])
const slowQueries = ref<any[]>([])
const conversations = ref<any[]>([])

// 对话详情
const showDetail = ref(false)
const detailLoading = ref(false)
const detailConvId = ref('')
const detailTitle = ref('')
const detailTurns = ref<any[]>([])
const openTurns = reactive<Record<number, boolean>>({})
const openThinking = reactive<Record<number, boolean>>({})
const detailChartRefs: Record<number, HTMLElement> = {}
const detailChartInstances: echarts.ECharts[] = []

function formatTime(iso: string): string {
  if (!iso) return ''
  try { return new Date(iso).toLocaleString('zh-CN') } catch { return iso }
}

function formatTokens(n: number): string {
  if (!n) return '0'
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

function intentTagType(intent: string): string {
  const map: Record<string, string> = {
    TEXT_TO_SQL: '', CLARIFICATION: 'warning', GENERAL: 'info',
    CHART_MODIFY: 'success', EXPLANATION: 'info',
  }
  return map[intent] || ''
}

function toggleTurn(idx: number) {
  openTurns[idx] = !openTurns[idx]
  // 首次展开时自动渲染图表
  if (openTurns[idx]) {
    nextTick(() => {
      const chartOpt = detailTurns.value[idx]?.state?.chart_option
      if (chartOpt && detailChartRefs[idx] && !detailChartInstances.find((_, i) => detailChartRefs[i] === detailChartRefs[idx])) {
        const chart = echarts.init(detailChartRefs[idx])
        chart.setOption(chartOpt)
        detailChartInstances.push(chart)
      }
    })
  }
}

function toggleThinking(idx: number) {
  openThinking[idx] = !openThinking[idx]
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

async function openConversationDetail(row: any) {
  detailConvId.value = row.conversation_id
  detailTitle.value = row.title || ''
  showDetail.value = true
  detailLoading.value = true
  detailTurns.value = []
  // 清理
  detailChartInstances.forEach(c => c.dispose())
  detailChartInstances.length = 0
  Object.keys(detailChartRefs).forEach(k => delete detailChartRefs[Number(k)])
  Object.keys(openTurns).forEach(k => delete openTurns[Number(k)])
  Object.keys(openThinking).forEach(k => delete openThinking[Number(k)])

  try {
    const { data } = await observability.conversationDetail(row.conversation_id)
    detailTurns.value = data
    // 默认展开最后一轮
    if (data.length > 0) {
      openTurns[data.length - 1] = true
    }
    // 渲染展开轮次的图表
    await nextTick()
    for (let i = 0; i < detailTurns.value.length; i++) {
      if (!openTurns[i]) continue
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

/* Token badge */
.token-badge {
  font-size: 0.78rem; color: #667eea; background: #f0f2ff;
  padding: 1px 6px; border-radius: 4px;
}

/* 抽屉内容 */
.detail-scroll { max-height: calc(100vh - 60px); overflow-y: auto; }
.detail-empty { text-align: center; color: #909399; padding: 40px 0; }

.turn-block {
  margin-bottom: 12px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
}
.turn-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: #fafbfc;
  cursor: pointer;
}
.turn-header:hover { background: #f5f7fa; }
.turn-time { font-size: 0.72rem; color: #c0c4cc; }
.turn-meta { font-size: 0.72rem; color: #909399; }
.turn-toggle { color: #c0c4cc; font-size: 12px; cursor: pointer; }

.turn-body { padding: 14px; }

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
  cursor: pointer;
}
.section-label:hover { color: #667eea; }
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
.sql-before {
  background: #2d1b1b;
  color: #f8a0a0;
  text-decoration: line-through;
  opacity: 0.8;
}
.heal-diff { margin-top: 6px; }
.heal-diff-label { font-size: 0.72rem; color: #e6a23c; margin-bottom: 4px; }

.thinking-content {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 10px 14px;
  font-size: 0.82rem;
  line-height: 1.6;
}
.think-row { margin-bottom: 4px; }
.think-key {
  font-weight: 600;
  color: #667eea;
  margin-right: 4px;
}

.token-detail {
  font-size: 0.78rem;
  color: #909399;
  display: flex;
  gap: 12px;
}

.turn-chart { width: 100%; height: 280px; }
</style>
