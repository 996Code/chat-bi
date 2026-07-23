<template>
  <div class="chat-layout">
    <!-- 左侧对话历史侧边栏 -->
    <aside class="chat-sidebar">
      <el-button type="primary" :icon="Plus" class="new-chat-btn" @click="startNewConversation">
        新建对话
      </el-button>
      <div class="conv-list">
        <div
          v-for="conv in conversations"
          :key="conv.conversation_id"
          class="conv-item"
          :class="{ active: conv.conversation_id === conversationId }"
          @click="loadConversation(conv.conversation_id)"
        >
          <el-icon class="conv-icon"><ChatDotRound /></el-icon>
          <div class="conv-text">
            <div class="conv-title">{{ conv.title || '新对话' }}</div>
            <div class="conv-meta">{{ conv.turn_count }} 轮 · {{ formatTime(conv.timestamp) }}</div>
          </div>
        </div>
        <div v-if="!conversations.length && !convLoading" class="conv-empty">
          暂无对话历史
        </div>
      </div>
    </aside>

    <!-- 右侧主对话区 -->
    <div class="chat-main">
      <div class="chat-header">
        <span class="title">ChatBI 问答</span>
        <span v-if="conversationId" class="conv-id-hint">{{ conversationId }}</span>
        <el-select v-model="selectedDsId" placeholder="选择数据源" size="small" style="margin-left: auto; width: 200px">
          <el-option v-for="ds in dataSources" :key="ds.id" :label="ds.name" :value="ds.id" />
        </el-select>
        <el-button text size="small" @click="$router.push('/datasources')">数据源</el-button>
        <el-button text size="small" @click="$router.push('/semantic')">语义层</el-button>
        <el-button text size="small" @click="$router.push('/dashboard')">看板</el-button>
        <el-button text size="small" @click="$router.push('/history')">历史</el-button>
        <el-button text size="small" @click="$router.push('/observability')">系统</el-button>
      </div>

      <!-- 对话区 -->
      <div class="chat-body" ref="chatBody">
        <div v-if="!messages.length" class="empty-hint">
          <el-empty description="开始你的数据查询之旅吧！">
            <template #image>
              <el-icon :size="72" color="#c0c4cc"><ChatDotRound /></el-icon>
            </template>
          </el-empty>
          <!-- 建议问题 (对标 V1 suggestedQuestions, 从语义层拉取) -->
          <div v-if="sampleQuestions.length" class="suggestions">
            <div
              v-for="q in sampleQuestions"
              :key="q"
              class="suggestion-chip"
              @click="askSuggestion(q)"
            >{{ q }}</div>
          </div>
        </div>

        <div v-for="(msg, idx) in messages" :key="idx" :class="['message', msg.role]">
          <div class="message-content">
            <!-- 用户消息 -->
            <template v-if="msg.role === 'user'">
              <div class="message-text">{{ msg.text }}</div>
            </template>
            <!-- Agent 回复 -->
            <template v-else>
              <div v-if="msg.error" class="error-box">
                <el-alert :title="msg.error" type="error" :closable="false" show-icon />
              </div>
              <template v-else>
                <!-- 自然语言回复 (GENERAL/EXPLANATION 意图) -->
                <div v-if="msg.reply" class="message-text">{{ msg.reply }}</div>
                <!-- 兜底: 成功但无回复且无管线结果 -->
                <div
                  v-if="msg.done && !msg.reply && !msg.steps?.length"
                  class="message-text empty-reply"
                >
                  该问题暂无可展示的结果，请尝试用更具体的业务问题提问。
                </div>
              </template>

              <!-- Agent 主动确认表单 (ARC-03: 关键节点暂停, 用户输入后继续) -->
              <div v-if="msg.askUser" class="ask-user-box">
                <!-- T047: Agent 状态徽标 (暂停在哪一步 + 已用 token) -->
                <div class="ask-status-row">
                  <el-tag size="small" type="warning" effect="plain">
                    ⏸ Agent 暂停 · {{ msg.steps?.[msg.steps.length - 1]?.label || '等待确认' }}
                  </el-tag>
                  <span v-if="msg.tokenUsage" class="ask-status-token">
                    已用 {{ msg.tokenUsage.total_tokens }} tokens
                  </span>
                </div>
                <div class="ask-question">{{ msg.askUser.question }}</div>
                <!-- 有候选选项 → 点击选择; 无选项 → 文本输入 -->
                <div v-if="msg.askUser.options?.length" class="ask-options">
                  <el-button
                    v-for="opt in msg.askUser.options"
                    :key="opt"
                    size="small" round
                    @click="answerClarify(opt)"
                  >{{ opt }}</el-button>
                </div>
                <div v-else class="ask-input">
                  <el-input
                    v-model="clarifyInput"
                    size="small"
                    placeholder="补充说明..."
                    @keydown.enter="answerClarify(clarifyInput)"
                  />
                  <el-button type="primary" size="small" @click="answerClarify(clarifyInput)">发送</el-button>
                </div>
              </div>

              <!-- BI 图表 (查询完成后图表为主, 展示在流程前面) -->
              <div v-if="msg.chart && msg.chart.chart_type !== 'table' && msg.rows?.length" class="chart-box">
                <div :ref="(el: any) => setChartRef(el, idx)" :style="{ width: '100%', height: msg.chart.chart_type === 'kpi' ? '200px' : '350px' }"></div>
                <div class="chart-toolbar">
                  <!-- 左侧: 导出 Excel + 保存到看板 -->
                  <div class="chart-toolbar-left">
                    <el-button text size="small" :icon="Download" @click="exportChart(idx)">导出 Excel</el-button>
                    <el-button text size="small" :icon="Monitor" @click="openSaveToDashboard(idx)">保存到看板</el-button>
                  </div>
                  <!-- 右侧: 完整流程 (弹窗) -->
                  <el-button
                    v-if="msg.done && msg.steps?.length"
                    text size="small" :icon="QuestionFilled"
                    @click="openTraceDialog(msg)"
                  >完整流程</el-button>
                </div>
              </div>
              <!-- TABLE 图表类型: 渲染 HTML 表格 -->
              <div v-if="msg.chart && msg.chart.chart_type === 'table' && msg.columns?.length && msg.rows?.length" class="chart-box chart-table-box">
                <div class="chart-table-wrap">
                  <table class="chart-table">
                    <thead><tr><th v-for="c in msg.columns" :key="c">{{ c }}</th></tr></thead>
                    <tbody>
                      <tr v-for="(row, ri) in (msg.rows || []).slice(0, 20)" :key="ri">
                        <td v-for="c in msg.columns" :key="c">{{ row[c] ?? '' }}</td>
                      </tr>
                    </tbody>
                  </table>
                  <div v-if="((msg.rows?.length || 0) > 20)" class="table-more">共 {{ msg.rows?.length }} 行, 仅展示前 20 行</div>
                </div>
                <div class="chart-toolbar">
                  <div class="chart-toolbar-left">
                    <el-button text size="small" :icon="Download" @click="exportChart(idx)">导出 Excel</el-button>
                    <el-button text size="small" :icon="Monitor" @click="openSaveToDashboard(idx)">保存到看板</el-button>
                  </div>
                  <el-button
                    v-if="msg.done && msg.steps?.length"
                    text size="small" :icon="QuestionFilled"
                    @click="openTraceDialog(msg)"
                  >完整流程</el-button>
                </div>
              </div>
              <!-- 查询无结果: 有列结构但无数据行 (无论有无旧 chart option) -->
              <div v-if="msg.columns?.length && !(msg.rows?.length) && msg.done" class="result-meta" style="display:flex;align-items:center;justify-content:space-between;">
                <span style="color:#909399;font-size:0.82rem;">查询无匹配数据</span>
                <el-button v-if="msg.steps?.length" text size="small" :icon="QuestionFilled" @click="openTraceDialog(msg)">完整流程</el-button>
              </div>

              <!-- Pipeline (V1 交互: 执行中竖排实时显示, 完成后隐藏 → 弹窗看完整流程) -->
              <div v-if="msg.steps?.length && !msg.done" class="pipeline">
                <div class="pipeline-header-row">
                  <span class="pipeline-title">查询流程</span>
                </div>
                <div
                  v-for="(s, si) in msg.steps" :key="si"
                >
                  <div :class="['pipeline-step', s.status]">
                    <div class="step-icon">
                      <el-icon v-if="s.status === 'running'" class="is-loading"><Loading /></el-icon>
                      <el-icon v-else-if="s.status === 'done'" class="step-done"><CircleCheck /></el-icon>
                      <el-icon v-else class="step-failed"><CircleClose /></el-icon>
                    </div>
                    <div class="step-content" :class="{ clickable: s.expandable }" @click="toggleStep(msg, si)">
                      <div class="step-label">
                        {{ s.label }}
                        <el-icon v-if="s.expandable" class="expand-icon"><ArrowDown :class="{ rotated: !s.expanded }" /></el-icon>
                      </div>
                      <div v-if="s.detail && !s.expandable" class="step-detail">{{ s.detail }}</div>
                    </div>
                    <span v-if="s.duration" class="step-dur">{{ s.duration }}ms</span>
                    <span v-if="s.llmCalls" class="step-llm">🤖 ×{{ s.llmCalls }}<template v-if="s.llmTokens"> · {{ s.llmTokens }} tokens</template></span>
                  </div>
                  <!-- 展开内容区域 (SQL / thinking / heal) — 紧跟对应步骤 -->
                  <div v-if="s.expandable && s.expanded" class="step-expand">
                    <pre v-if="s.type === 'sql' && msg.originalSql" class="sql-inline">{{ msg.originalSql }}</pre>
                    <div v-else-if="s.type === 'thinking' && s.thinkingData" class="thinking-detail">
                      <div v-if="s.thinkingData.tables?.length" class="thinking-section">
                        <span class="thinking-label">选表:</span> {{ s.thinkingData.tables.join(', ') }}
                      </div>
                      <div v-if="s.thinkingData.expandedTables?.length" class="thinking-section">
                        <span class="thinking-label">🕸️图谱扩展:</span>
                        {{ s.thinkingData.seedTables?.join(', ') || '-' }}
                        →
                        <el-tag v-for="t in s.thinkingData.expandedTables" :key="t" size="small" type="success" style="margin: 0 2px">+{{ t }}</el-tag>
                      </div>
                      <div v-if="s.thinkingData.joinPathSection" class="thinking-section thinking-join">
                        <span class="thinking-label">🕸️JOIN 路径:</span>
                        <pre class="join-path-pre">{{ s.thinkingData.joinPathSection }}</pre>
                      </div>
                      <div v-if="s.thinkingData.aggregation" class="thinking-section">
                        <span class="thinking-label">聚合:</span> {{ s.thinkingData.aggregation }}
                      </div>
                      <div v-if="s.thinkingData.caveats?.length" class="thinking-section">
                        <span class="thinking-label">注意:</span> {{ s.thinkingData.caveats.join('; ') }}
                      </div>
                    </div>
                    <div v-else-if="s.type === 'heal' && s.healData" class="heal-detail">
                      <div class="heal-compare">
                        <div class="heal-col">
                          <span class="heal-label">修复前</span>
                          <pre class="sql-inline heal-sql">{{ s.healData.before }}</pre>
                        </div>
                        <div class="heal-col">
                          <span class="heal-label">修复后</span>
                          <pre class="sql-inline heal-sql healed">{{ s.healData.after }}</pre>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- T049: token 用量汇总 (完成后显示) -->
              <div v-if="msg.done && msg.tokenUsage" class="trace-summary">
                🔥 {{ msg.tokenUsage.total_tokens }} tokens
                (输入 {{ msg.tokenUsage.prompt_tokens }} / 输出 {{ msg.tokenUsage.completion_tokens }})
                · {{ msg.tokenUsage.llm_calls }} 次 LLM 调用
                <span v-if="msg.selfHealRounds"> · 自愈 {{ msg.selfHealRounds }} 轮</span>
                <!-- H5: per-node 分段明细 -->
                <div v-if="msg.tokenUsage.nodes" class="token-nodes">
                  <span v-for="(nu, name) in msg.tokenUsage.nodes" :key="name" class="token-node-tag">
                    {{ name }}: {{ nu.total_tokens }}
                  </span>
                </div>
              </div>
              <!-- 指标命中提示 (完成后显示) -->
              <div v-if="msg.done && msg.metricHits?.length" class="metric-hits-bar">
                <span class="metric-hits-label">📊 命中指标</span>
                <el-tag
                  v-for="h in msg.metricHits.slice(0, 5)" :key="h.metric"
                  size="small" effect="plain" class="metric-hit-tag"
                >{{ h.metric }}<span class="metric-hit-table">{{ h.table }}</span></el-tag>
                <span v-if="msg.metricHits.length > 5" class="metric-hit-more">+{{ msg.metricHits.length - 5 }}</span>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- 输入区 (T048: slash command + token 预算) -->
      <div class="chat-input">
        <div class="input-wrap">
          <el-input
            v-model="input"
            type="textarea"
            :rows="2"
            placeholder="输入问题... (Enter 发送, Shift+Enter 换行, ↑↓ 切换历史, / 查看命令)"
            @keydown.enter.exact.prevent="send"
            @keydown.arrow-up.prevent="navigateHistory(-1)"
            @keydown.arrow-down.prevent="navigateHistory(1)"
            @input="onInputChange"
            :disabled="loading || !selectedDsId"
          />
          <!-- T048: slash command 弹出菜单 -->
          <div v-if="slashMenuVisible" class="slash-menu">
            <div
              v-for="cmd in slashCommands"
              :key="cmd.cmd"
              class="slash-item"
              @mousedown.prevent="runSlashCommand(cmd.cmd)"
            >
              <code>{{ cmd.cmd }}</code>
              <span class="slash-desc">{{ cmd.desc }}</span>
            </div>
          </div>
          <!-- T048: token 预算展示 (当前对话累计) -->
          <span v-if="totalTokens > 0" class="token-budget">
            {{ totalTokens }} tokens
          </span>
        </div>
        <el-button type="primary" :loading="loading" :disabled="!input.trim() || !selectedDsId" @click="send">
          发送
        </el-button>
      </div>
    </div>

	    <!-- 完整流程弹窗 (步骤表格, 整行点击展开/收起看详情) -->
	    <el-dialog v-model="showTraceDialog" title="查询执行记录" width="800px">
	      <div v-if="traceSteps.length" class="trace-table">
	        <el-table ref="traceTableRef" :data="traceSteps" stripe size="small" style="width: 100%" @row-click="toggleTraceRow">
	          <el-table-column type="expand" width="36">
	            <template #default="{ row }">
	              <div style="padding: 8px 12px;">
	                <!-- SQL -->
	                <pre v-if="row.type === 'sql' && traceSql" class="sql-inline">{{ traceSql }}</pre>
	                <!-- 预思考 -->
	                <div v-else-if="row.type === 'thinking' && row.thinkingData" class="trace-thinking">
	                  <div v-if="row.thinkingData.tables.length">选表: {{ row.thinkingData.tables.join('、') }}</div>
	                  <div v-if="row.thinkingData.expandedTables?.length">
	                    🕸️图谱扩展: {{ row.thinkingData.seedTables?.join('、') || '-' }} →
	                    <el-tag v-for="t in row.thinkingData.expandedTables" :key="t" size="small" type="success" style="margin: 0 2px">+{{ t }}</el-tag>
	                  </div>
	                  <div v-if="row.thinkingData.joinPathSection" class="trace-join-path">
	                    🕸️JOIN 路径:<pre class="join-path-pre">{{ row.thinkingData.joinPathSection }}</pre>
	                  </div>
	                  <div v-if="row.thinkingData.aggregation">聚合: {{ row.thinkingData.aggregation }}</div>
	                  <div v-for="c in row.thinkingData.caveats" :key="c" class="trace-caveat">⚠ {{ c }}</div>
	                </div>
	                <!-- 自愈对比 -->
	                <div v-else-if="row.type === 'heal' && row.healData" class="trace-heal">
	                  <div class="trace-heal-err">错误: {{ row.healData.error }}</div>
	                  <pre>{{ row.healData.after }}</pre>
	                </div>
	                <!-- 查询结果数据表格 -->
	                <div v-else-if="row.type === 'result' && traceColumns?.length" class="trace-result-table">
	                  <el-table
	                    :data="traceRows.slice(0, 100)"
	                    size="small" stripe border
	                    max-height="360"
	                    style="width: 100%"
	                  >
	                    <el-table-column
	                      v-for="col in traceColumns"
	                      :key="col"
	                      :prop="col" :label="col" min-width="100"
	                      show-overflow-tooltip
	                    />
	                  </el-table>
	                  <div v-if="traceRowCount > 100" class="result-more">
	                    共 {{ traceRowCount }} 行, 仅展示前 100 行
	                  </div>
	                </div>
	                <span v-else>{{ row.detail || '暂无详情' }}</span>
	              </div>
	            </template>
	          </el-table-column>
	          <el-table-column label="#" width="40" align="center">
	            <template #default="{ $index }">{{ $index + 1 }}</template>
	          </el-table-column>
	          <el-table-column label="步骤" prop="label" />
	          <el-table-column label="状态" width="70" align="center">
	            <template #default="{ row }">
	              <el-tag v-if="row.status === 'done'" type="success" size="small">完成</el-tag>
	              <el-tag v-else-if="row.status === 'failed'" type="danger" size="small">失败</el-tag>
	              <el-tag v-else type="info" size="small">进行中</el-tag>
	            </template>
	          </el-table-column>
		          <el-table-column label="耗时" width="80" align="center">
		            <template #default="{ row }">
		              <span v-if="row.duration">{{ row.duration }}ms</span>
		              <span v-else style="color:#c0c4cc">-</span>
		            </template>
		          </el-table-column>
		          <el-table-column label="LLM" width="120" align="center">
		            <template #default="{ row }">
		              <span v-if="row.llmCalls" class="trace-llm">🤖 ×{{ row.llmCalls }}<template v-if="row.llmTokens"> · {{ row.llmTokens }} tokens</template></span>
		              <span v-else style="color:#c0c4cc">-</span>
		            </template>
		          </el-table-column>
	        </el-table>
	      </div>
	    </el-dialog>

    <!-- 保存到看板弹窗 (V1 风格: 选择/新建看板 + 组件名) -->
    <el-dialog v-model="showSaveDashboard" title="保存到看板" width="480px" @open="loadDashboardList">
      <el-form label-width="80px" size="default">
        <el-form-item label="选择看板">
          <el-select v-model="saveDashId" placeholder="选择已有看板" style="width: 100%">
            <el-option
              v-for="d in dashboards"
              :key="d.id"
              :label="d.name"
              :value="d.id"
            />
          </el-select>
          <div class="or-divider">或者</div>
          <el-input v-model="newDashName" placeholder="输入新看板名称" />
        </el-form-item>
        <el-form-item label="组件名称">
          <el-input v-model="saveWidgetName" placeholder="给这个图表起个名字" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showSaveDashboard = false">取消</el-button>
        <el-button type="primary" :loading="savingDashboard" @click="doSaveToDashboard">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading, ChatDotRound, CircleCheck, CircleClose, Plus, ArrowDown, Download, QuestionFilled, Monitor } from '@element-plus/icons-vue'
import { chat, datasource, observability, semantic, dashboard, STREAM_URL, type ChatResponse, type ConversationItem, type DashboardItem } from '@/api'
import { extractErrorDetail } from '@/utils/error'
import { clearToken } from '@/composables/useAuth'
import { exportQueryToExcel } from '@/utils/exportExcel'
import * as echarts from 'echarts'

interface TraceStep {
  label: string
  status: 'running' | 'done' | 'failed'
  detail?: string
  duration?: number
  type?: 'sql' | 'result' | 'thinking' | 'heal'  // 步骤类型 (决定折叠内容)
  expandable?: boolean          // 是否可点击展开
  expanded?: boolean            // 当前是否展开
  llmCalls?: number             // 该步骤的 LLM 调用次数
  llmTokens?: number            // 该步骤消耗的 total tokens
  thinkingData?: {              // 预思考内容 (REF-001)
    tables: string[]
    aggregation: string
    caveats: string[]
    prevSqlReview: string
    // 图谱驱动 (前端展示扩展过程 + JOIN 路径)
    seedTables?: string[]         // 扩展前的种子表
    expandedTables?: string[]    // 图谱扩展新增的表
    joinPathSection?: string     // 预计算的 JOIN 路径文本
  }
  healData?: {                  // 自愈前后对比 (OBS-003)
    before: string
    after: string
    error: string
  }
}

interface Message {
  role: 'user' | 'assistant'
  text?: string
  reply?: string
  sql?: string              // 当前生效的 SQL (heal 后会更新)
  originalSql?: string      // LLM 最初生成的 SQL (不变, 供步骤展示)
  columns?: string[]
  rows?: Record<string, any>[]
  rowCount?: number
  truncated?: boolean
  chart?: Record<string, any> | null
  error?: string
  askUser?: { question: string; options: string[] | null } | null
  steps?: TraceStep[]
  done?: boolean
  pipelineCollapsed?: boolean
  tokenUsage?: {                // 本轮 token 统计 (T049 trace, complete 事件携带)
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    llm_calls: number
    nodes?: Record<string, { total_tokens: number; prompt_tokens: number; completion_tokens: number; call_count: number }>  // H5: per-node 分段
  } | null
  selfHealRounds?: number       // 本轮自愈次数 (T049 trace)
}

const input = ref('')
const messages = ref<Message[]>([])
const loading = ref(false)
const router = useRouter()
const route = useRoute()
const conversationId = ref<string | null>(null)
const dataSources = ref<{ id: string; name: string }[]>([])
const selectedDsId = ref<string>('')
const chatBody = ref<HTMLElement>()
const chartRefs: Record<number, HTMLElement> = {}
const chartInstances: Record<number, echarts.ECharts> = {}
// M3: SSE AbortController (组件卸载或超时时中断流)
let sseAbortController: AbortController | null = null
let sseTimeoutTimer: ReturnType<typeof setTimeout> | null = null
const conversations = ref<ConversationItem[]>([])
const convLoading = ref(false)
const sampleQuestions = ref<string[]>([])
const clarifyInput = ref('')
const inputHistory = ref<string[]>([])
const historyIdx = ref(-1)

// T048 + T061: slash command + token 预算
const slashCommands = [
  { cmd: '/new', desc: '新建对话' },
  { cmd: '/clear', desc: '清空当前对话' },
  { cmd: '/history', desc: '查看历史' },
  { cmd: '/ds', desc: '切换数据源 (如 /ds mydb)' },
  { cmd: '/sql', desc: '查看当前 SQL' },
  { cmd: '/explain', desc: '解释当前查询' },
  { cmd: '/help', desc: '查看命令帮助' },
]
const slashMenuVisible = ref(false)
// 完整流程弹窗 (V1 风格)
const showTraceDialog = ref(false)
const traceSteps = ref<any[]>([])
const traceSql = ref('')
const traceColumns = ref<string[]>([])
const traceRows = ref<Record<string, any>[]>([])
const traceRowCount = ref(0)
const traceTableRef = ref()
// T049: 当前对话累计 token (聚合所有消息的 tokenUsage)
const totalTokens = computed(() =>
  messages.value.reduce((sum, m) => sum + (m.tokenUsage?.total_tokens || 0), 0)
)

// ── 保存到看板 (V1 风格) ──
const showSaveDashboard = ref(false)
const saveDashId = ref('')
const newDashName = ref('')
const saveWidgetName = ref('')
const savingDashboard = ref(false)
const dashboards = ref<DashboardItem[]>([])
const saveMsgIdx = ref<number>(-1)  // 当前要保存的消息索引

function setChartRef(el: any, idx: number) {
  if (el) chartRefs[idx] = el
}

	// 步骤折叠: 点击 step label 展开/收起 (用 splice 触发响应式)
// 图表独立在外, 步骤展开只影响 SQL / 结果表格的显示
function toggleStep(msg: Message, si: number, _msgIdx?: number) {
  const step = msg.steps?.[si]
  if (!step?.expandable) return
  step.expanded = !step.expanded
  msg.steps = [...msg.steps!]  // 重新赋值数组触发 Vue 响应式
}

// 完整流程弹窗 (V1 PipelineTraceDialog 风格)
function openTraceDialog(msg: Message) {
  traceSteps.value = msg.steps || []
  traceSql.value = msg.sql || ''
  // 传结果数据供弹窗内 result 步骤展示表格 (msg.rows 已是对象数组, 保持原样)
  traceColumns.value = msg.columns || []
  traceRows.value = (msg.rows || []) as any[]
  traceRowCount.value = msg.rowCount || traceRows.value.length
  showTraceDialog.value = true
}

// 点击弹窗表格行展开/收起详情
function toggleTraceRow(row: any) {
  if (traceTableRef.value) {
    traceTableRef.value.toggleRowExpansion(row)
  }
}

// ── 保存到看板 (V1 风格) ──

async function loadDashboardList() {
  try {
    const { data } = await dashboard.list()
    dashboards.value = data
  } catch {
    dashboards.value = []
  }
}

function openSaveToDashboard(idx: number) {
  const msg = messages.value[idx]
  if (!msg?.chart && !msg?.columns?.length) {
    ElMessage.warning('当前查询无图表/数据可保存')
    return
  }
  saveMsgIdx.value = idx
  saveDashId.value = ''
  newDashName.value = ''
  saveWidgetName.value = msg.reply || msg.text || ''
  showSaveDashboard.value = true
}

async function doSaveToDashboard() {
  const msg = messages.value[saveMsgIdx.value]
  if (!msg) return

  const dashName = newDashName.value.trim()
  const selDashId = saveDashId.value

  // 校验: 必须选择或新建一个看板
  if (!selDashId && !dashName) {
    ElMessage.warning('请选择已有看板或输入新看板名称')
    return
  }
  const widgetName = saveWidgetName.value.trim()
  if (!widgetName) {
    ElMessage.warning('请输入组件名称')
    return
  }

  savingDashboard.value = true
  try {
    let targetDashId = selDashId

    // 新建看板
    if (!targetDashId && dashName) {
      const { data: newDash } = await dashboard.create({ name: dashName })
      targetDashId = newDash.id
      dashboards.value.unshift(newDash)
    }

    if (!targetDashId) {
      ElMessage.error('无法确定目标看板')
      return
    }

    // 保存 widget (实时查询模式: 只存 SQL + 数据源 + 图表类型, 不存结果)
    // chart_type 从当前图表的 series 提取 (供看板实时查询时做 AI 选型提示)
    const chartType = currentChartType(msg)

    if (!msg.sql) {
      ElMessage.warning('当前查询无 SQL, 无法保存到看板')
      return
    }

    await dashboard.addWidget(targetDashId, {
      question: widgetName,
      query_sql: msg.sql,
      datasource_id: selectedDsId.value,
      chart_type: chartType,
    })

    ElMessage.success('已保存到看板')
    showSaveDashboard.value = false
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (extractErrorDetail(e)))
  } finally {
    savingDashboard.value = false
  }
}

async function fetchDataSources() {
  try {
    const { data } = await datasource.list()
    dataSources.value = data.map((d: any) => ({ id: d.id, name: d.name }))
    if (dataSources.value.length && !selectedDsId.value) {
      selectedDsId.value = dataSources.value[0].id
      await fetchSampleQuestions()
    }
  } catch {
    ElMessage.error('加载数据源失败')
  }
}

// 从语义层拉取示例问题 (扫描时生成, 对标 V1 suggestedQuestions)
async function fetchSampleQuestions() {
  if (!selectedDsId.value) {
    sampleQuestions.value = []
    return
  }
  try {
    const { data } = await semantic.current(selectedDsId.value)
    const sq = data?.content?.sample_questions || []
    sampleQuestions.value = sq.length ? sq : [
      '本月销售额概览',
      '各分类的数量统计',
      '消费最高的前10条记录',
    ]
  } catch {
    sampleQuestions.value = []
  }
}

function askSuggestion(q: string) {
  input.value = q
  send()
}

async function fetchConversations() {
  convLoading.value = true
  try {
    const { data } = await observability.conversations()
    conversations.value = data
  } catch (e) {
    console.error('对话列表加载失败:', e)
  } finally {
    convLoading.value = false
  }
}

function startNewConversation() {
  // 释放 ECharts 实例, 防止内存泄漏
  Object.values(chartInstances).forEach(c => { try { c.dispose() } catch { /* ignore */ } })
  Object.keys(chartInstances).forEach(k => delete chartInstances[k])
  Object.keys(chartRefs).forEach(k => delete chartRefs[k])
  conversationId.value = null
  messages.value = []
}

async function loadConversation(convId: string) {
  // 释放旧图表实例, 防止切换对话时残留实例绑定已移除 DOM 导致新图表无法渲染
  Object.values(chartInstances).forEach(c => { try { c.dispose() } catch { /* ignore */ } })
  Object.keys(chartInstances).forEach(k => delete chartInstances[k])
  Object.keys(chartRefs).forEach(k => delete chartRefs[k])

  conversationId.value = convId
  messages.value = []
  try {
    const { data } = await observability.conversationDetail(convId)
    for (let ti = 0; ti < data.length; ti++) {
      const turn = data[ti]
      const st = turn.state || {}
      // 骨架行合并: start 事件落的骨架 (只有问题, 无 sql/reply), 若后续有同问题的完整行
      // 则跳过骨架 (完整行自带用户气泡), 仅当骨架后无完整行 (断流场景) 才显示骨架问题
      const isSkeleton = st.result_summary?.skeleton && !st.current_sql && !st.reply
      if (isSkeleton) {
        const next = data[ti + 1]
        const nextSt = next?.state || {}
        if (nextSt.question === st.question && (nextSt.current_sql || nextSt.reply)) {
          continue  // 完整行会显示, 跳过骨架避免重复问题
        }
      }
      // 恢复用户问题
      if (st.question) {
        messages.value.push({ role: 'user', text: st.question })
      }
      // 恢复助手回复 (有完整信息: SQL/结果/图表/确认问题)
      if (st.current_sql || st.reply || st.ask_user) {
        const rows = (st.rows_sample || []).map((row: any[]) => {
          const obj: Record<string, any> = {}
          ;(st.columns || []).forEach((col: string, ci: number) => { obj[col] = row[ci] })
          return obj
        })
        // 从 token_usage.nodes 提取各步骤的 LLM 调用信息
        const nodeUsage = st.token_usage?.nodes || {}
        const llmOf = (node: string) => {
          const n = nodeUsage[node]
          if (!n) return {}
          return { llmCalls: n.call_count ?? undefined, llmTokens: n.total_tokens ?? undefined }
        }
        // 从 step_durations 提取各步骤耗时
        const dur = st.step_durations || {}
        messages.value.push({
          role: 'assistant',
          reply: st.reply || undefined,
          sql: st.current_sql || undefined,
          columns: st.columns || undefined,
          rows,
          rowCount: st.result_summary?.row_count,
          chart: st.chart_option || undefined,
          // 全量回放: 主动确认内容 (刷新后还原 Agent 的确认问题 + 候选)
          askUser: st.ask_user ? { question: st.ask_user.question, options: st.ask_user.options || null } : null,
	          done: true,
	          metricHits: st.metric_hits || undefined,
	          steps: [
            {
              label: '意图识别', status: 'done' as const,
              detail: st.intent || undefined,
              duration: dur.intent ?? undefined,
              ...llmOf('intent'),
            },
            {
              label: 'Schema 检索', status: 'done' as const,
              detail: (st.current_tables || []).join(', ') || undefined,
              duration: dur.schema ?? undefined,
            },
            {
              label: '预思考', status: 'done' as const, type: 'thinking',
              expandable: true, expanded: false,
              duration: dur.thinking ?? undefined,
              ...llmOf('thinking'),
	              thinkingData: {
	                tables: st.thinking?.tables || [],
	                aggregation: st.thinking?.aggregation || '',
	                caveats: st.thinking?.caveats || [],
	                prevSqlReview: st.thinking?.prev_sql_review || '',
	                seedTables: st.thinking?.seed_tables || [],
	                expandedTables: st.thinking?.expanded_tables || [],
	                joinPathSection: st.thinking?.join_path_section || '',
	              },
            },
            {
              label: 'SQL 生成', status: 'done' as const, type: 'sql',
              expandable: true, expanded: false,
              duration: dur.generate_sql ?? undefined,
              ...llmOf('generate_sql'),
            },
            {
              label: '执行查询', status: 'done' as const,
              detail: `${st.result_summary?.row_count ?? 0} 行`,
              duration: dur.execute_sql ?? st.sql_duration_ms ?? undefined,
              type: 'result', expandable: true, expanded: false,
            },
            ...(nodeUsage.generate_chart ? [{
              label: '图表生成', status: 'done' as const,
              duration: dur.generate_chart ?? undefined,
              ...llmOf('generate_chart'),
            }] : []),
          ],
		          done: true,
		        })
      }
    }
    await scrollToBottom()
    // 渲染历史图表
    await nextTick()
    for (let i = 0; i < messages.value.length; i++) {
      if (messages.value[i].chart) renderChart(i)
    }
  } catch {
    ElMessage.error('加载对话失败')
  }
}

function formatTime(ts: string): string {
  if (!ts) return ''
  try {
    const d = new Date(ts)
    const now = new Date()
    if (d.toDateString() === now.toDateString()) {
      return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    }
    return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
  } catch {
    return ''
  }
}

// 乐观更新阶段的问题标题美化 (临时显示, complete 后被后端 LLM 标题覆盖)
// 规则: 去问号/语气词/前后标点 → 截到首个自然断点(逗号/句号/空格)保证语义完整 → 限 30 字
function prettifyTitle(q: string): string {
  let t = q.trim()
  // 去尾部问号 (中英文)
  t = t.replace(/[?？]+$/, '')
  // 循环剥离句首语气词 (复合前缀如"帮我看一下"需多轮剥离)
  while (true) {
    const m = t.match(/^(帮我|请帮|麻烦|能不能|可以|我想|给我|看一下|看一下吧|那个)\s*/)
    if (!m) break
    t = t.slice(m[0].length)
  }
  // 去句尾语气词 ($ 锚定, 只匹配一次)
  t = t.replace(/(吧|呢|啊|呀|哦|嘛|哈)+$/, '')
  t = t.trim()
  // 截到自然断点 (逗号/顿号/分号/句号/空格), 超过上限优先在断点截断
  const MAX = 30
  const MIN_BREAK = 8  // 断点位置不足此长度则视为太靠前, 直接硬截到 MAX
  if (t.length <= MAX) return t || '新对话'
  const slice = t.slice(0, MAX)
  const lastBreak = Math.max(
    slice.lastIndexOf('，'), slice.lastIndexOf('、'),
    slice.lastIndexOf(';'), slice.lastIndexOf(' '), slice.lastIndexOf('。'),
  )
  return (lastBreak > MIN_BREAK ? slice.slice(0, lastBreak) : slice).trim() || '新对话'
}

// ── 流式发送 (对标 V1 chatStore: fetch + ReadableStream 逐行解析) ──
async function send() {
  const q = input.value.trim()
  if (!q || loading.value || !selectedDsId.value) return

  // 立即设 loading, 防止双击竞态 (先于 push, 保证第二次 click 时 guard 生效)
  loading.value = true

  messages.value.push({ role: 'user', text: q })
  inputHistory.value.push(q)
  if (inputHistory.value.length > 50) inputHistory.value.shift()
  historyIdx.value = -1
  input.value = ''
  await scrollToBottom()

  // 创建占位 assistant 消息 (流式逐步填充)
  const assistantMsg: Message = {
    role: 'assistant',
    steps: [{ label: '意图识别', status: 'running' }],
    done: false,
    pipelineCollapsed: false,  // 查询中展开看进度, 完成后折叠
  }
  messages.value.push(assistantMsg)
  const msgIdx = messages.value.length - 1

  try {
    // M3: AbortController + 超时 (5 分钟), 防止后端挂起时前端无限等待
    sseAbortController = new AbortController()
    const SSE_TIMEOUT_MS = 5 * 60 * 1000
    const resetSseTimeout = () => {
      if (sseTimeoutTimer) clearTimeout(sseTimeoutTimer)
      sseTimeoutTimer = setTimeout(() => {
        sseAbortController?.abort()
        ElMessage.warning('查询超时，请稍后重试')
      }, SSE_TIMEOUT_MS)
    }
    resetSseTimeout()

    const token = localStorage.getItem('access_token')
    const response = await fetch(STREAM_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        question: q,
        data_source_id: selectedDsId.value,
        conversation_id: conversationId.value || undefined,
      }),
      signal: sseAbortController.signal,
    })

    if (!response.ok || !response.body) {
      // 401: token 失效, 跳转登录页
      if (response.status === 401) {
        loading.value = false
        clearToken()
        localStorage.removeItem('refresh_token')
        const { router } = await import('@/router')
        if (router.currentRoute.value.path !== '/login') {
          router.push({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
        }
        return
      }
      throw new Error(`HTTP ${response.status}`)
    }

    // ReadableStream 逐行解析 (对标 V1, 索引遍历不用 shift)
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let firstEvent = true

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      resetSseTimeout()  // M3: 每收到数据重置超时
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || '' // 保留不完整的尾行

      // 索引遍历 (V1 经验教训 #23: 不在遍历中 shift 数组)
      let i = 0
      while (i < lines.length) {
        if (lines[i].startsWith('event: ')) {
          const eventType = lines[i].slice(7).trim()
          i++
          if (i < lines.length && lines[i].startsWith('data: ')) {
            try {
              const data = JSON.parse(lines[i].slice(6))
              handleSSEEvent(eventType, data, msgIdx)
              if (firstEvent) {
                firstEvent = false
                loading.value = false // 首个事件到达, 关闭 "思考中"
              }
            } catch {
              // 跳过畸形行
            }
          }
        }
        i++
        if (firstEvent === false) await scrollToBottom()
      }
    }
    loading.value = false
    // M3: 清理超时计时器
    if (sseTimeoutTimer) { clearTimeout(sseTimeoutTimer); sseTimeoutTimer = null }
    sseAbortController = null
    await scrollToBottom()
    await nextTick()
    if (messages.value[msgIdx]?.chart) {
      nextTick(() => nextTick(() => renderChart(msgIdx)))
    }
    // 刷新侧边栏 (新对话标题)
    fetchConversations()
  } catch (e: any) {
    loading.value = false
    // 降级: 流式失败 → 非流式 (fail-closed)
    await sendFallback(q, msgIdx, e)
  }
}

function handleSSEEvent(type: string, data: any, msgIdx: number) {
  const msg = messages.value[msgIdx]
  if (!msg) return
  const steps = msg.steps || []

  // 从 SSE data 中提取 LLM 调用信息 (后端 emit 附带的 node_usage)
  const llmInfo = (data: any): { llmCalls?: number; llmTokens?: number } => {
    const nu = data?.node_usage
    if (!nu) return {}
    return {
      llmCalls: nu.call_count ?? undefined,
      llmTokens: nu.total_tokens ?? undefined,
    }
  }

  const updateStep = (label: string, status: 'done' | 'failed', extra?: Partial<TraceStep>) => {
    const step = steps.find(s => s.label === label)
    if (step) {
      step.status = status
      // 同步所有传入字段, 但 duration 只在有值时覆盖 (防 undefined 抹掉已有值)
      if (extra) {
        if (extra.duration == null) {
          const { duration, ...rest } = extra
          Object.assign(step, rest)
        } else {
          Object.assign(step, extra)
        }
      }
    }
  }

  switch (type) {
    case 'start':
      // 首事件: 立刻拿到 conversation_id (本地LLM慢, 不用等complete)
      if (data.conversation_id) {
        conversationId.value = data.conversation_id
        // 乐观更新: 后端 _persist 在 pipeline 末尾才落库, 此时列表接口还读不到新对话
        // 直接插入临时项 (标题前端规则美化), 等 complete 后 fetchConversations 拉真数据覆盖
        // (start 时调 fetchConversations 拉到的是旧数据, 反而会用空结果覆盖乐观项)
        if (!conversations.value.some(c => c.conversation_id === data.conversation_id)) {
          const userMsg = messages.value[msgIdx - 1]
          conversations.value.unshift({
            conversation_id: data.conversation_id,
            title: prettifyTitle(userMsg?.text || '新对话'),
            turn_count: 0,
            last_sql: '',
            last_tables: [],
            timestamp: new Date().toISOString(),
            total_prompt_tokens: 0,
            total_completion_tokens: 0,
            total_tokens: 0,
          })
        }
      }
      break
    case 'intent':
      updateStep('意图识别', 'done', { detail: data.intent, duration: data.duration_ms, ...llmInfo(data) })
      if (data.intent === 'GENERAL' || data.intent === 'EXPLANATION') {
        msg.reply = data.reply || ''
        msg.done = true
        break
      }
      // 继续管线: 推进到 schema 步骤
      steps.push({ label: 'Schema 检索', status: 'running' })
      break
    case 'schema':
      updateStep('Schema 检索', 'done', { detail: (data.tables || []).join(', '), duration: data.duration_ms, ...llmInfo(data) })
      steps.push({ label: '预思考', status: 'running' })
      break
    case 'sql': {
      if (data.error) {
        updateStep('SQL 生成', 'failed', { detail: data.error, ...llmInfo(data) })
        msg.error = data.error
        break
      }
      msg.sql = data.sql
      msg.originalSql = data.sql  // 保存初始 SQL, heal 后不覆盖
      const fewshotHint = data.fewshot_count > 0 ? `📚 命中 ${data.fewshot_count} 条相似示例` : ''
      updateStep('SQL 生成', 'done', { detail: fewshotHint, duration: data.duration_ms, type: 'sql', expandable: true, expanded: false, ...llmInfo(data) })
      steps.push({ label: '执行查询', status: 'running' })
      break
    }
    case 'data':
      msg.columns = data.columns
      msg.rows = (data.rows || []).map((row: any[]) => {
        const obj: Record<string, any> = {}
        data.columns.forEach((col: string, ci: number) => { obj[col] = row[ci] })
        return obj
      })
      msg.rowCount = data.row_count
      msg.truncated = data.truncated
      // 执行查询步骤: 可展开看结果表格 (默认折叠, 图表在外独立展示)
      updateStep('执行查询', 'done', { detail: `${data.row_count} 行`, type: 'result', expandable: true, expanded: false, duration: data.duration_ms })
      steps.push({ label: '图表生成', status: 'running' })
      break
    case 'heal':
      if (data.success) {
        msg.sql = data.sql
        // 自愈步骤: 展开后可看修复前后对比 (OBS-003)
        if (!steps.some(s => s.label === `自愈 #${data.retry}`)) {
          msg.selfHealRounds = (msg.selfHealRounds || 0) + 1  // T049: 累计自愈次数
          steps.push({
            label: `自愈 #${data.retry}`,
            status: 'done',
            detail: '已修复 SQL',
            duration: data.duration_ms,
		            type: 'heal',
		            expandable: true,
		            expanded: false,
            ...llmInfo(data),
            healData: {
              before: data.before_sql || '',
              after: data.sql || '',
              error: data.error || '',
            },
          })
        }
      } else {
        steps.push({ label: `自愈 #${data.retry}`, status: 'failed', detail: data.error, ...llmInfo(data) })
      }
      break
    case 'chart':
      updateStep('图表生成', 'done', { duration: data.duration_ms, ...llmInfo(data) })
      msg.chart = data.option
      // 图表出来了 = 查询实质完成, 立即隐藏 pipeline (不等 complete, 避免延迟)
      msg.done = true
      // 图表独立在 pipeline 外渲染。双重 nextTick 确保 v-if 的 DOM + ref 回调就绪
      if (data.option) {
        nextTick(() => nextTick(() => renderChart(msgIdx)))
      }
      break
    case 'clarify':
      msg.askUser = { question: data.question, options: data.options || null }
      break
    case 'persist_warning': {
      // E1 Task 2.4: 反哺失败非阻塞 toast 告知 (Fail-Closed, 不静默吞错)
      const questionText = data.question ? `『${data.question.slice(0, 15)}』` : ''
      ElMessage.warning({
        message: `查询${questionText}的后台保存失败 (${data.stage})，不影响结果`,
        duration: 5000,
        showClose: true,
      })
      break
    }
    case 'thinking':
      // 预思考 (REF-001): schema 事件已 push running 的预思考步骤, 这里更新为 done + 填充内容
      updateStep('预思考', 'done', {
        type: 'thinking',
        expandable: true,
        expanded: false,
        detail: (data.tables || []).join(',') || '',
        duration: data.duration_ms,
        ...llmInfo(data),
        thinkingData: {
          tables: data.tables || [],
          aggregation: data.aggregation || '',
          caveats: data.caveats || [],
          prevSqlReview: data.prev_sql_review || '',
          // 图谱驱动: 扩展过程 + JOIN 路径
          seedTables: data.seed_tables || [],
          expandedTables: data.expanded_tables || [],
          joinPathSection: data.join_path_section || '',
        },
      })
      // 预思考完成 → 推进到 SQL 生成
      steps.push({ label: 'SQL 生成', status: 'running' })
      break
    case 'complete':
      // 收尾: 所有 running 步骤标记为 done
      steps.forEach(s => { if (s.status === 'running') s.status = 'done' })
      msg.done = true
      // T049: 记录本轮 token 统计 (complete 事件携带)
      if (data.token_usage) {
        msg.tokenUsage = {
          prompt_tokens: data.token_usage.prompt_tokens || 0,
          completion_tokens: data.token_usage.completion_tokens || 0,
          total_tokens: data.token_usage.total_tokens || 0,
          llm_calls: data.token_usage.llm_calls || 0,
          nodes: data.token_usage.nodes,
        }
      }
	      if (data.conversation_id) conversationId.value = data.conversation_id
	      // 指标命中: 记录本次查询命中的业务指标
	      if (data.metric_hits?.length) {
	        msg.metricHits = data.metric_hits
	      }
	      if (!data.success && data.error && !msg.error && !msg.reply) {
        msg.error = data.error
      }
      break
    case 'error':
      steps.forEach(s => { if (s.status === 'running') s.status = 'failed' })
      msg.done = true
      msg.error = data.error || '未知错误'
      break
  }
  // 触发响应式 (重新赋值数组)
  msg.steps = [...steps]
}

// 降级: 流式失败时走非流式 chat.ask (fail-closed)
async function sendFallback(q: string, msgIdx: number, streamErr: any) {
  const msg = messages.value[msgIdx]
  if (!msg) return
  try {
    const { data } = await chat.ask({
      question: q,
      data_source_id: selectedDsId.value,
      conversation_id: conversationId.value || undefined,
    })
    conversationId.value = data.conversation_id
    msg.reply = data.reply || undefined
    msg.sql = data.sql || undefined
    msg.columns = data.columns
    msg.rows = (data.rows || []).map((row: any[]) => {
      const obj: Record<string, any> = {}
      data.columns.forEach((col: string, ci: number) => { obj[col] = row[ci] })
      return obj
    })
    msg.rowCount = data.row_count
    msg.truncated = data.truncated
    msg.chart = data.chart
    if (!data.success) msg.error = data.error || '查询失败'
    msg.askUser = data.ask_user
    // T049: 非流式降级也读 token_usage + 自愈次数 (ChatResponse 已携带)
    if (data.token_usage) {
      msg.tokenUsage = {
        prompt_tokens: data.token_usage.prompt_tokens || 0,
        completion_tokens: data.token_usage.completion_tokens || 0,
        total_tokens: data.token_usage.total_tokens || 0,
        llm_calls: data.token_usage.llm_calls || 0,
        nodes: data.token_usage.nodes,
      }
    }
    msg.selfHealRounds = data.self_heal_rounds || 0
    // 非流式降级: 同样构建 pipeline 步骤 (SQL/结果均收进折叠区, 默认展开结果)
    if (msg.sql) {
      const fewshotHint = data.fewshot_count > 0 ? `📚 命中 ${data.fewshot_count} 条相似示例` : ''
      msg.steps = [
        { label: 'SQL 生成', status: 'done' as const, detail: fewshotHint, type: 'sql', expandable: true, expanded: false },
      ]
      if (data.columns?.length || data.chart) {
        msg.steps.push({
          label: '执行查询', status: 'done' as const, detail: `${data.row_count} 行`,
          type: 'result', expandable: true, expanded: false,
        })
      }
	    }
		    msg.done = true
		    await nextTick()
	    if (msg.chart) nextTick(() => nextTick(() => renderChart(msgIdx)))
	    // 降级路径也需要刷新对话列表
	    fetchConversations()
	  } catch (e2: any) {
    msg.error = streamErr.message + ' | ' + (e2.response?.data?.detail || e2.message || '网络错误')
    msg.done = true
  }
}

function renderChart(idx: number, retries = 3) {
  const msg = messages.value[idx]
  if (!msg?.chart) return
  // table 类型不调 ECharts, 由模板直接渲染 HTML 表格
  if (msg.chart.chart_type === 'table') return
  // 0 行数据不渲染图表 (避免空坐标轴)
  if (!msg.rows?.length) return
  const el = chartRefs[idx]
  if (!el) {
    // ref 未就绪: DOM 可能还在更新中, 短延迟重试 (健壮性, 不只为特定场景)
    if (retries > 0) {
      setTimeout(() => renderChart(idx, retries - 1), 100)
    }
    return
  }
  // M4: 复用已有实例而非重新 init (避免内存泄漏)
  let chart = chartInstances[idx]
  if (!chart || chart.isDisposed()) {
    try {
      chart = echarts.init(el)
      chartInstances[idx] = chart
    } catch (e) {
      console.warn(`[ChatView] echarts.init failed for msg ${idx}:`, e)
      return
    }
  }
  try {
    // KPI gauge: 替换 formatter 为千分位格式化 (JSON option 不支持函数, 需前端注入)
    const opt = msg.chart
    if (opt.chart_type === 'kpi' && opt.series?.[0]) {
      opt.series[0].detail = { ...opt.series[0].detail, formatter: (v: any) => {
        const num = typeof v === 'object' ? v.value : v
        return num != null ? Number(num).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '-'
      }}
    }
    chart.setOption(opt, true)  // true = notMerge, 替换而非合并
  } catch (e) {
    console.warn(`[ChatView] chart.setOption failed for msg ${idx}:`, e)
    try { chart.dispose() } catch { /* ignore */ }
    delete chartInstances[idx]
  }
}

/** 当前图表类型 (从 series 推断) */
/** 获取当前图表类型 (从 series[0].type 推断) */
function currentChartType(msg: Message): string {
  // 优先使用 chart_type 字段 (kpi/table 由后端显式标记)
  if (msg.chart?.chart_type) return msg.chart.chart_type
  if (!msg.chart?.series?.length) return 'bar'
  return msg.chart.series[0].type || 'bar'
}

// 导出查询结果到 Excel (数据 sheet + 图表 sheet)
// 对标 V1 API-05 + CHART-09: 数据 + 图表一起导出
async function exportChart(idx: number) {
  const msg = messages.value[idx]
  if (!msg) return
  // 无数据列时无法导出数据 sheet, 提示用户
  if (!msg.columns?.length) {
    ElMessage.warning('当前查询无数据可导出')
    return
  }
  const chart = chartInstances[idx]
  try {
    await exportQueryToExcel({
      question: msg.text || msg.reply || '查询结果',
      columns: msg.columns,
      rows: msg.rows || [],
      chart: chart || undefined,
    })
    ElMessage.success('已导出 Excel')
  } catch (e: any) {
    ElMessage.error('导出失败: ' + (e.message || '未知错误'))
  }
}

function navigateHistory(dir: number) {
  if (!inputHistory.value.length) return
  if (historyIdx.value === -1 && dir > 0) return
  historyIdx.value = Math.min(Math.max(historyIdx.value + dir, -1), inputHistory.value.length - 1)
  if (historyIdx.value === -1) {
    input.value = ''
  } else {
    input.value = inputHistory.value[inputHistory.value.length - 1 - historyIdx.value]
  }
}

// T048: 输入变化 → 检测 slash command 菜单
function onInputChange(val: string) {
  // T061: 更宽松的菜单触发 — 支持带参数的命令 (如 /ds mydb, /chart bar)
  const trimmed = val.trimStart()
  slashMenuVisible.value = trimmed.startsWith('/') && trimmed.length <= 20
}

// T048 + T061: 执行 slash command (支持带参数)
function runSlashCommand(cmd: string) {
  slashMenuVisible.value = false
  input.value = ''
  const parts = cmd.split(' ')
  const base = parts[0]
  const arg = parts.slice(1).join(' ')

  switch (base) {
    case '/new':
    case '/clear':
      startNewConversation()
      ElMessage.success(base === '/new' ? '已新建对话' : '已清空对话')
      break
    case '/history':
      router.push('/history')
      break
    case '/help':
      ElMessage.info('可用命令: /new 新建 · /clear 清空 · /history 历史 · /ds <名称> 切换数据源 · /sql 查看SQL · /explain 解释查询 · /help 帮助')
      break
    // T061: /ds 切换数据源
    case '/ds':
      if (arg) {
        const ds = dataSources.value.find(d => d.name === arg || d.id === arg)
        if (ds) {
          selectedDsId.value = ds.id
          ElMessage.success(`已切换到数据源: ${ds.name}`)
        } else {
          ElMessage.warning(`数据源 "${arg}" 不存在。可用: ${dataSources.value.map(d => d.name).join(', ')}`)
        }
      } else {
        ElMessage.info(`当前数据源: ${dataSources.value.find(d => d.id === selectedDsId.value)?.name || '未选择'}\n可用: ${dataSources.value.map(d => d.name).join(', ')}`)
      }
      break
    // T061: /sql 查看/复制当前 SQL
    case '/sql':
      const lastSql = findLastSql()
      if (lastSql) {
        // 复制到剪贴板 + 展示
        navigator.clipboard.writeText(lastSql).catch(() => {})
        ElMessage.success({ message: `当前 SQL 已复制到剪贴板`, duration: 3000 })
        // 追加一条消息展示 SQL
        messages.value.push({ role: 'assistant', text: `**当前 SQL:**\n\`\`\`sql\n${lastSql}\n\`\`\`` })
      } else {
        ElMessage.info('当前对话暂无 SQL')
      }
	      break
	    // T061: /explain 解释当前查询
    case '/explain':
      const explainSql = findLastSql()
      if (explainSql) {
        // 用消息展示 SQL 解释 (不调 LLM, 就是对 SQL 的简要说明)
        messages.value.push({ role: 'assistant', text: `**查询解释:**\n当前 SQL:\n\`\`\`sql\n${explainSql}\n\`\`\`\n数据源: ${dataSources.value.find(d => d.id === selectedDsId.value)?.name || '未选择'}` })
      } else {
        ElMessage.info('当前对话暂无可解释的查询')
      }
      break
  }
}

// T061: 辅助函数 — 找最后一条有 SQL 的消息
function findLastSql(): string | null {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const m = messages.value[i]
    if (m.sql) return m.sql
  }
  return null
}

	// 用户回答 Agent 的澄清问题 (ARC-03: 暂停后恢复对话流)
// 用户选择候选或输入补充后, 把答案作为新问题发送 (带上 conversation_id 续接上下文)
function answerClarify(answer: string) {
  const ans = (answer || '').trim()
  if (!ans) return
  // 清掉当前消息的 askUser (避免表单残留)
  const lastMsg = messages.value[messages.value.length - 1]
  if (lastMsg) lastMsg.askUser = null
  clarifyInput.value = ''
  input.value = ans
  send()
}

async function scrollToBottom() {
  await nextTick()
  if (chatBody.value) {
    chatBody.value.scrollTop = chatBody.value.scrollHeight
  }
}

// M4: resize 处理 — 遍历所有 ECharts 实例调用 resize()
function handleWindowResize() {
  for (const chart of Object.values(chartInstances)) {
    if (!chart.isDisposed()) chart.resize()
  }
}

onMounted(() => {
  fetchDataSources()
  fetchConversations()
  // M4: 监听窗口 resize → ECharts 自适应
  window.addEventListener('resize', handleWindowResize)
  // 看板页"来源对话"跳转: 读取 conv query 自动加载该对话
  const convId = route.query.conv as string
  if (convId) {
    loadConversation(convId)
  }
})

// M3 + M4: 组件卸载时清理 SSE + ECharts
onBeforeUnmount(() => {
  // M3: 中断正在进行的 SSE 流
  if (sseAbortController) {
    sseAbortController.abort()
    sseAbortController = null
  }
  if (sseTimeoutTimer) {
    clearTimeout(sseTimeoutTimer)
    sseTimeoutTimer = null
  }
  // M4: dispose 所有 ECharts 实例
  for (const chart of Object.values(chartInstances)) {
    if (!chart.isDisposed()) chart.dispose()
  }
  Object.keys(chartInstances).forEach(k => delete chartInstances[Number(k)])
  window.removeEventListener('resize', handleWindowResize)
})

// 数据源切换时刷新示例问题
watch(selectedDsId, () => { fetchSampleQuestions() })
</script>

<style scoped>
/* 外层水平 flex: 侧边栏 + 主对话区 (根因修复: 扣除 56px 导航栏高度) */
.chat-layout {
  display: flex;
  height: calc(100vh - 56px);
  overflow: hidden;
}

/* ── 左侧对话历史侧边栏 (对标 V1: 渐变色 active + 柔和背景) ── */
.chat-sidebar {
  width: 260px;
  flex-shrink: 0;
  border-right: 1px solid #ebeef5;
  background: #fafbfc;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.new-chat-btn {
  margin: 12px;
}
.conv-list {
  flex: 1;
  overflow-y: auto;
  min-height: 0; /* flex 溢出保护 */
  padding: 4px 0;
}
.conv-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  cursor: pointer;
  border-right: 3px solid transparent;
  transition: background 0.15s;
}
.conv-item:hover { background: #eef0f3; }
.conv-item.active { background: #e8edf3; border-right-color: #667eea; }
.conv-icon { color: #909399; flex-shrink: 0; }
.conv-text { flex: 1; min-width: 0; }
.conv-title {
  font-size: 0.85rem;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.conv-meta { font-size: 0.7rem; color: #909399; margin-top: 2px; }
.conv-empty { text-align: center; color: #909399; padding: 30px 0; font-size: 0.82rem; }

/* ── 主对话区 (根因修复: flex 列 + min-height:0 防溢出) ── */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}
.chat-header {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 20px; border-bottom: 1px solid #ebeef5; background: #fff;
}
.title { font-size: 1.1rem; font-weight: bold; }
.conv-id-hint { font-size: 0.7rem; color: #c0c4cc; margin-left: 8px; font-family: monospace; }
.chat-body { flex: 1; overflow-y: auto; padding: 24px 20px; min-height: 0; background: #f5f7fa; }
.empty-hint { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 16px; }

/* ── 消息布局 (对标 V1: flex 左右分开 + 真正的气泡容器) ── */
.message {
  display: flex;
  margin-bottom: 16px;
}
.message.user { justify-content: flex-end; }
.message.assistant { justify-content: flex-start; }
.message-content {
  max-width: 80%;
  padding: 12px 16px;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}
.message.user .message-content {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
}
.message.assistant .message-content { width: 80%; }
.message-text { margin-bottom: 8px; line-height: 1.6; white-space: pre-wrap; }
.empty-reply { color: #909399; font-size: 0.9rem; }

/* 建议问题标签 (对标 V1 suggestedQuestions) */
.suggestions {
  display: flex; flex-wrap: wrap; gap: 8px;
  justify-content: center; max-width: 560px;
}
.suggestion-chip {
  padding: 8px 16px;
  background: #fff;
  border: 1px solid #dcdfe6;
  border-radius: 20px;
  font-size: 0.85rem;
  color: #606266;
  cursor: pointer;
  transition: all 0.2s;
}
.suggestion-chip:hover {
  border-color: #667eea;
  color: #667eea;
  background: #f5f7ff;
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(102, 126, 234, 0.15);
}
.result-box { margin: 4px 0 8px; }
.result-meta { font-size: 0.8rem; color: #909399; margin-bottom: 6px; }
.chart-box { margin-bottom: 12px; }
.chart-table-box { background: #fafafa; border-radius: 6px; padding: 12px; }
.chart-table-wrap { overflow-x: auto; }
.chart-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
.chart-table th { background: #f5f7fa; padding: 6px 10px; text-align: left; border-bottom: 2px solid #ebeef5; font-weight: 600; white-space: nowrap; }
.chart-table td { padding: 5px 10px; border-bottom: 1px solid #ebeef5; white-space: nowrap; }
.chart-table tbody tr:hover { background: #f5f7fa; }
.table-more { font-size: 0.75rem; color: #909399; margin-top: 6px; text-align: center; }
/* 弹窗内结果表格 */
.trace-result-table { margin-top: 4px; overflow-x: auto; }
.trace-result-table .el-table { border-radius: 4px; }
.trace-result-table :deep(.el-table__body-wrapper) { overflow-x: auto; overflow-y: auto; }
.trace-result-table :deep(.el-table .cell) { white-space: nowrap; line-height: 1.8; }
.result-more {
  text-align: center;
  font-size: 0.75rem;
  color: #909399;
  padding: 6px 0;
}
/* 图表底部操作栏: 左侧导出+看板, 右侧查询流程开关 */
.chart-toolbar { display: flex; justify-content: space-between; align-items: center; margin-top: 4px; }
.chart-toolbar-left { display: flex; gap: 4px; align-items: center; }
.chart-toolbar .el-button { color: #909399; }
.chart-toolbar .el-button:hover { color: #667eea; }
.chart-toolbar .expand-toggle { margin-left: 2px; }

/* 保存到看板弹窗 */
.or-divider {
  text-align: center;
  color: #c0c4cc;
  font-size: 0.8rem;
  margin: 8px 0;
}
.ask-user-box { margin-bottom: 12px; }
.ask-options { margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap; }
.ask-input { margin-top: 8px; display: flex; gap: 8px; }
.ask-question { font-size: 0.88rem; color: #e6a23c; margin-bottom: 4px; }
.error-box { margin-bottom: 8px; }
/* 预思考展开内容 (REF-001) */
.thinking-box { background: #f5f7fa; border-radius: 6px; padding: 8px 12px; margin-bottom: 8px; font-size: 0.8rem; }
.think-section { margin-bottom: 4px; line-height: 1.5; color: #606266; }
.think-label { font-weight: 600; color: #909399; margin-right: 4px; }
.think-caveat { display: inline-block; background: #fdf6ec; color: #e6a23c; border-radius: 4px; padding: 1px 6px; margin: 2px 4px 2px 0; font-size: 0.75rem; }
/* 自愈前后对比 (OBS-003) */
.heal-box { background: #f5f7fa; border-radius: 6px; padding: 8px 12px; margin-bottom: 8px; font-size: 0.8rem; }
.heal-error { color: #f56c6c; margin-bottom: 6px; font-size: 0.75rem; }
.heal-diff { display: flex; gap: 12px; }
.heal-before, .heal-after { flex: 1; min-width: 0; }
.heal-tag { display: inline-block; font-size: 0.7rem; font-weight: 600; border-radius: 3px; padding: 1px 6px; margin-bottom: 4px; }
.heal-tag.del { background: #fde2e2; color: #f56c6c; }
.heal-tag.add { background: #e1f3d8; color: #67c23a; }
.heal-box pre { margin: 0; font-size: 0.75rem; white-space: pre-wrap; word-break: break-all; max-height: 120px; overflow-y: auto; }

/* ── Pipeline 竖排步骤条 (V1 Dify 风格: 执行中实时显示) ── */
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
  transition: opacity 0.3s ease;
}
.pipeline-step.failed { opacity: 0.7; }
.step-icon {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}
.step-done { color: #67c23a; font-size: 16px; }
.step-failed { color: #f56c6c; font-size: 16px; }
.step-content { flex: 1; min-width: 0; }
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
.step-dur {
  color: #c0c4cc;
  font-size: 0.7rem;
  flex-shrink: 0;
  margin-top: 2px;
}
.step-llm {
  color: #8c9eff;
  font-size: 0.65rem;
  flex-shrink: 0;
  margin-top: 2px;
  margin-left: 4px;
}
.trace-llm {
  color: #8c9eff;
  font-size: 0.75rem;
}
/* Pipeline 步骤展开区域 */
.step-content.clickable {
  cursor: pointer;
}
.expand-icon {
  font-size: 0.7rem;
  margin-left: 4px;
  transition: transform 0.2s;
}
.expand-icon .rotated {
  transform: rotate(-90deg);
}
.step-expand {
  margin: 0 0 8px 28px;
  padding: 0 8px;
}
.thinking-detail {
  font-size: 0.78rem;
  color: #606266;
  background: #f5f7fa;
  padding: 8px 12px;
  border-radius: 6px;
}
.thinking-section {
  margin-bottom: 4px;
}
.thinking-label {
  color: #909399;
  font-weight: 500;
}
.thinking-join {
  margin-top: 4px;
}
.join-path-pre {
  display: inline-block;
  margin: 2px 0 0;
  padding: 4px 8px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  font-size: 0.72rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 120px;
  overflow-y: auto;
}
.trace-join-path {
  margin-top: 4px;
}
.heal-compare {
  display: flex;
  gap: 12px;
}
.heal-col {
  flex: 1;
  min-width: 0;
}
.heal-label {
  font-size: 0.75rem;
  color: #909399;
  margin-bottom: 2px;
  display: block;
}
.heal-sql {
  font-size: 0.72rem !important;
  padding: 6px 10px !important;
}
.heal-sql.healed {
  color: #a5d6ff;
  border-left: 3px solid #67c23a;
}

/* SQL 深色代码块 (V1 风格: 黑底 + 蓝字) */
.sql-inline {
  margin: 4px 0 8px;
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

/* trace 弹窗内预思考/自愈 */
.trace-thinking { font-size: 12px; line-height: 1.6; color: #606266; }
.trace-thinking > div { margin-bottom: 2px; }
.trace-caveat { display: block; color: #e6a23c; margin: 2px 0; }
.trace-heal { font-size: 12px; }
.trace-heal-err { color: #f56c6c; margin-bottom: 4px; }
.trace-heal pre {
  margin: 0; padding: 6px 10px; background: #1e1e1e; color: #a5d6ff;
  font-size: 12px; border-radius: 6px; white-space: pre-wrap; word-break: break-all;
}
/* T049: token 用量汇总行 (pipeline 底部) */
.trace-summary {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #e4e7ed;
  font-size: 0.72rem;
  color: #909399;
  text-align: right;
}
.metric-hits-bar {
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px dashed #e4e7ed;
  font-size: 0.72rem;
  color: #909399;
  text-align: right;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  flex-wrap: wrap;
}
.metric-hits-label {
  color: #909399;
  margin-right: 2px;
}
.metric-hit-tag {
  font-size: 0.68rem;
}
.metric-hit-table {
  color: #c0c4cc;
  margin-left: 3px;
  font-size: 0.62rem;
}
.metric-hit-more {
  color: #c0c4cc;
  font-size: 0.68rem;
}
.token-nodes {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  justify-content: flex-end;
  margin-top: 2px;
}
.token-node-tag {
  background: #f4f4f5;
  border-radius: 3px;
  padding: 0 4px;
  font-size: 0.68rem;
  color: #606266;
}

.chat-input {
  display: flex; gap: 10px; padding: 16px 20px;
  border-top: 1px solid #ebeef5; background: #fff;
  align-items: flex-end;
}
.chat-input .el-button {
  height: 60px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
}
.chat-input .el-button:hover { opacity: 0.9; }

/* T048: 输入区容器 (相对定位, 承载 slash 菜单 + token 预算) */
.input-wrap { position: relative; flex: 1; }
/* T048: slash command 弹出菜单 */
.slash-menu {
  position: absolute;
  bottom: 100%;
  left: 0;
  margin-bottom: 4px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
  z-index: 10;
  min-width: 220px;
  overflow: hidden;
}
.slash-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 14px; cursor: pointer; font-size: 0.85rem;
}
.slash-item:hover { background: #f5f7fa; }
.slash-item code {
  background: #f0f2f5; border-radius: 4px;
  padding: 1px 6px; color: #667eea; font-size: 0.8rem;
}
.slash-desc { color: #909399; font-size: 0.78rem; }
/* T048: token 预算 (输入框右上角) */
.token-budget {
  position: absolute;
  top: -18px;
  right: 4px;
  font-size: 0.68rem;
  color: #c0c4cc;
  pointer-events: none;
}

/* T047: Agent 状态徽标 (ask_user 表单上方) */
.ask-status-row {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 8px;
}
.ask-status-token { font-size: 0.72rem; color: #909399; }

</style>
