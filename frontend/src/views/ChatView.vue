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
        <el-select v-model="selectedDsId" placeholder="选择数据源" size="small" style="width: 200px">
          <el-option v-for="ds in dataSources" :key="ds.id" :label="ds.name" :value="ds.id" />
        </el-select>
        <el-button text size="small" @click="$router.push('/datasources')">数据源</el-button>
        <el-button text size="small" @click="$router.push('/semantic')">语义层</el-button>
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

              <!-- Pipeline 折叠区: SQL / 结果表格 收在里面 (默认折叠); 图表在外独立展示 -->
              <div v-if="msg.steps?.length" class="pipeline">
                <div class="pipeline-header-row" @click="togglePipeline(msg, idx)" style="cursor: pointer; user-select: none;">
                  <span class="pipeline-title">查询流程</span>
                  <span v-if="msg.rowCount != null" class="pipeline-summary">{{ msg.rowCount }} 行结果</span>
                  <el-icon class="expand-toggle"><ArrowDown v-if="msg.pipelineCollapsed" /><ArrowUp v-else /></el-icon>
                </div>
                <template v-if="!msg.pipelineCollapsed">
                <div
                  v-for="(s, si) in msg.steps" :key="si"
                  :class="['pipeline-step', s.status]"
                >
                  <div class="step-icon">
                    <el-icon v-if="s.status === 'running'" class="is-loading"><Loading /></el-icon>
                    <el-icon v-else-if="s.status === 'done'" class="step-done"><CircleCheck /></el-icon>
                    <el-icon v-else class="step-failed"><CircleClose /></el-icon>
                  </div>
                  <div class="step-content">
                    <div class="step-label" :class="{ clickable: s.expandable }" @click="toggleStep(msg, si, idx)">
                      {{ s.label }}
                      <span v-if="s.detail" class="step-detail-inline">{{ s.detail }}</span>
                      <el-icon v-if="s.expandable" class="expand-toggle"><ArrowDown v-if="!s.expanded" /><ArrowUp v-else /></el-icon>
                    </div>
                    <!-- SQL 折叠内容 -->
                    <div v-if="s.expanded && s.type === 'sql' && msg.sql" class="sql-box">
                      <div class="sql-label">SQL</div>
                      <pre><code>{{ msg.sql }}</code></pre>
                    </div>
                    <!-- 执行查询步骤展开后: 结果表格 (默认折叠, 图表在外独立展示) -->
                    <div v-if="s.expanded && s.type === 'result' && msg.columns?.length" class="result-box">
                      <div class="result-meta">
                        {{ msg.rowCount }} 行{{ msg.truncated ? ' (已截断)' : '' }}
                      </div>
                      <el-table :data="msg.rows" size="small" border max-height="400">
                        <el-table-column
                          v-for="col in msg.columns" :key="col"
                          :prop="String(col)" :label="String(col)" min-width="100"
                        />
                      </el-table>
                    </div>
                    <!-- 预思考步骤展开后: 选表理由+聚合+陷阱 (REF-001) -->
                    <div v-if="s.expanded && s.type === 'thinking' && s.thinkingData" class="thinking-box">
                      <div v-if="s.thinkingData.tables.length" class="think-section">
                        <span class="think-label">选表:</span> {{ s.thinkingData.tables.join('、') }}
                      </div>
                      <div v-if="s.thinkingData.aggregation" class="think-section">
                        <span class="think-label">聚合:</span> {{ s.thinkingData.aggregation }}
                      </div>
                      <div v-if="s.thinkingData.caveats.length" class="think-section">
                        <span class="think-label">注意:</span>
                        <span v-for="c in s.thinkingData.caveats" :key="c" class="think-caveat">{{ c }}</span>
                      </div>
                    </div>
                    <!-- 自愈步骤展开后: 修复前后 SQL 对比 (OBS-003) -->
                    <div v-if="s.expanded && s.type === 'heal' && s.healData" class="heal-box">
                      <div class="heal-error">错误: {{ s.healData.error }}</div>
                      <div class="heal-diff">
                        <div class="heal-before"><span class="heal-tag del">修复前</span><pre>{{ s.healData.before }}</pre></div>
                        <div class="heal-after"><span class="heal-tag add">修复后</span><pre>{{ s.healData.after }}</pre></div>
                      </div>
                    </div>
                  </div>
                  <span v-if="s.duration" class="step-dur">{{ s.duration }}ms</span>
                </div>
                </template>
                <!-- T049: token 用量汇总 (本轮请求级, complete 事件携带) -->
                <div v-if="msg.tokenUsage" class="trace-summary">
                  🔥 {{ msg.tokenUsage.total_tokens }} tokens
                  (输入 {{ msg.tokenUsage.prompt_tokens }} / 输出 {{ msg.tokenUsage.completion_tokens }})
                  · {{ msg.tokenUsage.llm_calls }} 次 LLM 调用
                  <span v-if="msg.selfHealRounds"> · 自愈 {{ msg.selfHealRounds }} 轮</span>
                </div>
              </div>

              <!-- BI 图表 (独立展示在 pipeline 外, 始终可见) -->
              <div v-if="msg.chart" class="chart-box">
                <div :ref="(el: any) => setChartRef(el, idx)" style="width: 100%; height: 350px"></div>
              </div>

              <!-- 反馈按钮组 (FBK-001: 查询完成后可点赞/点踩) -->
              <div v-if="msg.done && !msg.error" class="feedback-row">
                <span class="feedback-label">这个结果有帮助吗?</span>
                <el-button
                  text size="small"
                  :type="msg.feedbackGiven === 'like' ? 'success' : ''"
                  @click="giveFeedback(msg, 'like')"
                >👍</el-button>
                <el-button
                  text size="small"
                  :type="msg.feedbackGiven === 'dislike' ? 'danger' : ''"
                  @click="giveFeedback(msg, 'dislike')"
                >👎</el-button>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- PERF-03: 异步任务进度卡片 -->
      <div v-if="asyncTask" class="async-task-card">
        <div class="async-head">
          <el-icon v-if="asyncTask.status === 'running'" class="is-loading"><Loading /></el-icon>
          <el-icon v-else-if="asyncTask.status === 'done'" class="step-done"><CircleCheck /></el-icon>
          <el-icon v-else class="step-failed"><CircleClose /></el-icon>
          <span class="async-q">{{ asyncTask.current_stage || asyncTask.status }}</span>
          <el-progress :percentage="asyncTask.progress" :status="asyncTask.status === 'failed' ? 'exception' : (asyncTask.status === 'done' ? 'success' : undefined)" style="flex: 1; margin: 0 12px" />
          <el-button v-if="asyncTask.status === 'running' || asyncTask.status === 'pending'" size="small" text type="danger" @click="cancelAsyncTask">取消</el-button>
        </div>
      </div>

      <!-- 输入区 (T048: slash command + token 预算) -->
      <div class="chat-input">
        <!-- PERF-03: 异步执行开关 -->
        <el-switch v-model="asyncMode" active-text="异步" inline-prompt style="margin-right: 8px" />
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
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading, ChatDotRound, CircleCheck, CircleClose, Plus, ArrowDown, ArrowUp } from '@element-plus/icons-vue'
import { chat, datasource, observability, semantic, feedback as feedbackApi, asyncQuery, STREAM_URL, type ChatResponse, type ConversationItem, type AsyncTask } from '@/api'
import * as echarts from 'echarts'

interface TraceStep {
  label: string
  status: 'running' | 'done' | 'failed'
  detail?: string
  duration?: number
  type?: 'sql' | 'result' | 'thinking' | 'heal'  // 步骤类型 (决定折叠内容)
  expandable?: boolean          // 是否可点击展开
  expanded?: boolean            // 当前是否展开
  thinkingData?: {              // 预思考内容 (REF-001)
    tables: string[]
    aggregation: string
    caveats: string[]
    prevSqlReview: string
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
  sql?: string
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
  feedbackGiven?: 'like' | 'dislike' | null  // 用户已给的反馈 (FBK-001)
  tokenUsage?: {                // 本轮 token 统计 (T049 trace, complete 事件携带)
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    llm_calls: number
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
const conversations = ref<ConversationItem[]>([])
const convLoading = ref(false)
const sampleQuestions = ref<string[]>([])
const clarifyInput = ref('')
const inputHistory = ref<string[]>([])
const historyIdx = ref(-1)

// T048: slash command + token 预算
const slashCommands = [
  { cmd: '/new', desc: '新建对话' },
  { cmd: '/clear', desc: '清空当前对话' },
  { cmd: '/history', desc: '查看历史' },
  { cmd: '/help', desc: '查看命令帮助' },
]
const slashMenuVisible = ref(false)
// T049: 当前对话累计 token (聚合所有消息的 tokenUsage)
const totalTokens = computed(() =>
  messages.value.reduce((sum, m) => sum + (m.tokenUsage?.total_tokens || 0), 0)
)
// PERF-03: 异步查询
const asyncMode = ref(false)
const asyncTask = ref<AsyncTask | null>(null)
let _pollTimer: ReturnType<typeof setInterval> | null = null

function setChartRef(el: any, idx: number) {
  if (el) chartRefs[idx] = el
}

// Pipeline 整体展开/折叠 (图表独立在外, 不受此影响)
function togglePipeline(msg: Message, _msgIdx: number) {
  msg.pipelineCollapsed = !msg.pipelineCollapsed
}

// 步骤折叠: 点击 step label 展开/收起 (用 splice 触发响应式)
// 图表独立在外, 步骤展开只影响 SQL / 结果表格的显示
function toggleStep(msg: Message, si: number, _msgIdx?: number) {
  const step = msg.steps?.[si]
  if (!step?.expandable) return
  step.expanded = !step.expanded
  msg.steps = [...msg.steps!]  // 重新赋值数组触发 Vue 响应式
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
  } catch {
    // 加载失败不阻塞
  } finally {
    convLoading.value = false
  }
}

function startNewConversation() {
  conversationId.value = null
  messages.value = []
}

async function loadConversation(convId: string) {
  conversationId.value = convId
  messages.value = []
  try {
    const { data } = await observability.conversationDetail(convId)
    for (const turn of data) {
      const st = turn.state || {}
      // 恢复用户问题
      if (st.question) {
        messages.value.push({ role: 'user', text: st.question })
      }
      // 恢复助手回复 (有完整信息: SQL/结果/图表)
      if (st.current_sql || st.reply) {
        const rows = (st.rows_sample || []).map((row: any[]) => {
          const obj: Record<string, any> = {}
          ;(st.columns || []).forEach((col: string, ci: number) => { obj[col] = row[ci] })
          return obj
        })
        messages.value.push({
          role: 'assistant',
          reply: st.reply || undefined,
          sql: st.current_sql || undefined,
          columns: st.columns || undefined,
          rows,
          rowCount: st.result_summary?.row_count,
          chart: st.chart_option || undefined,
          steps: [
            { label: 'SQL 生成', status: 'done' as const, type: 'sql', expandable: true, expanded: false },
            { label: '执行查询', status: 'done' as const, detail: `${st.result_summary?.row_count ?? 0} 行`, type: 'result', expandable: true, expanded: false },
          ],
          done: true,
          pipelineCollapsed: true,
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

// ── 流式发送 (对标 V1 chatStore: fetch + ReadableStream 逐行解析) ──
async function send() {
  const q = input.value.trim()
  if (!q || loading.value || !selectedDsId.value) return

  // PERF-03: 异步模式走 sendAsync
  if (asyncMode.value) {
    await sendAsync(q)
    return
  }

  messages.value.push({ role: 'user', text: q })
  inputHistory.value.push(q)
  if (inputHistory.value.length > 50) inputHistory.value.shift()
  historyIdx.value = -1
  input.value = ''
  loading.value = true
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
    })

    if (!response.ok || !response.body) {
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

  const updateStep = (label: string, status: 'done' | 'failed', extra?: Partial<TraceStep>) => {
    const step = steps.find(s => s.label === label)
    if (step) {
      step.status = status
      // 同步所有传入字段 (detail/duration/type/expandable/expanded),
      // 不能只挑两个字段——否则 expandable/type 永远落不到 step 上, SQL 无法展开
      if (extra) Object.assign(step, extra)
    }
  }

  switch (type) {
    case 'intent':
      updateStep('意图识别', 'done', { detail: data.intent, duration: data.duration_ms })
      if (data.intent === 'GENERAL' || data.intent === 'EXPLANATION') {
        msg.reply = data.reply || ''
        msg.done = true
        break
      }
      // 继续管线: 推进到 schema 步骤
      steps.push({ label: 'Schema 检索', status: 'running' })
      break
    case 'schema':
      updateStep('Schema 检索', 'done', { detail: (data.tables || []).join(', '), duration: data.duration_ms })
      steps.push({ label: 'SQL 生成', status: 'running' })
      break
    case 'sql':
      if (data.error) {
        updateStep('SQL 生成', 'failed', { detail: data.error })
        msg.error = data.error
        break
      }
      msg.sql = data.sql
      // SQL 步骤: 可点击展开查看 (默认收起)
      updateStep('SQL 生成', 'done', { duration: data.duration_ms, type: 'sql', expandable: true, expanded: false })
      steps.push({ label: '执行查询', status: 'running' })
      break
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
            healData: {
              before: data.before_sql || '',
              after: data.sql || '',
              error: data.error || '',
            },
          })
        }
      } else {
        steps.push({ label: `自愈 #${data.retry}`, status: 'failed', detail: data.error })
      }
      break
    case 'chart':
      updateStep('图表生成', 'done', { duration: data.duration_ms })
      msg.chart = data.option
      // 图表独立在 pipeline 外渲染。双重 nextTick 确保 v-if 的 DOM + ref 回调就绪
      if (data.option) {
        nextTick(() => nextTick(() => renderChart(msgIdx)))
      }
      break
    case 'clarify':
      msg.askUser = { question: data.question, options: data.options || null }
      break
    case 'thinking':
      // 预思考 (REF-001): 在 schema 步骤后插入一个可展开的"预思考"步骤
      steps.push({
        label: '预思考',
        status: 'done',
        type: 'thinking',
        expandable: true,
        expanded: false,
        detail: (data.tables || []).join(',') || '',
        duration: data.duration_ms,
        thinkingData: {
          tables: data.tables || [],
          aggregation: data.aggregation || '',
          caveats: data.caveats || [],
          prevSqlReview: data.prev_sql_review || '',
        },
      })
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
        }
      }
      // 结果已收进 pipeline 折叠区的"执行查询"步骤内, pipeline 本身保持展开便于查看
      // (用户可点 pipeline 头手动折叠)
      msg.pipelineCollapsed = false
      if (data.conversation_id) conversationId.value = data.conversation_id
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
    msg.rows = data.rows.map((row: any[]) => {
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
      }
    }
    msg.selfHealRounds = data.self_heal_rounds || 0
    // 非流式降级: 同样构建 pipeline 步骤 (SQL/结果均收进折叠区, 默认展开结果)
    if (msg.sql) {
      msg.steps = [
        { label: 'SQL 生成', status: 'done' as const, type: 'sql', expandable: true, expanded: false },
      ]
      if (data.columns?.length || data.chart) {
        msg.steps.push({
          label: '执行查询', status: 'done' as const, detail: `${data.row_count} 行`,
          type: 'result', expandable: true, expanded: false,
        })
      }
    }
    msg.done = true
    msg.pipelineCollapsed = false
    await nextTick()
    if (msg.chart) nextTick(() => nextTick(() => renderChart(msgIdx)))
  } catch (e2: any) {
    msg.error = streamErr.message + ' | ' + (e2.response?.data?.detail || e2.message || '网络错误')
    msg.done = true
  }
}

function renderChart(idx: number, retries = 3) {
  const msg = messages.value[idx]
  if (!msg?.chart) return
  const el = chartRefs[idx]
  if (!el) {
    // ref 未就绪: DOM 可能还在更新中, 短延迟重试 (健壮性, 不只为特定场景)
    if (retries > 0) {
      setTimeout(() => renderChart(idx, retries - 1), 100)
    }
    return
  }
  const chart = echarts.init(el)
  chart.setOption(msg.chart)
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
  slashMenuVisible.value = val.trimStart().startsWith('/') && val.trim().length <= 8
}

// T048: 执行 slash command
function runSlashCommand(cmd: string) {
  slashMenuVisible.value = false
  input.value = ''
  switch (cmd) {
    case '/new':
    case '/clear':
      startNewConversation()
      ElMessage.success(cmd === '/new' ? '已新建对话' : '已清空对话')
      break
    case '/history':
      router.push('/history')
      break
    case '/help':
      ElMessage.info('可用命令: /new 新建 · /clear 清空 · /history 历史 · /help 帮助')
      break
  }
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

// 用户反馈 (FBK-001: 点赞/点踩, 静默提交不阻塞)
async function giveFeedback(msg: Message, type: 'like' | 'dislike') {
  if (msg.feedbackGiven === type) return  // 已给过同类反馈
  msg.feedbackGiven = type
  try {
    await feedbackApi.create({ feedback_type: type })
  } catch {
    msg.feedbackGiven = null  // 失败回退, 允许重试
    ElMessage.error('反馈提交失败')
  }
}

async function scrollToBottom() {
  await nextTick()
  if (chatBody.value) {
    chatBody.value.scrollTop = chatBody.value.scrollHeight
  }
}

onMounted(() => {
  fetchDataSources()
  fetchConversations()
  // 看板页"来源对话"跳转: 读取 conv query 自动加载该对话
  const convId = route.query.conv as string
  if (convId) {
    loadConversation(convId)
  }
})

// 数据源切换时刷新示例问题
watch(selectedDsId, () => { fetchSampleQuestions() })

// ── PERF-03: 异步查询 ────────────────────────────────────────
async function sendAsync(q: string) {
  messages.value.push({ role: 'user', text: q })
  input.value = ''
  try {
    const { data } = await asyncQuery.create({ question: q, data_source_id: selectedDsId.value })
    asyncTask.value = data
    // 轮询 (2s 间隔)
    _pollTimer = setInterval(pollAsyncTask, 2000)
  } catch (e: any) {
    if (e.response?.status === 409) {
      ElMessage.warning('已有进行中的相同查询, 请等待完成')
    } else {
      ElMessage.error('提交失败: ' + (e.response?.data?.detail || e.message))
    }
  }
}

async function pollAsyncTask() {
  if (!asyncTask.value) return
  try {
    const { data } = await asyncQuery.get(asyncTask.value.id)
    asyncTask.value = data
    if (data.status === 'done') {
      _stopPolling()
      // 结果渲染为消息
      if (data.result) {
        const rows = (data.result.rows || []).map((row: any[]) => {
          const obj: Record<string, any> = {}
          ;(data.result!.columns || []).forEach((col: string, ci: number) => { obj[col] = row[ci] })
          return obj
        })
        messages.value.push({
          role: 'assistant',
          reply: data.result.reply || undefined,
          sql: data.result.sql || undefined,
          columns: data.result.columns,
          rows,
          rowCount: data.result.row_count,
          chart: data.result.chart,
          steps: [
            { label: 'SQL 生成', status: 'done' as const, type: 'sql', expandable: true, expanded: false },
            { label: '执行查询', status: 'done' as const, detail: `${data.result.row_count} 行`, type: 'result', expandable: true, expanded: false },
          ],
          done: true,
          pipelineCollapsed: true,
        })
        await scrollToBottom()
        await nextTick()
        const idx = messages.value.length - 1
        if (messages.value[idx]?.chart) {
          nextTick(() => nextTick(() => renderChart(idx)))
        }
      }
      // 清除任务卡片 (延迟, 让用户看到完成态)
      setTimeout(() => { asyncTask.value = null }, 2000)
      fetchConversations()
    } else if (data.status === 'failed' || data.status === 'cancelled') {
      _stopPolling()
      messages.value.push({ role: 'assistant', error: data.error || '查询失败' })
      setTimeout(() => { asyncTask.value = null }, 2000)
    }
  } catch {
    _stopPolling()
  }
}

async function cancelAsyncTask() {
  if (!asyncTask.value) return
  try {
    await asyncQuery.cancel(asyncTask.value.id)
    ElMessage.info('已取消')
  } catch (e: any) {
    ElMessage.error('取消失败: ' + (e.response?.data?.detail || e.message))
  }
}

function _stopPolling() {
  if (_pollTimer) {
    clearInterval(_pollTimer)
    _pollTimer = null
  }
}

onUnmounted(_stopPolling)
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
.title { font-size: 1.1rem; font-weight: bold; flex: 1; }
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
.sql-box {
  background: #f5f7fa; border: 1px solid #e4e7ed; border-radius: 6px;
  padding: 10px 14px; margin-bottom: 12px; overflow-x: auto;
}
.sql-label { font-size: 0.75rem; color: #909399; margin-bottom: 4px; }
.sql-box pre { margin: 0; font-size: 0.85rem; }
.result-box { margin-bottom: 12px; }
.result-meta { font-size: 0.8rem; color: #909399; margin-bottom: 6px; }
.chart-box { margin-bottom: 12px; }
.feedback-row { display: flex; align-items: center; gap: 4px; margin-top: 4px; padding-top: 8px; border-top: 1px solid #f0f0f0; }
.feedback-label { font-size: 0.75rem; color: #909399; margin-right: 8px; }
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

/* ── Pipeline 步骤条 (对标 V1 Dify 风格: 始终展开, 竖排实时进度) ── */
.pipeline {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 10px;
  padding: 10px 12px;
  background: #f8f9fa;
  border-radius: 8px;
  border-left: 3px solid #667eea;
}
.pipeline-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.pipeline-title {
  font-size: 0.75rem;
  font-weight: 600;
  color: #64748b;
}
.pipeline-summary {
  flex: 1;
  text-align: center;
  font-size: 0.72rem;
  color: #909399;
}
.pipeline-step {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 4px 0;
  font-size: 0.82rem;
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
  display: flex;
  align-items: center;
  gap: 6px;
}
.step-label.clickable {
  cursor: pointer;
  user-select: none;
}
.step-label.clickable:hover { color: #667eea; }
.step-detail-inline {
  font-weight: 400;
  color: #909399;
  font-size: 0.72rem;
}
.expand-toggle {
  font-size: 12px;
  color: #c0c4cc;
  margin-left: 2px;
}
.step-detail {
  color: #909399;
  font-size: 0.72rem;
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
/* T049: token 用量汇总行 (pipeline 底部) */
.trace-summary {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #e4e7ed;
  font-size: 0.72rem;
  color: #909399;
  text-align: right;
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

/* PERF-03: 异步任务卡片 */
.async-task-card {
  padding: 10px 20px;
  background: #f0f5ff;
  border-bottom: 1px solid #d6e4ff;
}
.async-head { display: flex; align-items: center; gap: 8px; }
.async-q { font-size: 0.85rem; color: #303133; white-space: nowrap; }
</style>
