<template>
  <el-drawer
    :model-value="modelValue"
    :title="`对话详情 · ${title || (conversationId || '').slice(0, 8)}`"
    direction="rtl"
    size="58%"
    @close="onClose"
  >
    <div v-loading="loading" class="detail-scroll">
      <div v-if="!turns.length && !loading" class="detail-empty">
        暂无对话数据
      </div>

      <div
        v-for="(turn, ti) in turns"
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
              <div v-if="turn.state.thinking.expanded_tables?.length" class="think-row">
                <span class="think-key">🕸️图谱扩展:</span>
                <span>{{ turn.state.thinking.seed_tables?.join(', ') || '-' }} →</span>
                <el-tag v-for="t in turn.state.thinking.expanded_tables" :key="t" size="small" type="success" style="margin: 0 2px">+{{ t }}</el-tag>
              </div>
              <div v-if="turn.state.thinking.join_path_section" class="think-row think-join">
                <span class="think-key">🕸️JOIN 路径:</span>
                <pre class="join-path-pre">{{ turn.state.thinking.join_path_section }}</pre>
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
            <div :ref="(el: any) => setChartRef(el, ti)" class="turn-chart"></div>
          </div>

          <!-- 命中指标 -->
          <div v-if="turn.state?.metric_hits?.length" class="turn-section">
            <div class="section-label">📊 命中指标</div>
            <div class="metric-hits-row">
              <el-tag
                v-for="h in turn.state.metric_hits" :key="h.metric"
                size="small" effect="plain"
              >{{ h.metric }}<span v-if="h.source === 'rule_inferred'">⚙️</span><span v-else-if="h.source === 'auto_inferred'">🤖</span><span v-if="h.table" style="color:#c0c4cc;margin-left:3px;font-size:0.62rem">{{ h.table }}</span></el-tag>
            </div>
          </div>
        </div>
      </div>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, ChatDotRound } from '@element-plus/icons-vue'
import { observability } from '@/api'
import * as echarts from 'echarts'

const props = defineProps<{
  modelValue: boolean
  conversationId: string
  title?: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: boolean): void
}>()

const loading = ref(false)
const turns = ref<any[]>([])
const openTurns = reactive<Record<number, boolean>>({})
const openThinking = reactive<Record<number, boolean>>({})
const chartRefs: Record<number, HTMLElement> = {}
const chartInstances: echarts.ECharts[] = []

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
  if (openTurns[idx]) {
    nextTick(() => {
      const chartOpt = turns.value[idx]?.state?.chart_option
      if (chartOpt && chartRefs[idx] && !chartInstances.find((_, i) => chartRefs[i] === chartRefs[idx])) {
        const chart = echarts.init(chartRefs[idx])
        chart.setOption(chartOpt)
        chartInstances.push(chart)
      }
    })
  }
}

function toggleThinking(idx: number) {
  openThinking[idx] = !openThinking[idx]
}

function rowsToObjs(columns: string[], rows: any[][]): Record<string, any>[] {
  if (!columns || !rows) return []
  return rows.map(row => {
    const obj: Record<string, any> = {}
    columns.forEach((col, i) => { obj[col] = row[i] ?? '' })
    return obj
  })
}

function setChartRef(el: any, idx: number) {
  if (el) chartRefs[idx] = el
}

function cleanup() {
  chartInstances.forEach(c => { try { c.dispose() } catch { /* ignore */ } })
  chartInstances.length = 0
  Object.keys(chartRefs).forEach(k => delete chartRefs[Number(k)])
  Object.keys(openTurns).forEach(k => delete openTurns[Number(k)])
  Object.keys(openThinking).forEach(k => delete openThinking[Number(k)])
}

async function loadDetail() {
  if (!props.conversationId) return
  loading.value = true
  turns.value = []
  cleanup()

  try {
    const { data } = await observability.conversationDetail(props.conversationId)
    turns.value = data
    if (data.length > 0) {
      openTurns[data.length - 1] = true
    }
    await nextTick()
    for (let i = 0; i < turns.value.length; i++) {
      if (!openTurns[i]) continue
      const chartOpt = turns.value[i]?.state?.chart_option
      if (chartOpt && chartRefs[i]) {
        const chart = echarts.init(chartRefs[i])
        chart.setOption(chartOpt)
        chartInstances.push(chart)
      }
    }
  } catch {
    turns.value = []
  } finally {
    loading.value = false
  }
}

function onClose() {
  cleanup()
  emit('update:modelValue', false)
}

// 当 drawer 打开且有 conversationId 时自动加载
watch(() => props.modelValue, (val) => {
  if (val && props.conversationId) {
    loadDetail()
  } else if (!val) {
    cleanup()
  }
})

onBeforeUnmount(() => {
  cleanup()
})
</script>

<style scoped>
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
.section-label:hover { color: #667eda; }
.sample-hint { font-weight: normal; color: #c0c4cc; }
.metric-hits-row { display: flex; flex-wrap: wrap; gap: 4px; }

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
.think-join {
  display: flex;
  align-items: flex-start;
  gap: 4px;
}
.join-path-pre {
  margin: 0;
  padding: 6px 10px;
  background: #eef0f5;
  border-radius: 4px;
  font-size: 0.75rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  flex: 1;
  overflow-x: auto;
}

.token-detail {
  font-size: 0.78rem;
  color: #909399;
  display: flex;
  gap: 12px;
}

.turn-chart { width: 100%; height: 280px; }
</style>
