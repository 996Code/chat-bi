<template>
  <div class="chat-view">
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
        <el-empty description="输入问题开始查询，如「各类目商品数量」「消费最高的用户」">
        </el-empty>
      </div>

      <div v-for="(msg, idx) in messages" :key="idx" class="message" :class="msg.role">
        <div class="msg-content">
          <!-- 用户消息 -->
          <template v-if="msg.role === 'user'">
            <div class="user-q">{{ msg.text }}</div>
          </template>
          <!-- Agent 回复 -->
          <template v-else>
            <div v-if="msg.error" class="error-box">
              <el-alert :title="msg.error" type="error" :closable="false" show-icon />
            </div>
            <template v-else>
              <!-- SQL -->
              <div v-if="msg.sql" class="sql-box">
                <div class="sql-label">SQL</div>
                <pre><code>{{ msg.sql }}</code></pre>
              </div>
              <!-- 结果表格 -->
              <div v-if="msg.columns?.length" class="result-box">
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
              <!-- 图表 -->
              <div v-if="msg.chart" class="chart-box">
                <div :ref="(el: any) => setChartRef(el, idx)" style="width: 100%; height: 350px"></div>
              </div>
              <!-- ask_user 确认 (T047: 选项按钮 + 状态展示) -->
              <div v-if="msg.askUser" class="ask-user-box">
                <el-alert :title="msg.askUser.question" type="warning" :closable="false" show-icon />
                <div v-if="msg.askUser.options?.length" class="ask-options">
                  <el-button
                    v-for="opt in msg.askUser.options" :key="opt"
                    size="small" @click="input = opt; send()"
                  >{{ opt }}</el-button>
                </div>
                <el-input v-else v-model="clarifyInput" size="small" placeholder="输入澄清..." @keydown.enter="sendClarify()">
                  <template #append><el-button @click="sendClarify()">回复</el-button></template>
                </el-input>
              </div>
              <!-- Pipeline Trace (T049: 调用链折叠) -->
              <el-collapse v-if="msg.stage || msg.llmCalls" class="trace-collapse">
                <el-collapse-item title="执行详情">
                  <div class="trace-detail">
                    <div>阶段: <el-tag size="small">{{ msg.stage }}</el-tag></div>
                    <div>LLM 调用: {{ msg.llmCalls }} 次</div>
                    <div v-if="msg.healRounds">自愈: {{ msg.healRounds }} 轮</div>
                    <div v-if="msg.truncated">⚠ 结果已截断 (超过上限)</div>
                  </div>
                </el-collapse-item>
              </el-collapse>
            </template>
          </template>
        </div>
      </div>

      <!-- 加载中 -->
      <div v-if="loading" class="message assistant">
        <div class="msg-content">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span style="margin-left: 8px">思考中...</span>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="chat-input">
      <el-input
        v-model="input"
        type="textarea"
        :rows="2"
        placeholder="输入问题... (Enter 发送, Shift+Enter 换行, ↑↓ 切换历史)"
        @keydown.enter.exact.prevent="send"
        @keydown.arrow-up.prevent="navigateHistory(-1)"
        @keydown.arrow-down.prevent="navigateHistory(1)"
        :disabled="loading || !selectedDsId"
      />
      <el-button type="primary" :loading="loading" :disabled="!input.trim() || !selectedDsId" @click="send">
        发送
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { chat, datasource, type ChatResponse } from '@/api'
import * as echarts from 'echarts'

interface Message {
  role: 'user' | 'assistant'
  text?: string
  sql?: string
  columns?: string[]
  rows?: Record<string, any>[]
  rowCount?: number
  truncated?: boolean
  chart?: Record<string, any> | null
  error?: string
  askUser?: { question: string; options: string[] | null } | null
  stage?: string
  llmCalls?: number
  healRounds?: number
}

const input = ref('')
const messages = ref<Message[]>([])
const loading = ref(false)
const conversationId = ref<string | null>(null)
const dataSources = ref<{ id: string; name: string }[]>([])
const selectedDsId = ref<string>('')
const chatBody = ref<HTMLElement>()
const chartRefs: Record<number, HTMLElement> = {}

function setChartRef(el: any, idx: number) {
  if (el) chartRefs[idx] = el
}

async function fetchDataSources() {
  try {
    const { data } = await datasource.list()
    dataSources.value = data.map((d: any) => ({ id: d.id, name: d.name }))
    if (dataSources.value.length && !selectedDsId.value) {
      selectedDsId.value = dataSources.value[0].id
    }
  } catch {
    ElMessage.error('加载数据源失败')
  }
}

async function send() {
  const q = input.value.trim()
  if (!q || loading.value || !selectedDsId.value) return

  messages.value.push({ role: 'user', text: q })
  inputHistory.value.push(q) // T048: 存历史
  if (inputHistory.value.length > 50) inputHistory.value.shift() // 最多50条
  historyIdx.value = -1
  input.value = ''
  loading.value = true
  await scrollToBottom()

  try {
    const { data } = await chat.ask({
      question: q,
      data_source_id: selectedDsId.value,
      conversation_id: conversationId.value || undefined,
    })
    conversationId.value = data.conversation_id

    // 表格行数据转 prop 格式
    const rows = data.rows.map((row) => {
      const obj: Record<string, any> = {}
      data.columns.forEach((col, i) => { obj[col] = row[i] })
      return obj
    })

    messages.value.push({
      role: 'assistant',
      sql: data.sql || undefined,
      columns: data.columns,
      rows,
      rowCount: data.row_count,
      truncated: data.truncated,
      chart: data.chart,
      error: data.success ? undefined : (data.error || '查询失败'),
      askUser: data.ask_user,
      stage: data.stage,
      llmCalls: data.llm_calls,
      healRounds: data.self_heal_rounds,
    })
    await scrollToBottom()

    // 渲染图表 (nextTick 后 DOM 才更新)
    await nextTick()
    renderLastChart()
  } catch (e: any) {
    messages.value.push({
      role: 'assistant',
      error: e.response?.data?.detail || e.message || '网络错误',
    })
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

function renderLastChart() {
  const lastIdx = messages.value.length - 1
  const msg = messages.value[lastIdx]
  if (!msg?.chart) return
  const el = chartRefs[lastIdx]
  if (!el) return
  const chart = echarts.init(el)
  chart.setOption(msg.chart)
}

// T048: 输入历史导航 (上下箭头切换)
const inputHistory = ref<string[]>([])
const historyIdx = ref(-1)
const clarifyInput = ref('')

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

// T047: ask_user 澄清回复
function sendClarify() {
  if (!clarifyInput.value.trim()) return
  input.value = clarifyInput.value
  clarifyInput.value = ''
  send()
}

async function scrollToBottom() {
  await nextTick()
  if (chatBody.value) {
    chatBody.value.scrollTop = chatBody.value.scrollHeight
  }
}

onMounted(fetchDataSources)
</script>

<style scoped>
.chat-view { display: flex; flex-direction: column; height: 100vh; max-height: 100vh; }
.chat-header {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 20px; border-bottom: 1px solid #ebeef5; background: #fff;
}
.title { font-size: 1.1rem; font-weight: bold; flex: 1; }
.chat-body { flex: 1; overflow-y: auto; padding: 20px; }
.empty-hint { display: flex; align-items: center; justify-content: center; height: 100%; }
.message { margin-bottom: 20px; }
.message.user { text-align: right; }
.msg-content { display: inline-block; max-width: 85%; text-align: left; }
.user-q {
  background: #409eff; color: #fff; padding: 10px 16px;
  border-radius: 12px 12px 2px 12px; display: inline-block;
}
.message.assistant .msg-content { width: 100%; }
.sql-box {
  background: #f5f7fa; border: 1px solid #e4e7ed; border-radius: 6px;
  padding: 10px 14px; margin-bottom: 12px; overflow-x: auto;
}
.sql-label { font-size: 0.75rem; color: #909399; margin-bottom: 4px; }
.sql-box pre { margin: 0; font-size: 0.85rem; }
.result-box { margin-bottom: 12px; }
.result-meta { font-size: 0.8rem; color: #909399; margin-bottom: 6px; }
.chart-box { margin-bottom: 12px; }
.ask-user-box { margin-bottom: 12px; }
.ask-options { margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap; }
.error-box { margin-bottom: 8px; }
.meta-info { display: flex; gap: 6px; flex-wrap: wrap; }
.trace-collapse { margin-bottom: 8px; border: none; }
.trace-collapse :deep(.el-collapse-item__header) { font-size: 0.8rem; color: #909399; height: 32px; line-height: 32px; }
.trace-detail { font-size: 0.85rem; color: #606266; line-height: 1.8; }
.chat-input {
  display: flex; gap: 10px; padding: 16px 20px;
  border-top: 1px solid #ebeef5; background: #fff;
}
.chat-input .el-button { align-self: flex-end; }
</style>
