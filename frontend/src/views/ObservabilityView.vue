<template>
  <div class="obs-view">
    <div class="page-header">
      <el-button :icon="ArrowLeft" text @click="$router.push('/chat')">返回</el-button>
      <span class="title">系统状态</span>
    </div>

    <!-- 组件状态 -->
    <el-card v-loading="healthLoading">
      <template #header><b>组件状态</b></template>
      <div v-for="(val, key) in health.components" :key="key" class="comp-row">
        <span class="comp-name">{{ key }}</span>
        <el-tag :type="String(val).includes('ok') || String(val).includes('loaded') ? 'success' : 'warning'" size="small">{{ val }}</el-tag>
      </div>
    </el-card>

    <!-- 配置 -->
    <el-card style="margin-top: 16px">
      <template #header><b>配置</b></template>
      <div v-for="(val, key) in health.config" :key="key" class="comp-row">
        <span class="comp-name">{{ key }}</span>
        <code>{{ val }}</code>
      </div>
    </el-card>

    <!-- T050: dump-prompts 导出 + token 用量统计 -->
    <el-card style="margin-top: 16px">
      <template #header>
        <div class="card-header-row">
          <b>Prompt 调试 & Token 用量 (T050)</b>
          <div class="trace-actions">
            <el-select
              v-model="selectedConvId" placeholder="选择对话" size="small"
              style="width: 280px" filterable
            >
              <el-option
                v-for="c in conversations" :key="c.conversation_id"
                :label="`${c.title} (${c.turn_count} 轮)`" :value="c.conversation_id"
              />
            </el-select>
            <el-button
              type="primary" size="small" :icon="Download"
              :disabled="!selectedConvId" :loading="exporting"
              @click="exportTrace"
            >导出 dump-prompts</el-button>
          </div>
        </div>
      </template>

      <!-- token 用量概览 -->
      <div v-if="traceSummary" class="token-overview">
        <div class="token-stat">
          <div class="token-num">{{ traceSummary.total_turns }}</div>
          <div class="token-label">总轮次</div>
        </div>
        <div class="token-stat">
          <div class="token-num">{{ traceSummary.total_prompt_tokens }}</div>
          <div class="token-label">输入 tokens</div>
        </div>
        <div class="token-stat">
          <div class="token-num">{{ traceSummary.total_completion_tokens }}</div>
          <div class="token-label">输出 tokens</div>
        </div>
        <div class="token-stat">
          <div class="token-num">{{ traceSummary.total_tokens }}</div>
          <div class="token-label">合计 tokens</div>
        </div>
      </div>

      <el-empty v-else description="选择对话查看 token 用量明细" :image-size="60" />

      <!-- 各轮 prompt 节点明细 (可展开查看 prompt 文本) -->
      <div v-if="traceTurns.length" class="turn-list">
        <div v-for="t in traceTurns" :key="t.turn" class="turn-item">
          <div class="turn-head" @click="toggleTurn(t.turn)">
            <span class="turn-badge">第 {{ t.turn }} 轮</span>
            <span class="turn-q">{{ t.question || '(无问题)' }}</span>
            <span class="turn-token">{{ t.prompt_tokens + t.completion_tokens }} tokens · {{ (t.prompts || []).length }} 节点</span>
            <el-icon class="turn-toggle"><ArrowDown v-if="!openTurns[t.turn]" /><ArrowUp v-else /></el-icon>
          </div>
          <div v-if="openTurns[t.turn]" class="turn-body">
            <div v-for="(p, pi) in (t.prompts || [])" :key="pi" class="prompt-node">
              <div class="prompt-head" @click="togglePrompt(t.turn, pi)">
                <el-tag size="small" type="info">{{ p.node }}</el-tag>
                <span class="prompt-token">{{ p.prompt_tokens }} + {{ p.completion_tokens }} = {{ p.total_tokens }} tokens</span>
                <el-icon class="turn-toggle"><ArrowDown v-if="!openPrompts[`${t.turn}-${pi}`]" /><ArrowUp v-else /></el-icon>
              </div>
              <div v-if="openPrompts[`${t.turn}-${pi}`]" class="prompt-text">
                <div v-if="p.system" class="prompt-block">
                  <div class="prompt-label">system</div>
                  <pre>{{ p.system }}</pre>
                </div>
                <div class="prompt-block">
                  <div class="prompt-label">user</div>
                  <pre>{{ p.user }}</pre>
                </div>
              </div>
            </div>
            <div v-if="t.sql" class="prompt-block">
              <div class="prompt-label">执行的 SQL</div>
              <pre>{{ t.sql }}</pre>
            </div>
          </div>
        </div>
      </div>
    </el-card>

    <!-- DSO-05: 数据源状态监控 -->
    <el-card style="margin-top: 16px" v-loading="metricsLoading">
      <template #header><b>数据源监控 (最近 24h)</b></template>
      <el-table :data="metrics" size="small" border>
        <el-table-column prop="name" label="数据源" min-width="140" />
        <el-table-column prop="query_count" label="查询数" width="100" />
        <el-table-column label="平均耗时" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="row.avg_duration_ms > 10000 ? 'danger' : (row.avg_duration_ms > 3000 ? 'warning' : 'success')">
              {{ row.avg_duration_ms }}ms
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="错误率" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.error_rate > 5 ? 'danger' : 'success'">{{ row.error_rate }}%</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="慢查询" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.slow_count > 0" size="small" type="danger">🔥 {{ row.slow_count }}</el-tag>
            <span v-else style="color:#c0c4cc">0</span>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!metrics.length && !metricsLoading" description="最近 24h 无查询记录" :image-size="50" />
    </el-card>

    <!-- OPS-02: 备份恢复 -->
    <el-card style="margin-top: 16px">
      <template #header><b>备份恢复 (OPS-02)</b></template>
      <div class="backup-actions">
        <el-button type="primary" :icon="Download" :loading="backupLoading" @click="doBackup">
          备份元数据库
        </el-button>
        <el-upload
          :show-file-list="false"
          :before-upload="handleRestore"
          accept=".sql"
        >
          <el-button type="danger" plain :loading="restoreLoading">恢复 (上传 .sql)</el-button>
        </el-upload>
      </div>
      <el-alert type="warning" :closable="false" show-icon style="margin-top: 12px">
        备份的是 ChatBI 元数据库 (用户/数据源/语义层), 业务库不备份。恢复会覆盖现有数据, 谨慎操作。
      </el-alert>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, ArrowDown, ArrowUp, Download } from '@element-plus/icons-vue'
import { observability, backupApi, type ConversationItem } from '@/api'

const health = ref<any>({ components: {}, config: {} })
const healthLoading = ref(false)
const conversations = ref<ConversationItem[]>([])
const selectedConvId = ref('')
const exporting = ref(false)
const traceSummary = ref<any>(null)
const traceTurns = ref<any[]>([])
const openTurns = reactive<Record<number, boolean>>({})
const openPrompts = reactive<Record<string, boolean>>({})
// DSO-05: 数据源监控
const metrics = ref<any[]>([])
const metricsLoading = ref(false)
// OPS-02: 备份恢复
const backupLoading = ref(false)
const restoreLoading = ref(false)

onMounted(async () => {
  healthLoading.value = true
  try {
    const { data } = await observability.healthDetail()
    health.value = data
  } catch {
    // fail-closed: 加载失败明确提示 (原版本吞掉了错误)
    ElMessage.warning('系统状态加载失败, 可能权限不足或服务异常')
  } finally {
    healthLoading.value = false
  }
  // 加载对话列表供 dump-prompts 选择
  try {
    const { data } = await observability.conversations()
    conversations.value = data
  } catch {
    // 静默: 不阻塞页面
  }
  // DSO-05: 加载数据源监控指标
  await loadMetrics()
})

// DSO-05: 加载数据源监控
async function loadMetrics() {
  metricsLoading.value = true
  try {
    const { data } = await observability.datasourceMetrics()
    metrics.value = data
  } catch {
    metrics.value = []
  } finally {
    metricsLoading.value = false
  }
}

// OPS-02: 备份
async function doBackup() {
  backupLoading.value = true
  try {
    const res = await backupApi.create()
    const blob = new Blob([res.data], { type: 'application/sql' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `chatbi-backup-${new Date().toISOString().slice(0, 10)}.sql`
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('备份已下载')
  } catch (e: any) {
    let detail = e.message || '备份失败'
    if (e.response?.data instanceof Blob) {
      try { detail = JSON.parse(await e.response.data.text()).detail || detail } catch { /* */ }
    } else if (e.response?.data?.detail) {
      detail = e.response.data.detail
    }
    ElMessage.error('备份失败: ' + detail)
  } finally {
    backupLoading.value = false
  }
}

// OPS-02: 恢复 (before-upload 钩子, 返回 false 阻止自动上传)
async function handleRestore(file: File) {
  try {
    await ElMessageBox.confirm(
      `确认用「${file.name}」恢复? 这将覆盖现有元数据库数据, 操作不可逆!`,
      '恢复确认', { type: 'error', confirmButtonText: '确认覆盖', cancelButtonText: '取消' },
    )
  } catch {
    return false
  }
  restoreLoading.value = true
  try {
    const { data } = await backupApi.restore(file)
    ElMessage.success(`恢复成功 (${data.size_bytes} bytes)`)
    setTimeout(() => window.location.reload(), 1500)  // 恢复后刷新页面
  } catch (e: any) {
    let detail = e.message || '恢复失败'
    if (e.response?.data instanceof Blob) {
      try { detail = JSON.parse(await e.response.data.text()).detail || detail } catch { /* */ }
    } else if (e.response?.data?.detail) {
      detail = e.response.data.detail
    }
    ElMessage.error('恢复失败: ' + detail)
  } finally {
    restoreLoading.value = false
  }
  return false  // 阻止 el-upload 自动上传 (已手动处理)
}

// 导出某对话的完整 trace (prompt + token), 前端 Blob 下载 JSON
async function exportTrace() {
  if (!selectedConvId.value) return
  exporting.value = true
  try {
    const { data } = await observability.conversationTrace(selectedConvId.value)
    traceSummary.value = data.summary
    traceTurns.value = data.turns || []
    // Blob 下载 (对标 Claude Code dump-prompts)
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `prompts-${selectedConvId.value}.json`
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success(`已导出 ${data.summary.total_turns} 轮 prompt (${data.summary.total_tokens} tokens)`)
  } catch (e: any) {
    // fail-closed: 非 DEBUG 模式 prompt 未持久化 → 404/空, 明确提示
    const detail = e.response?.data?.detail || e.message || '导出失败'
    ElMessage.error(`dump-prompts 导出失败: ${detail} (需 DEBUG 模式才持久化 prompt 文本)`)
  } finally {
    exporting.value = false
  }
}

function toggleTurn(turn: number) {
  openTurns[turn] = !openTurns[turn]
}

function togglePrompt(turn: number, idx: number) {
  const key = `${turn}-${idx}`
  openPrompts[key] = !openPrompts[key]
}
</script>

<style scoped>
.obs-view { padding: 24px; max-width: 1100px; margin: 0 auto; }
.page-header { margin-bottom: 20px; }
.title { font-size: 1.3rem; font-weight: bold; margin-left: 8px; }
.comp-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #f0f0f0; }
.comp-name { font-weight: 500; }

.card-header-row { display: flex; justify-content: space-between; align-items: center; }
.trace-actions { display: flex; gap: 8px; align-items: center; }

/* token 概览 */
.token-overview { display: flex; gap: 16px; margin: 12px 0; }
.token-stat { flex: 1; text-align: center; background: #f5f7fa; border-radius: 8px; padding: 14px 8px; }
.token-num { font-size: 1.5rem; font-weight: 700; color: #667eea; }
.token-label { font-size: 0.75rem; color: #909399; margin-top: 4px; }

/* 各轮明细 */
.turn-list { margin-top: 12px; }
.turn-item { border: 1px solid #ebeef5; border-radius: 6px; margin-bottom: 8px; overflow: hidden; }
.turn-head {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; background: #fafbfc; cursor: pointer;
}
.turn-head:hover { background: #f5f7fa; }
.turn-badge {
  font-size: 0.72rem; font-weight: 600; color: #667eea;
  background: #f0f2ff; border-radius: 4px; padding: 1px 6px;
}
.turn-q { flex: 1; font-size: 0.85rem; color: #303133; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.turn-token { font-size: 0.72rem; color: #909399; }
.turn-toggle { color: #c0c4cc; font-size: 12px; }
.turn-body { padding: 10px 14px; }

/* prompt 节点 */
.prompt-node { border-left: 2px solid #e4e7ed; padding-left: 10px; margin-bottom: 8px; }
.prompt-head { display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 2px 0; }
.prompt-head:hover { color: #667eea; }
.prompt-token { font-size: 0.72rem; color: #909399; }
.prompt-text { margin-top: 6px; }
.prompt-block { margin-bottom: 6px; }
.prompt-label { font-size: 0.7rem; color: #c0c4cc; margin-bottom: 2px; }
.prompt-block pre {
  background: #f5f7fa; border-radius: 4px; padding: 8px 10px;
  font-size: 0.78rem; white-space: pre-wrap; word-break: break-all;
  max-height: 200px; overflow-y: auto; margin: 0;
}
/* OPS-02: 备份恢复 */
.backup-actions { display: flex; gap: 12px; align-items: center; }
</style>
