<template>
  <div class="memory-view">
    <div class="page-header">
      <span class="title">Agent 记忆</span>
      <div>
        <el-select v-model="selectedDsId" placeholder="选择数据源" size="small" style="width: 200px; margin-right: 8px" @change="fetchData">
          <el-option v-for="ds in dataSources" :key="ds.id" :label="ds.name" :value="ds.id" />
        </el-select>
        <el-button type="primary" :icon="Plus" :disabled="!selectedDsId || consolidating" @click="openEditor()">新建记忆</el-button>
        <el-button
          :icon="Sort"
          :loading="consolidating"
          :disabled="!selectedDsId || selectableMems.length === 0 || consolidating"
          @click="doConsolidate"
        >
          <template v-if="consolidating">
            整理中 {{ consolidateProgress }}% — {{ consolidateStage }}
          </template>
          <template v-else>
            整理记忆<span v-if="selectedIds.size > 0" class="sel-count">({{ selectedIds.size }})</span>
          </template>
        </el-button>
        <el-button :icon="Refresh" :disabled="consolidating" @click="fetchData">刷新</el-button>
      </div>
    </div>

    <el-alert v-if="!selectedDsId" type="warning" :closable="false" show-icon style="margin-bottom: 16px">
      <template #title>请先选择数据源，记忆按数据源隔离管理</template>
    </el-alert>

    <el-alert v-else type="info" :closable="false" show-icon style="margin-bottom: 16px">
      <template #title>
        <span>
          记忆是 Agent <b>跨会话保留</b> 的事实和偏好。查询时 Agent 会自动检索与问题<b>关键词相关</b>的记忆注入上下文，
          无需手动触发。描述写得越准确，召回越精准。对话成功后 Agent 会自动提炼值得保留的新知识。
        </span>
      </template>
    </el-alert>

    <div v-loading="loading">
      <!-- 工具栏: 全选 + 显示已整理开关 -->
      <div v-if="selectedDsId && memoryList.length" class="list-toolbar">
        <el-checkbox
          :model-value="allSelected"
          :indeterminate="someSelected && !allSelected"
          @change="toggleSelectAll"
        >全选</el-checkbox>
        <span class="sel-info" v-if="selectedIds.size > 0">已选 {{ selectedIds.size }} 条</span>
        <el-switch v-model="showConsolidated" active-text="显示已整理" @change="fetchData" style="margin-left: auto" />
      </div>

      <!-- 空白引导面板 -->
      <div v-if="!memoryList.length && !loading && selectedDsId" class="onboarding">
        <div class="onboarding-title">🧠 还没有记忆，从常见场景快速创建：</div>
        <div class="onboarding-desc">
          记忆帮助 Agent 记住业务约定。比如"GMV 不含退款"这类规则，写一条记忆后每次相关查询都会自动召回。
        </div>
        <div class="example-cards">
          <div class="example-card" @click="createFromExample(examples[0])">
            <div class="example-icon">💡</div>
            <div class="example-name">业务约定</div>
            <div class="example-desc">记录 GMV 口径、计算规则等业务约定，查询时自动召回避免算错</div>
          </div>
          <div class="example-card" @click="createFromExample(examples[1])">
            <div class="example-icon">🏷️</div>
            <div class="example-name">字段偏好</div>
            <div class="example-desc">记录"查销量用 quantity 而不是 amount"这类字段使用偏好</div>
          </div>
          <div class="example-card" @click="createFromExample(examples[2])">
            <div class="example-icon">📊</div>
            <div class="example-name">常用维度</div>
            <div class="example-desc">记录常用的分组维度和时间范围，让 Agent 自动按习惯聚合</div>
          </div>
        </div>
      </div>

      <!-- 记忆列表 -->
      <el-card v-for="mem in memoryList" :key="mem.id" class="mem-card" :class="{ 'consolidated-card': mem.consolidated }">
        <template #header>
          <div class="card-header">
            <div class="card-header-left">
              <!-- 已整理的不可勾选 -->
              <el-checkbox
                v-if="!mem.consolidated"
                :model-value="selectedIds.has(mem.id)"
                @change="(v: boolean) => toggleSelect(mem.id, v)"
              />
              <el-icon v-else class="lock-icon"><Lock /></el-icon>
              <b>{{ mem.description || mem.name }}</b>
              <el-tag size="small" :type="typeTag(mem.type)" style="margin-left: 8px">{{ typeLabel(mem.type) }}</el-tag>
              <el-tag size="small" type="success" style="margin-left: 4px">自动召回</el-tag>
              <el-tag v-if="mem.consolidated" size="small" type="info" style="margin-left: 4px">已整理</el-tag>
            </div>
            <div v-if="!mem.consolidated || mem.type === 'consolidated'">
              <el-button size="small" :icon="Edit" @click="openEditor(mem)">编辑</el-button>
              <el-button size="small" type="danger" plain :icon="Delete" @click="doDelete(mem)">删除</el-button>
            </div>
          </div>
        </template>
        <div class="mem-content markdown-body" v-html="renderMarkdown(mem.content)"></div>
        <!-- linkage 结构化信息 -->
        <div v-if="mem.type === 'linkage' && (mem.tables || mem.co_occurrence)" class="linkage-meta">
          <el-tag v-if="mem.co_occurrence" size="small" type="info">共现 {{ mem.co_occurrence }} 次</el-tag>
          <el-tag v-if="mem.aggregation" size="small">{{ mem.aggregation }}</el-tag>
          <span v-if="mem.join_paths?.length" class="linkage-detail">
            <el-icon size="12"><Link /></el-icon>
            {{ mem.join_paths.length }} 条 JOIN 路径
          </span>
          <span v-if="mem.scenes?.length" class="linkage-detail">
            {{ mem.scenes.length }} 个场景
          </span>
        </div>
        <div class="recall-hint">
          <el-icon size="12" color="#909399"><InfoFilled /></el-icon>
          <span>描述「{{ mem.description || mem.name }}」用于关键词匹配召回，越具体召回越精准</span>
        </div>
      </el-card>
    </div>

    <!-- 编辑器抽屉 -->
    <el-drawer v-model="editorVisible" :title="editing.id ? `编辑: ${editing.name}` : '新建记忆'" size="600px">
      <el-form label-position="top">
        <el-form-item label="名称 (简短标题，可修改)">
          <el-input v-model="editing.name" placeholder="订单业务约定" />
        </el-form-item>
        <el-form-item>
          <template #label>
            <span>描述 <el-tag size="small" type="warning">重要</el-tag> — 用于关键词匹配召回，越具体越精准</span>
          </template>
          <el-input v-model="editing.description" placeholder="订单业务约定：GMV计算、状态定义、时间口径" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="editing.type" style="width: 100%">
            <el-option label="项目 (project) — 业务约定和规则" value="project" />
            <el-option label="用户 (user) — 个人偏好和习惯" value="user" />
            <el-option label="反馈 (feedback) — 经验教训" value="feedback" />
            <el-option label="参考 (reference) — 外部知识" value="reference" />
            <el-option label="整理成果 (consolidated) — AI 整理产出" value="consolidated" />
            <el-option label="链路经验 (linkage) — 表关联经验 (系统自动生成)" value="linkage" />
          </el-select>
        </el-form-item>
        <el-form-item label="内容 (Markdown)">
          <el-input v-model="editing.content" type="textarea" :rows="16"
            placeholder="记录业务事实、约定、偏好...&#10;&#10;例如:&#10;- GMV = SUM(total_amount) WHERE status IN ('paid', 'shipped')&#10;- 退款不算入 GMV&#10;- 所有时间口径按自然月统计" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSave">保存</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Edit, Delete, InfoFilled, Sort, Lock, Link } from '@element-plus/icons-vue'
import { memory as memoryApi, datasource, type Memory, type ConsolidateStatus } from '@/api'
import { extractErrorDetail } from '@/utils/error'
import { marked } from 'marked'

// 配置 marked
marked.setOptions({
  breaks: true,
  gfm: true,
})

// 渲染 Markdown 内容
function renderMarkdown(content: string): string {
  if (!content) return ''
  return marked.parse(content) as string
}

const memoryList = ref<Memory[]>([])
const loading = ref(false)
const editorVisible = ref(false)
const saving = ref(false)
const consolidating = ref(false)
const consolidateProgress = ref(0)
const consolidateStage = ref('')
const editing = ref<Partial<Memory>>({
  name: '', description: '', type: 'project', content: '',
})
const dataSources = ref<{ id: string; name: string }[]>([])
const selectedDsId = ref<string>('')
const showConsolidated = ref(false)
// 勾选的记忆 id 集合 (只允许勾选未整理的)
const selectedIds = ref<Set<string>>(new Set())

// 轮询定时器
let _pollTimer: ReturnType<typeof setInterval> | null = null
let _pollFailCount = 0

// 可勾选的记忆 = 未整理的
const selectableMems = computed(() => memoryList.value.filter(m => !m.consolidated))
const someSelected = computed(() => selectedIds.value.size > 0 && selectedIds.value.size < selectableMems.value.length)
const allSelected = computed(() => selectableMems.value.length > 0 && selectedIds.value.size === selectableMems.value.length)

function toggleSelect(id: string, checked: boolean) {
  if (checked) selectedIds.value.add(id)
  else selectedIds.value.delete(id)
  // 触发响应式
  selectedIds.value = new Set(selectedIds.value)
}

function toggleSelectAll(checked: boolean) {
  if (checked) {
    selectedIds.value = new Set(selectableMems.value.map(m => m.id))
  } else {
    selectedIds.value = new Set()
  }
}

// ── 整理轮询 ──────────────────────────────────────────────

function startPolling() {
  stopPolling()
  _pollFailCount = 0
  _pollTimer = setInterval(pollConsolidateStatus, 2000)
}

function stopPolling() {
  if (_pollTimer) {
    clearInterval(_pollTimer)
    _pollTimer = null
  }
}

async function pollConsolidateStatus() {
  if (!selectedDsId.value) return
  try {
    const { data } = await memoryApi.consolidateStatus(selectedDsId.value)
    consolidateProgress.value = data.progress
    consolidateStage.value = data.stage

    if (data.status === 'done') {
      stopPolling()
      consolidating.value = false
      const detail = data.result?.detail || '整理完成'

      // E1 Wave 4: 图谱同步冲突弹框
      if (data.result?.graph_sync_conflict) {
        showGraphSyncConflictDialog(data.result)
      } else if (data.result?.graph_sync) {
        const gs = data.result.graph_sync
        const syncInfo = gs.new_version
          ? `图谱已更新 (v${gs.new_version}, ${gs.boosted_pairs} 个表对增强, ${gs.new_pairs} 个新发现)`
          : ''
        ElMessage.success(detail + (syncInfo ? `\n${syncInfo}` : ''))
      } else {
        ElMessage.success(detail)
      }

      selectedIds.value = new Set()
      await fetchData()
    } else if (data.status === 'failed') {
      stopPolling()
      consolidating.value = false
      ElMessage.error('整理失败: ' + (data.error || '未知错误'))
      await fetchData()
    }
    _pollFailCount = 0
  } catch {
    _pollFailCount++
    if (_pollFailCount >= 10) {
      stopPolling()
      consolidating.value = false
      ElMessage.error('网络异常，轮询已停止')
    }
  }
}

/** 图谱同步冲突弹框: 三个选项 (重试/放弃图谱更新/取消) */
async function showGraphSyncConflictDialog(result: NonNullable<ConsolidateStatus['result']>) {
  const detail = result.detail || '整理完成'
  try {
    await ElMessageBox.confirm(
      `${detail}\n\n图谱同步时版本冲突 (其他操作同时修改了语义层)。\n请选择如何处理：`,
      '图谱同步冲突',
      {
        confirmButtonText: '重试同步',
        cancelButtonText: '放弃图谱更新',
        distinguishCancelAndClose: true,
        type: 'warning',
      },
    )
    // 用户点"重试同步"
    await doRetryGraphSync()
  } catch (action: any) {
    if (action === 'cancel') {
      // 用户点"放弃图谱更新" — 记忆整理结果保留, 仅图谱未更新
      ElMessage.info('已放弃图谱同步, 记忆整理结果保留')
    } else {
      // 用户点关闭按钮 — 同放弃
      ElMessage.info('已跳过图谱同步')
    }
  }
}

/** 调用重试图谱同步端点 */
async function doRetryGraphSync() {
  if (!selectedDsId.value) return
  consolidating.value = true
  consolidateProgress.value = 50
  consolidateStage.value = '重试图谱同步...'
  try {
    await memoryApi.retryGraphSync(selectedDsId.value)
    // 重试也是异步, 启动轮询
    startPolling()
  } catch (e: any) {
    consolidating.value = false
    if (e.response?.status === 409) {
      // 仍在运行, 开始轮询
      consolidating.value = true
      startPolling()
    } else {
      ElMessage.error('图谱同步重试失败: ' + (extractErrorDetail(e)))
    }
  }
}

/** 页面加载时检查是否有进行中的整理任务 (用户刷新页面后恢复进度) */
async function checkRunningConsolidate() {
  if (!selectedDsId.value) return
  try {
    const { data } = await memoryApi.consolidateStatus(selectedDsId.value)
    if (data.status === 'running') {
      consolidating.value = true
      consolidateProgress.value = data.progress
      consolidateStage.value = data.stage
      startPolling()
    }
  } catch { /* 静默忽略 */ }
}

// ── 示例模板 ──────────────────────────────────────────────

const examples = [
  {
    name: '订单业务约定',
    description: '订单业务约定：GMV计算与状态定义',
    type: 'project',
    content: `## GMV 计算规则

GMV = SUM(total_amount) WHERE status IN ('paid', 'shipped')
退款不算入 GMV

## 时间口径

所有时间统计按自然月，不按30天滚动
时间字段统一使用 created_at，不用 update_time`,
  },
  {
    name: '字段偏好',
    description: '字段使用偏好：销量用quantity',
    type: 'user',
    content: `## 字段偏好

- 查"销量"时用 quantity 字段，不用 amount (amount 是金额)
- 查"用户数"时用 COUNT(DISTINCT user_id)，不用 COUNT(*) (可能含重复)
- 查"占比"时结果用百分比格式 (乘 100 + %)`,
  },
  {
    name: '常用维度',
    description: '常用维度与时间范围',
    type: 'reference',
    content: `## 常用分组维度

- 按品类分组: GROUP BY category_name
- 按城市分组: GROUP BY city
- 按月份分组: DATE_TRUNC('month', created_at)

## 默认时间范围

未指定时间时默认查最近一个月的数据`,
  },
]

function createFromExample(ex: typeof examples[0]) {
  editing.value = {
    name: ex.name,
    description: ex.description,
    type: ex.type,
    content: ex.content,
  }
  editorVisible.value = true
}

// ── 数据加载 ──────────────────────────────────────────────

async function fetchDataSources() {
  try {
    const { data } = await datasource.list()
    dataSources.value = data
    if (data.length && !selectedDsId.value) {
      selectedDsId.value = data[0].id
      await fetchData()
      // 检查是否有进行中的整理任务
      await checkRunningConsolidate()
    }
  } catch (e: any) {
    console.error('数据源列表加载失败:', e)
  }
}

async function fetchData() {
  if (!selectedDsId.value) return
  loading.value = true
  selectedIds.value = new Set()  // 刷新时清空勾选
  try {
    const { data } = await memoryApi.list(selectedDsId.value, showConsolidated.value)
    memoryList.value = data
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (extractErrorDetail(e)))
  } finally {
    loading.value = false
  }
}

function openEditor(mem?: Memory) {
  if (mem) {
    editing.value = { ...mem }
  } else {
    editing.value = { name: '', description: '', type: 'project', content: '' }
  }
  editorVisible.value = true
}

async function doSave() {
  if (!selectedDsId.value) return
  const ed = editing.value
  if (!ed.name?.trim() || !ed.content?.trim()) {
    ElMessage.warning('名称和内容不能为空')
    return
  }
  saving.value = true
  try {
    await memoryApi.save({
      id: ed.id,  // 有 id = 更新, 无 id = 新建
      name: ed.name, description: ed.description || '',
      type: ed.type || 'project', content: ed.content,
    }, selectedDsId.value)
    ElMessage.success('保存成功')
    editorVisible.value = false
    await fetchData()
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (extractErrorDetail(e)))
  } finally {
    saving.value = false
  }
}

async function doDelete(mem: Memory) {
  if (!selectedDsId.value) return
  try {
    await ElMessageBox.confirm(`确认删除记忆「${mem.description || mem.name}」?`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await memoryApi.delete(mem.id, selectedDsId.value)
    ElMessage.success('已删除')
    await fetchData()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (extractErrorDetail(e)))
  }
}

async function doConsolidate() {
  if (!selectedDsId.value) return
  const selCount = selectedIds.value.size
  const ids = selCount > 0 ? Array.from(selectedIds.value) : undefined
  const hint = ids ? `将整理选中的 ${selCount} 条记忆` : '将整理全部记忆'
  try {
    await ElMessageBox.confirm(`${hint}，合并后原始记忆标记为已整理（默认隐藏）`, '整理记忆', { type: 'info' })
  } catch { return }
  consolidating.value = true
  consolidateProgress.value = 0
  consolidateStage.value = '提交中...'
  try {
    await memoryApi.consolidate(selectedDsId.value, ids)
    // POST 返回 202, 启动轮询
    startPolling()
  } catch (e: any) {
    consolidating.value = false
    if (e.response?.status === 409) {
      // 已在运行, 直接开始轮询
      consolidating.value = true
      startPolling()
    } else {
      ElMessage.error('整理失败: ' + (extractErrorDetail(e)))
    }
  }
}

function typeLabel(t: string): string {
  const map: Record<string, string> = { project: '项目', user: '用户', feedback: '反馈', reference: '参考', consolidated: '整理成果', linkage: '链路经验' }
  return map[t] || t
}
function typeTag(t: string): any {
  const map: Record<string, any> = { project: '', user: 'success', feedback: 'warning', reference: 'info', consolidated: 'danger', linkage: 'success' }
  return map[t] || ''
}

onMounted(fetchDataSources)
onUnmounted(stopPolling)
</script>

<style scoped>
.memory-view { padding: 24px; }
.page-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 20px;
}
.title { font-size: 1.3rem; font-weight: bold; }
.sel-count {
  margin-left: 2px;
  font-size: 0.8em;
}
.list-toolbar {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 12px; padding: 8px 12px;
  background: #f5f7fa; border-radius: 6px;
}
.sel-info { font-size: 0.8rem; color: #909399; }
.mem-card { margin-bottom: 16px; }
.card-header {
  display: flex; justify-content: space-between; align-items: center;
}
.card-header-left {
  display: flex; align-items: center; gap: 8px;
}
.lock-icon { color: #c0c4cc; }
.mem-content {
  margin: 0; white-space: pre-wrap; font-size: 0.85rem;
  line-height: 1.6; color: #606266; max-height: 300px; overflow-y: auto;
}
.mem-content.markdown-body {
  white-space: normal;
}
.mem-content.markdown-body :deep(h1),
.mem-content.markdown-body :deep(h2),
.mem-content.markdown-body :deep(h3),
.mem-content.markdown-body :deep(h4),
.mem-content.markdown-body :deep(h5),
.mem-content.markdown-body :deep(h6) {
  margin: 8px 0 4px 0;
  font-weight: 600;
  color: #303133;
}
.mem-content.markdown-body :deep(h1) { font-size: 1.2em; }
.mem-content.markdown-body :deep(h2) { font-size: 1.1em; }
.mem-content.markdown-body :deep(h3) { font-size: 1.05em; }
.mem-content.markdown-body :deep(p) {
  margin: 4px 0;
}
.mem-content.markdown-body :deep(ul),
.mem-content.markdown-body :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
.mem-content.markdown-body :deep(li) {
  margin: 2px 0;
}
.mem-content.markdown-body :deep(code) {
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 0.9em;
  color: #e83e8c;
}
.mem-content.markdown-body :deep(pre) {
  background: #f5f7fa;
  padding: 8px 12px;
  border-radius: 4px;
  overflow-x: auto;
  margin: 8px 0;
}
.mem-content.markdown-body :deep(pre code) {
  background: transparent;
  padding: 0;
  color: inherit;
}
.mem-content.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
  width: 100%;
}
.mem-content.markdown-body :deep(th),
.mem-content.markdown-body :deep(td) {
  border: 1px solid #e4e7ed;
  padding: 6px 10px;
  text-align: left;
}
.mem-content.markdown-body :deep(th) {
  background: #f5f7fa;
  font-weight: 600;
}
.mem-content.markdown-body :deep(strong) {
  color: #303133;
  font-weight: 600;
}
.mem-content.markdown-body :deep(a) {
  color: #409eff;
  text-decoration: none;
}
.mem-content.markdown-body :deep(a:hover) {
  text-decoration: underline;
}
.recall-hint {
  display: flex; align-items: center; gap: 4px;
  margin-top: 8px; font-size: 0.72rem; color: #909399;
  border-top: 1px dashed #ebeef5; padding-top: 6px;
}
.linkage-meta {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-top: 8px; padding-top: 6px;
  border-top: 1px dashed #ebeef5;
}
.linkage-detail {
  display: inline-flex; align-items: center; gap: 2px;
  font-size: 0.75rem; color: #909399;
}
.consolidated-card {
  opacity: 0.65;
  border-style: dashed;
}

/* 空白引导面板 */
.onboarding {
  text-align: center; padding: 40px 20px;
}
.onboarding-title {
  font-size: 1.1rem; font-weight: 600; color: #303133; margin-bottom: 8px;
}
.onboarding-desc {
  font-size: 0.88rem; color: #909399; margin-bottom: 24px; max-width: 520px;
  margin-left: auto; margin-right: auto; line-height: 1.6;
}
.example-cards {
  display: flex; gap: 16px; justify-content: center; flex-wrap: wrap;
  max-width: 720px; margin: 0 auto;
}
.example-card {
  flex: 1; min-width: 200px; max-width: 240px;
  border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 20px 16px; cursor: pointer;
  transition: all 0.2s; text-align: center;
}
.example-card:hover {
  border-color: #67c23a; background: #f0f9eb;
  box-shadow: 0 2px 12px rgba(103,194,58,0.1);
}
.example-icon { font-size: 1.6rem; margin-bottom: 8px; }
.example-name {
  font-size: 0.88rem; font-weight: 600; color: #303133; margin-bottom: 6px;
}
.example-desc {
  font-size: 0.76rem; color: #909399; line-height: 1.5;
}
</style>
