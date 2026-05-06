<template>
  <div class="page-container">
    <div class="header">
      <h2>评估测试</h2>
      <div style="display: flex; gap: 12px; align-items: center;">
        <el-button @click="router.push('/')">返回对话</el-button>
      </div>
    </div>

    <el-alert v-if="!isAdmin" title="仅管理员可运行评估测试" type="warning" :closable="false" style="margin-bottom: 16px" />

    <!-- Dataset editor -->
    <el-card style="margin-bottom: 24px">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span>评估问题列表</span>
          <div style="display: flex; gap: 8px;">
            <el-button size="small" @click="importJson">
              <el-icon><Upload /></el-icon> 导入 JSON
            </el-button>
            <el-button size="small" @click="exportJson">
              <el-icon><Download /></el-icon> 导出 JSON
            </el-button>
            <el-button size="small" type="primary" @click="addCase">
              <el-icon><Plus /></el-icon> 新增
            </el-button>
          </div>
        </div>
      </template>

      <el-form :model="form" label-width="80px" style="margin-bottom: 16px">
        <el-form-item label="数据源">
          <el-select v-model="form.datasource_id" placeholder="选择数据源" style="width: 100%">
            <el-option v-for="ds in datasourceStore.datasources" :key="ds.id" :label="ds.name" :value="ds.id" />
          </el-select>
        </el-form-item>
      </el-form>

      <el-table :data="dataset" border size="small">
        <el-table-column label="#" width="50" align="center">
          <template #default="{ $index }">{{ $index + 1 }}</template>
        </el-table-column>
        <el-table-column label="问题" min-width="400">
          <template #default="{ row }">
            <el-input v-model="row.question" placeholder="例如：各城市订单数量" size="small" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="center">
          <template #default="{ $index }">
            <el-button size="small" type="danger" link @click="removeCase($index)">
              <el-icon><Delete /></el-icon>
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="dataset-summary">
        共 {{ dataset.length }} 个测试问题
      </div>

      <el-button
        type="primary"
        size="large"
        :loading="running"
        :disabled="!isAdmin || !form.datasource_id || dataset.length === 0 || dataset.every(d => !d.question.trim())"
        @click="runEval"
        style="margin-top: 16px"
      >
        运行评估（{{ validCount }} 个问题）
      </el-button>
    </el-card>

    <!-- Results -->
    <el-card v-if="result">
      <template #header>
        <span>评估结果</span>
        <el-tag v-if="result.total" size="small" style="margin-left: 12px">
          {{ result.total }} 个问题
        </el-tag>
        <el-tag v-if="result.cache_hits" type="warning" size="small" style="margin-left: 8px">
          缓存命中 {{ result.cache_hits }} 次
        </el-tag>
      </template>

      <el-row :gutter="16" style="margin-bottom: 20px">
        <el-col :span="8">
          <div class="metric-card">
            <div class="metric-label">SQL 正确率</div>
            <div class="metric-value" :class="accuracyClass(result.sql_accuracy)">
              {{ (result.sql_accuracy * 100).toFixed(1) }}%
            </div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="metric-card">
            <div class="metric-label">平均耗时</div>
            <div class="metric-value">{{ result.avg_time_ms ?? '—' }}ms</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="metric-card">
            <div class="metric-label">缓存命中</div>
            <div class="metric-value warning">{{ result.cache_hits ?? 0 }}</div>
          </div>
        </el-col>
      </el-row>

      <el-table :data="result.results || []" border size="small">
        <el-table-column label="#" width="50" align="center">
          <template #default="{ $index }">{{ $index + 1 }}</template>
        </el-table-column>
        <el-table-column label="问题" min-width="150">
          <template #default="{ row }">{{ row.question }}</template>
        </el-table-column>
        <el-table-column label="来源" width="70" align="center">
          <template #default="{ row }">
            <el-tag :type="row.source === 'eval' ? 'warning' : 'info'" size="small">
              {{ row.source === 'eval' ? '评估' : '正常' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="缓存" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.cached" :type="row.cache_type === 'semantic' ? 'warning' : 'success'" size="small">
              {{ row.cache_type === 'semantic' ? '语义' : '精确' }}
            </el-tag>
            <span v-else class="text-muted">未命中</span>
          </template>
        </el-table-column>
        <el-table-column label="SQL" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.sql_match ? 'success' : 'danger'" size="small">
              {{ row.sql_match ? '✓' : '✗' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="80" align="right">
          <template #default="{ row }">
            <span v-if="row.time_ms">{{ row.time_ms }}ms</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="执行链路" width="90" align="center">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="showTrace(row)" :disabled="!row.steps || row.steps.length === 0">
              查看
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="生成 SQL" min-width="200">
          <template #default="{ row }">
            <code class="sql-text">{{ row.generated_sql || '—' }}</code>
          </template>
        </el-table-column>
        <el-table-column label="错误" min-width="150">
          <template #default="{ row }">
            <span v-if="row.error" class="error-text">{{ row.error }}</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- Trace dialog -->
    <PipelineTraceDialog v-model="showTraceDialog" :steps="traceSteps" title="评估执行链路" />

    <!-- Import dialog -->
    <el-dialog v-model="showImportDialog" title="导入 JSON" width="600px">
      <el-input
        v-model="importText"
        type="textarea"
        :rows="10"
        placeholder='["各城市订单数量", "上月销售额"]'
      />
      <template #footer>
        <el-button @click="showImportDialog = false">取消</el-button>
        <el-button type="primary" @click="confirmImport">导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Delete, Upload, Download } from '@element-plus/icons-vue'
import { useDatasourceStore } from '@/stores/datasourceStore'
import api from '@/api'
import PipelineTraceDialog from '@/components/PipelineTraceDialog.vue'

const router = useRouter()
const datasourceStore = useDatasourceStore()

const running = ref(false)
const result = ref<any>(null)
const form = ref({
  datasource_id: '',
})
const dataset = ref<Array<{ question: string }>>([])
const showImportDialog = ref(false)
const importText = ref('')

const showTraceDialog = ref(false)
const traceSteps = ref<any[]>([])

const validCount = computed(() => dataset.value.filter(d => d.question.trim()).length)

const isAdmin = computed(() => {
  try {
    const token = localStorage.getItem('access_token')
    if (!token) return false
    const payload = JSON.parse(atob(token.split('.')[1]))
    return payload.role === 'admin'
  } catch {
    return false
  }
})

function addCase() {
  dataset.value.push({ question: '' })
}

function removeCase(idx: number) {
  dataset.value.splice(idx, 1)
}

function importJson() {
  importText.value = ''
  showImportDialog.value = true
}

function confirmImport() {
  try {
    const parsed = JSON.parse(importText.value)
    if (!Array.isArray(parsed)) {
      ElMessage.warning('格式应为 JSON 数组')
      return
    }
    dataset.value = parsed.map((item: any) => ({
      question: typeof item === 'string' ? item : (item.question || ''),
    })).filter(d => d.question.trim())
    showImportDialog.value = false
    ElMessage.success(`已导入 ${dataset.value.length} 条问题`)
  } catch {
    ElMessage.error('JSON 格式错误')
  }
}

function exportJson() {
  const json = JSON.stringify(dataset.value.map(d => d.question), null, 2)
  const blob = new Blob([json], { type: 'application/json' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = 'evaluation-questions.json'
  link.click()
  URL.revokeObjectURL(link.href)
  ElMessage.success('已导出')
}

async function runEval() {
  const questions = dataset.value.filter(d => d.question.trim())
  if (questions.length === 0) {
    ElMessage.warning('请至少填写一个问题')
    return
  }

  running.value = true
  result.value = null
  try {
    const res = await api.post('/evaluation/run', {
      datasource_id: form.value.datasource_id,
      dataset: questions.map(d => ({ question: d.question })),
    })
    result.value = res.data
    ElMessage.success('评估完成')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail?.message || '评估失败')
  } finally {
    running.value = false
  }
}

function showTrace(row: any) {
  traceSteps.value = row.steps || []
  showTraceDialog.value = true
}

function accuracyClass(val: number) {
  if (val >= 0.8) return 'success'
  if (val >= 0.5) return 'warning'
  return 'danger'
}

onMounted(async () => {
  await datasourceStore.list()
  if (datasourceStore.datasources.length > 0) {
    form.value.datasource_id = datasourceStore.datasources[0].id
  }
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
.dataset-summary {
  margin-top: 8px;
  font-size: 12px;
  color: #94a3b8;
}
.metric-card {
  background: #f8f9fa;
  border-radius: 8px;
  padding: 16px;
  text-align: center;
}
.metric-label {
  font-size: 12px;
  color: #94a3b8;
  margin-bottom: 4px;
}
.metric-value {
  font-size: 28px;
  font-weight: 700;
}
.metric-value.success { color: #67c23a; }
.metric-value.warning { color: #e6a23c; }
.metric-value.danger { color: #f56c6c; }
.sql-text {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 11px;
  color: #64748b;
  word-break: break-all;
}
.error-text {
  color: #f56c6c;
  font-size: 12px;
}
.text-muted {
  color: #c0c4cc;
  font-size: 12px;
}
</style>
