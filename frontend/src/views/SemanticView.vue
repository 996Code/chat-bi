<template>
  <div class="semantic-view">
    <div class="page-header">
      <div>
        <el-button :icon="ArrowLeft" text @click="$router.push('/datasources')">返回</el-button>
        <span class="title">语义层查看</span>
        <el-tag v-if="model" type="success" style="margin-left: 8px">
          v{{ model.version }}
        </el-tag>
      </div>
      <el-button-group v-if="model">
        <el-button :icon="Refresh" @click="fetchData">刷新</el-button>
        <el-button :icon="Clock" @click="openVersions">版本历史</el-button>
      </el-button-group>
    </div>

    <div v-if="!model && !loading" class="empty-hint">
      <el-empty description="该数据源还没有语义层，请先在数据源页点击「扫描」">
        <el-button type="primary" @click="$router.push('/datasources')">去数据源页</el-button>
      </el-empty>
    </div>

    <!-- 表列表 (左侧) + 详情/图谱 (右侧) -->
    <div v-loading="loading" v-if="model" class="content-layout">
      <el-card class="table-list">
        <template #header>
          <b>表 ({{ filteredModels.length }})</b>
        </template>
        <el-input v-model="search" placeholder="搜索表名/指标名..." clearable size="small" style="margin-bottom: 12px" />
        <div
          v-for="m in filteredModels"
          :key="m.name"
          class="table-item"
          :class="{ active: selected?.name === m.name }"
          @click="selected = m; schemaGraphRef?.focusNode?.(m.name)"
        >
          <div class="table-name">{{ m.display_name }}</div>
          <div class="table-meta">
            <span>{{ m.name }}</span>
              <span class="badges">
              <el-badge :value="m.columns.length" type="primary" />列
              <el-badge :value="getRelationshipCount(m.name)" type="success" />关系
              <el-badge v-if="m.metrics?.length" :value="m.metrics.length" type="warning" />指标
            </span>
          </div>
        </div>
      </el-card>

      <!-- 右侧: 详情 / 图谱 Tab 切换 -->
      <div class="right-panel">
        <el-tabs v-model="activeTab" class="view-tabs">
          <el-tab-pane label="详情" name="detail">
            <el-card v-if="selected" class="table-detail">
        <template #header>
          <div class="detail-header">
            <div>
              <!-- T015: 表中文名可编辑 -->
              <el-input
                v-if="editingTable"
                v-model="editForm.tableDisplayName"
                size="small" style="width: 240px"
                @keydown.enter="saveTableEdit"
              />
              <b v-else style="font-size: 1.1rem">{{ selected.display_name }}</b>
              <code style="margin-left: 8px; color: #999">{{ selected.name }}</code>
              <el-button
                v-if="!editingTable" text size="small" :icon="Edit"
                @click="startTableEdit"
              >编辑</el-button>
              <template v-else>
                <el-button text size="small" type="primary" @click="saveTableEdit">保存</el-button>
                <el-button text size="small" @click="editingTable = false">取消</el-button>
              </template>
            </div>
            <div>
              <el-tag size="small" :type="sourceTag(selected.source)">{{ sourceLabel(selected.source, selected.confidence) }}</el-tag>
            </div>
          </div>
        </template>

        <!-- T015: 表描述可编辑 -->
        <div class="desc-edit-row">
          <el-input
            v-if="editingTable"
            v-model="editForm.tableDescription"
            type="textarea" :rows="2" size="small"
            placeholder="表的业务描述..."
          />
          <p v-else-if="selected.description" class="desc">{{ selected.description }}</p>
        </div>

        <h4>列 ({{ selected.columns.length }})</h4>
        <el-table :data="selected.columns" size="small" border>
          <el-table-column prop="name" label="列名" min-width="130">
            <template #default="{ row }"><code>{{ row.name }}</code></template>
          </el-table-column>
          <!-- T015: 中文名可编辑 (双击) -->
          <el-table-column label="中文名" min-width="130">
            <template #default="{ row }">
              <el-input
                v-if="editingCol === row.name"
                v-model="editForm.colDisplayName"
                size="small"
                @keydown.enter.prevent="saveColEdit(row)"
                @blur="saveColEdit(row)"
              />
              <span v-else class="editable-cell" @dblclick="startColEdit(row, 'name')">
                {{ row.display_name }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="data_type" label="类型" width="140" />
          <!-- T015: 语义类型可编辑 (下拉) -->
          <el-table-column label="语义" width="130">
            <template #default="{ row }">
              <el-select
                v-if="editingCol === row.name"
                v-model="editForm.colSemanticType"
                size="small" style="width: 100px"
                @change="saveColEdit(row)"
              >
                <el-option label="度量 measure" value="measure" />
                <el-option label="维度 dimension" value="dimension" />
                <el-option label="主键 key" value="key" />
              </el-select>
              <el-tag
                v-else size="small" class="editable-cell"
                :type="semanticTag(row.semantic_type)"
                @dblclick="startColEdit(row, 'semantic')"
              >{{ row.semantic_type || '-' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="来源" width="120">
            <template #default="{ row }">
              <el-tag size="small" :type="sourceTag(row.source)">{{ sourceLabel(row.source, row.confidence) }}</el-tag>
            </template>
          </el-table-column>
        </el-table>

        <h4 v-if="selected.relationships.length" style="margin-top: 20px">
          关系 ({{ selected.relationships.length }})
        </h4>
        <el-table v-if="selected.relationships.length" :data="selected.relationships" size="small" border>
          <el-table-column prop="type" label="基数" width="80" />
          <el-table-column prop="target_model" label="目标表" min-width="120" />
          <el-table-column prop="join_type" label="JOIN" width="80" />
          <el-table-column prop="on" label="ON 条件" min-width="250">
            <template #default="{ row }"><code style="font-size: 0.85em">{{ row.on }}</code></template>
          </el-table-column>
        </el-table>

        <h4 v-if="reverseRelationships.length" style="margin-top: 20px">

        <!-- 指标区域 (和列、关系平级) -->
        <h4 style="margin-top: 20px">
          指标 ({{ selected.metrics?.length || 0 }})
          <el-button text size="small" type="primary" @click="addMetric" style="margin-left: 8px">+ 新增</el-button>
        </h4>
        <el-table v-if="selected.metrics?.length" :data="selected.metrics" size="small" border>
          <el-table-column prop="name" label="标识" min-width="100">
            <template #default="{ row }"><code>{{ row.name }}</code></template>
          </el-table-column>
          <el-table-column prop="display_name" label="中文名" min-width="100">
            <template #default="{ row }">
              <span>{{ row.display_name }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="formula" label="公式" min-width="180">
            <template #default="{ row }">
              <code style="font-size: 0.85em">{{ row.formula }}</code>
            </template>
          </el-table-column>
          <el-table-column prop="type" label="类型" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="row.type === 'composite' ? 'warning' : 'success'">{{ row.type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="条件" min-width="150">
            <template #default="{ row }">
              <code v-if="row.condition" style="font-size: 0.85em">{{ row.condition }}</code>
              <span v-else style="color: #c0c4cc">-</span>
            </template>
          </el-table-column>
          <el-table-column label="使用" width="70" align="center">
            <template #default="{ row }">{{ row.co_occurrence || 0 }}</template>
          </el-table-column>
          <el-table-column label="来源" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="metricSourceTag(row.source)">{{ metricSourceLabel(row.source) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button text size="small" type="primary" @click="editMetric(row)">编辑</el-button>
              <el-button text size="small" type="danger" @click="deleteMetric(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div v-else style="color: #909399; font-size: 0.85rem; padding: 8px 0">暂无指标定义 (扫描时 LLM 会自动推断业务指标)</div>

        <h4 v-if="reverseRelationships.length" style="margin-top: 20px">
          被引用 ({{ reverseRelationships.length }})
          <el-tooltip content="其他表通过外键或推断关系引用了此表" placement="top">
            <el-icon style="color: #909399; margin-left: 4px"><InfoFilled /></el-icon>
          </el-tooltip>
        </h4>
        <el-table v-if="reverseRelationships.length" :data="reverseRelationships" size="small" border>
          <el-table-column prop="source" label="来源表" min-width="120" />
          <el-table-column prop="joinType" label="JOIN" width="80" />
          <el-table-column prop="on" label="ON 条件" min-width="250">
            <template #default="{ row }"><code style="font-size: 0.85em">{{ row.on }}</code></template>
          </el-table-column>
          <el-table-column label="来源" width="120">
            <template #default="{ row }">
              <el-tag size="small" :type="sourceTag(row.relSource)">{{ sourceLabel(row.relSource, row.confidence) }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
            </el-card>
          </el-tab-pane>
          <el-tab-pane label="图谱" name="graph">
            <div class="graph-panel" v-show="activeTab === 'graph'">
              <SchemaGraph v-if="dataSourceId" :data-source-id="dataSourceId" ref="schemaGraphRef" @data-changed="refreshReverseRelCount" />
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>

    <!-- 版本历史抽屉 (T015) -->
    <el-drawer v-model="versionDrawer" title="版本历史" size="400px">
      <div v-loading="versionLoading">
        <div v-if="!versions.length && !versionLoading" class="empty-hint" style="text-align:center;color:#999;padding:40px">
          暂无历史版本
        </div>
        <div v-for="v in versions" :key="v.id" class="version-item">
          <div class="version-head">
            <el-tag :type="v.is_current ? 'success' : 'info'" size="small">
              v{{ v.version }}{{ v.is_current ? ' 当前' : '' }}
            </el-tag>
            <div class="version-actions">
              <el-button
                v-if="!v.is_current"
                size="small" plain
                :loading="diffLoading === v.version"
                @click="showDiff(v.version)"
              >
                对比当前
              </el-button>
              <el-button
                v-if="!v.is_current"
                size="small" type="warning" plain
                :loading="rollingBack === v.version"
                @click="doRollback(v.version)"
              >
                回滚到此版本
              </el-button>
            </div>
          </div>
          <div class="version-id"><code>{{ (v.id || '').slice(0, 8) }}</code></div>
        </div>
      </div>
    </el-drawer>

    <!-- 指标编辑对话框 -->
    <el-dialog v-model="metricDialogVisible" :title="metricEditMode === 'add' ? '新增指标' : '编辑指标'" width="500px">
      <el-form label-width="100px" size="small">
        <el-form-item label="标识 (name)">
          <el-input v-model="metricForm.name" placeholder="英文标识, 如 gmv" :disabled="metricEditMode === 'edit'" />
        </el-form-item>
        <el-form-item label="中文名">
          <el-input v-model="metricForm.display_name" placeholder="中文展示名, 如 成交总额" />
        </el-form-item>
        <el-form-item label="公式">
          <el-input v-model="metricForm.formula" placeholder="如 SUM(total_amount)" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="metricForm.type" style="width: 100%">
            <el-option label="single (单指标)" value="single" />
            <el-option label="composite (复合指标)" value="composite" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="metricForm.type === 'composite'" label="子指标名">
          <el-input v-model="metricForm.factor_metric_names" placeholder="逗号分隔, 如 gmv, order_count" />
        </el-form-item>
        <el-form-item label="过滤条件">
          <el-input v-model="metricForm.condition" placeholder="如 status IN ('paid','shipped'), 可选" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="metricForm.description" type="textarea" :rows="2" placeholder="指标的业务含义, 可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="metricDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="metricSaving" @click="saveMetric">保存</el-button>
      </template>
    </el-dialog>

    <!-- 版本对比抽屉 -->
    <el-drawer v-model="diffDrawer" title="版本对比" size="500px">
      <div v-loading="diffLoading !== null">
        <div v-if="diffResult">
          <div v-if="diffResult.added_tables?.length" class="diff-section">
            <div class="diff-title add">新增表 ({{ diffResult.added_tables.length }})</div>
            <el-tag v-for="t in diffResult.added_tables" :key="t" type="success" size="small" style="margin: 2px">{{ t }}</el-tag>
          </div>
          <div v-if="diffResult.removed_tables?.length" class="diff-section">
            <div class="diff-title remove">删除表 ({{ diffResult.removed_tables.length }})</div>
            <el-tag v-for="t in diffResult.removed_tables" :key="t" type="danger" size="small" style="margin: 2px">{{ t }}</el-tag>
          </div>
          <div v-if="diffResult.changed_tables?.length" class="diff-section">
            <div class="diff-title change">变更表 ({{ diffResult.changed_tables.length }})</div>
            <div v-for="t in diffResult.changed_tables" :key="t.table" class="diff-table">
              <strong>{{ t.table }}</strong>
              <span v-if="t.added_columns?.length" class="diff-col add">+ {{ t.added_columns.join(', ') }}</span>
              <span v-if="t.removed_columns?.length" class="diff-col remove">- {{ t.removed_columns.join(', ') }}</span>
            </div>
          </div>
          <div v-if="!diffResult.added_tables?.length && !diffResult.removed_tables?.length && !diffResult.changed_tables?.length" class="empty-hint" style="text-align:center;color:#999;padding:40px">
            两个版本无差异
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Refresh, Clock, Edit, InfoFilled } from '@element-plus/icons-vue'
import { semantic, datasource, graph, type SemanticModel, type SemanticTableModel, type SemanticColumn, type SemanticMetric, type ReverseRelationship } from '@/api'
import { extractErrorDetail } from '@/utils/error'
import SchemaGraph from '@/components/SchemaGraph.vue'

const route = useRoute()
const model = ref<SemanticModel | null>(null)
const selected = ref<SemanticTableModel | null>(null)
const reverseRelationships = ref<ReverseRelationship[]>([])
// 反向关系计数 map: { tableName → 被引用次数 } (用于表列表显示双向关系数)
const reverseRelCountMap = ref<Record<string, number>>({})

/** 获取表的双向关系总数 (出关系 + 被引用关系) */
function getRelationshipCount(tableName: string): number {
  const m = model.value?.content?.models?.find(mm => mm.name === tableName)
  const outbound = m?.relationships?.length || 0
  const inbound = reverseRelCountMap.value[tableName] || 0
  return outbound + inbound
}
const loading = ref(false)
const activeTab = ref('detail')
const dataSourceId = ref('')
const schemaGraphRef = ref<InstanceType<typeof SchemaGraph> | null>(null)
// 版本历史 + 回滚 (T015)
const versionDrawer = ref(false)
const versionLoading = ref(false)
const versions = ref<{ id: string; version: number; is_current: boolean }[]>([])
// 版本对比 (diff)
const diffDrawer = ref(false)
const diffLoading = ref<number | null>(null)
const diffResult = ref<any>(null)
const rollingBack = ref<number | null>(null)
const search = ref('')

// T015: 行内编辑
const editingTable = ref(false)
const editingCol = ref<string | null>(null)  // 正在编辑的列名
const editForm = ref({
  tableDisplayName: '',
  tableDescription: '',
  colDisplayName: '',
  colSemanticType: '' as string,
})
const saving = ref(false)

const filteredModels = computed(() => {
  if (!model.value) return []
  const q = search.value.toLowerCase()
  return model.value.content.models.filter(m =>
    m.name.toLowerCase().includes(q) ||
    m.display_name.toLowerCase().includes(q) ||
    (m.metrics || []).some(mt => mt.name.toLowerCase().includes(q) || mt.display_name.toLowerCase().includes(q))
  )
})

async function fetchData() {
  let dsId = route.query.data_source_id as string
  // 缺 data_source_id 时自动选第一个数据源 (从导航栏直接进入时)
  if (!dsId) {
    try {
      const { data } = await datasource.list()
      if (data.length) dsId = data[0].id
    } catch { /* ignore */ }
  }
  if (!dsId) return
  dataSourceId.value = dsId
  loading.value = true
  try {
    const { data } = await semantic.current(dsId)
    model.value = data
    // 从语义层计算每张表被引用的次数 (双向关系计数)
    const revMap: Record<string, number> = {}
    if (data?.content?.models) {
      for (const m of data.content.models) {
        for (const rel of m.relationships || []) {
          if (rel.target_model) {
            revMap[rel.target_model] = (revMap[rel.target_model] || 0) + 1
          }
        }
      }
    }
    reverseRelCountMap.value = revMap
    if (data && data.content.models.length > 0) {
      selected.value = data.content.models[0]
    }
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (extractErrorDetail(e)))
  } finally {
    loading.value = false
  }
}

async function openVersions() {
  if (!model.value) return
  versionDrawer.value = true
  versionLoading.value = true
  try {
    const { data } = await semantic.versions(model.value.id)
    versions.value = data
  } catch (e: any) {
    ElMessage.error('加载版本历史失败: ' + (extractErrorDetail(e)))
  } finally {
    versionLoading.value = false
  }
}

async function doRollback(toVersion: number) {
  if (!model.value) return
  try {
    await ElMessageBox.confirm(
      `确认回滚到 v${toVersion}? 将创建新版本 (历史版本不可变)。`,
      '回滚确认',
      { type: 'warning' },
    )
  } catch {
    return // 用户取消
  }
  rollingBack.value = toVersion
  try {
    await semantic.rollback(model.value.id, toVersion)
    ElMessage.success(`已回滚到 v${toVersion} (创建为新版本)`)
    versionDrawer.value = false
    await fetchData() // 刷新当前语义层
  } catch (e: any) {
    ElMessage.error('回滚失败: ' + (extractErrorDetail(e)))
  } finally {
    rollingBack.value = null
  }
}

async function showDiff(fromVersion: number) {
  if (!model.value) return
  // diff: fromVersion → 当前版本
  diffLoading.value = fromVersion
  diffDrawer.value = true
  diffResult.value = null
  try {
    const currentVersion = model.value.version
    const { data } = await semantic.diff(model.value.id, fromVersion, currentVersion)
    diffResult.value = data
  } catch (e: any) {
    ElMessage.error('对比失败: ' + (extractErrorDetail(e)))
    diffDrawer.value = false
  } finally {
    diffLoading.value = null
  }
}

// ── T015: 行内编辑 ──────────────────────────────────────────
// 编辑 = 写新版本 (append-only), source→manual/confidence→1.0
function startTableEdit() {
  if (!selected.value) return
  editForm.value.tableDisplayName = selected.value.display_name
  editForm.value.tableDescription = selected.value.description || ''
  editingTable.value = true
}

async function saveTableEdit() {
  if (!model.value || !selected.value) return
  // 检查是否有变化
  if (
    editForm.value.tableDisplayName === selected.value.display_name &&
    editForm.value.tableDescription === (selected.value.description || '')
  ) {
    editingTable.value = false
    return
  }
  saving.value = true
  try {
    await semantic.patch(model.value.id, {
      table_name: selected.value.name,
      display_name: editForm.value.tableDisplayName,
      description: editForm.value.tableDescription,
    })
    ElMessage.success('已更新 (创建为新版本)')
    editingTable.value = false
    await fetchData()  // 刷新到新版本
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (extractErrorDetail(e)))
  } finally {
    saving.value = false
  }
}

function startColEdit(row: SemanticColumn, field: 'name' | 'semantic') {
  editingCol.value = row.name
  editForm.value.colDisplayName = row.display_name
  editForm.value.colSemanticType = row.semantic_type || 'dimension'
}

async function saveColEdit(row: SemanticColumn) {
  const col = editingCol.value
  editingCol.value = null
  if (!model.value || !selected.value || !col) return
  // 检查变化
  if (
    editForm.value.colDisplayName === row.display_name &&
    editForm.value.colSemanticType === (row.semantic_type || '')
  ) {
    return
  }
  saving.value = true
  try {
    await semantic.patch(model.value.id, {
      table_name: selected.value.name,
      column_name: col,
      column_display_name: editForm.value.colDisplayName,
      column_semantic_type: editForm.value.colSemanticType || undefined,
    })
    ElMessage.success('已更新 (创建为新版本)')
    await fetchData()
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (extractErrorDetail(e)))
  } finally {
    saving.value = false
  }
}

function sourceLabel(source: string, confidence: number): string {
  const map: Record<string, string> = {
    manual: '📋 注释',
    foreign_key: '🔑 外键',
    auto_inferred: confidence >= 0.8 ? '🤖 LLM' : '⚠️ 退化',
    name_pattern: '🔤 命名',
    ai_inferred: '🤖 LLM',
  }
  return map[source] || source
}

function sourceTag(source: string): any {
  const map: Record<string, string> = {
    manual: 'success',
    foreign_key: 'warning',
    auto_inferred: 'info',
    name_pattern: 'info',
    ai_inferred: 'info',
  }
  return map[source] || ''
}

function semanticTag(type: string | null): any {
  const map: Record<string, string> = {
    measure: 'danger',
    dimension: 'primary',
    key: 'warning',
  }
  return type ? map[type] || '' : 'info'
}

// ── 指标编辑 (Metric CRUD) ─────────────────────────────────
const metricDialogVisible = ref(false)
const metricEditMode = ref<'add' | 'edit'>('add')
const metricForm = ref({
  name: '',
  display_name: '',
  formula: '',
  type: 'single' as 'single' | 'composite',
  condition: '',
  description: '',
  factor_metric_names: '',
})
const metricSaving = ref(false)

function metricSourceLabel(source: string): string {
  const map: Record<string, string> = {
    manual: '📋 人工',
    auto_inferred: '🤖 推断',
    metric_suggestion: '💡 建议',
  }
  return map[source] || source
}

function metricSourceTag(source: string): any {
  const map: Record<string, string> = {
    manual: 'success',
    auto_inferred: 'info',
    metric_suggestion: 'warning',
  }
  return map[source] || ''
}

function addMetric() {
  metricEditMode.value = 'add'
  metricForm.value = {
    name: '', display_name: '', formula: '',
    type: 'single', condition: '', description: '', factor_metric_names: '',
  }
  metricDialogVisible.value = true
}

function editMetric(row: SemanticMetric) {
  metricEditMode.value = 'edit'
  metricForm.value = {
    name: row.name,
    display_name: row.display_name,
    formula: row.formula,
    type: row.type,
    condition: row.condition || '',
    description: row.description || '',
    factor_metric_names: (row.factor_metric_names || []).join(', '),
  }
  metricDialogVisible.value = true
}

async function saveMetric() {
  if (!model.value || !selected.value) return
  if (!metricForm.value.name || !metricForm.value.display_name || !metricForm.value.formula) {
    ElMessage.warning('请填写标识、中文名和公式')
    return
  }
  if (metricForm.value.type === 'composite' && !metricForm.value.factor_metric_names.trim()) {
    ElMessage.warning('composite 指标需填写子指标名 (factor_metric_names)')
    return
  }
  metricSaving.value = true
  try {
    const factors = metricForm.value.factor_metric_names
      .split(',').map(s => s.trim()).filter(Boolean)
    await semantic.patchMetric(model.value.id, {
      table_name: selected.value.name,
      metric_name: metricForm.value.name,
      metric_display_name: metricForm.value.display_name,
      metric_formula: metricForm.value.formula,
      metric_type: metricForm.value.type,
      metric_condition: metricForm.value.condition || undefined,
      metric_description: metricForm.value.description || undefined,
      metric_factor_metric_names: factors.length ? factors : undefined,
    })
    ElMessage.success('已保存 (创建为新版本)')
    metricDialogVisible.value = false
    await fetchData()
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (extractErrorDetail(e)))
  } finally {
    metricSaving.value = false
  }
}

async function deleteMetric(row: SemanticMetric) {
  if (!model.value || !selected.value) return
  try {
    await ElMessageBox.confirm(
      `确认删除指标「${row.display_name} (${row.name})」?`,
      '删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await semantic.patchMetric(model.value.id, {
      table_name: selected.value.name,
      metric_name: row.name,
      delete_metric: true,
    })
    ElMessage.success('已删除 (创建为新版本)')
    await fetchData()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (extractErrorDetail(e)))
  }
}

// 选中表变化时加载反向关系 (被引用)
watch(selected, async (val) => {
  reverseRelationships.value = []
  if (!val || !dataSourceId.value) return
  try {
    const { data } = await graph.reverseRelationships(dataSourceId.value, val.name)
    reverseRelationships.value = data.relationships
  } catch {
    // 静默失败, 不影响主功能
  }
})

/** 图谱关系增删后刷新反向关系计数 (重新拉取语义层) */
async function refreshReverseRelCount() {
  if (!dataSourceId.value) return
  try {
    const { data } = await semantic.current(dataSourceId.value)
    if (data?.content?.models) {
      model.value = data
      const revMap: Record<string, number> = {}
      for (const m of data.content.models) {
        for (const rel of m.relationships || []) {
          if (rel.target_model) {
            revMap[rel.target_model] = (revMap[rel.target_model] || 0) + 1
          }
        }
      }
      reverseRelCountMap.value = revMap
      // 重新定位 selected 到新数据 (model.value 已替换, selected 还指向旧对象)
      if (selected.value) {
        const refreshed = data.content.models.find(m => m.name === selected.value!.name)
        if (refreshed) selected.value = refreshed
      }
    }
  } catch {
    // 静默失败
  }
}

watch(() => route.query.data_source_id, fetchData)
onMounted(fetchData)
</script>

<style scoped>
.semantic-view { padding: 24px; }
.page-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 20px;
}
.title { font-size: 1.3rem; font-weight: bold; margin-left: 8px; }
.content-layout { display: flex; gap: 16px; height: calc(100vh - 140px); }
.table-list { width: 280px; flex-shrink: 0; display: flex; flex-direction: column; }
.table-list :deep(.el-card__body) { flex: 1; overflow-y: auto; }
.right-panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.right-panel :deep(.el-tabs) { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.right-panel :deep(.el-tabs__content) { flex: 1; overflow: hidden; }
.right-panel :deep(.el-tab-pane) { height: 100%; display: flex; flex-direction: column; }
.table-detail { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.table-detail :deep(.el-card__body) { flex: 1; overflow-y: auto; }
.graph-panel { height: calc(100vh - 200px); min-height: 400px; }
.table-item {
  padding: 10px 12px; border-radius: 6px; cursor: pointer; margin-bottom: 4px;
  border: 1px solid transparent;
}
.table-item:hover { background: #f5f7fa; }
.table-item.active { background: #ecf5ff; border-color: #b3d8ff; }
.table-name { font-weight: 600; }
.table-meta {
  font-size: 0.8rem; color: #999; margin-top: 4px;
  display: flex; justify-content: space-between;
}
.badges span { margin-left: 8px; }
.desc { color: #666; margin: 0 0 16px; }
.desc-edit-row { margin-bottom: 12px; }
h4 { margin: 16px 0 8px; color: #303030; }
/* T015: 可双击编辑的单元格 */
.editable-cell {
  cursor: pointer;
  border-bottom: 1px dashed #dcdfe6;
  padding-bottom: 1px;
}
.editable-cell:hover { border-bottom-color: #409eff; color: #409eff; }
.version-item {
  padding: 12px 0; border-bottom: 1px solid #ebeef5;
}
.version-head {
  display: flex; justify-content: space-between; align-items: center;
}
.version-id { font-size: 0.8rem; color: #999; margin-top: 6px; }
.version-actions { display: flex; gap: 6px; }
.diff-section { margin-bottom: 16px; }
.diff-title { font-weight: 600; margin-bottom: 6px; font-size: 0.85rem; }
.diff-title.add { color: #67c23a; }
.diff-title.remove { color: #f56c6c; }
.diff-title.change { color: #e6a23c; }
.diff-table { padding: 6px 0; border-bottom: 1px solid #f0f0f0; font-size: 0.82rem; }
.diff-col { display: block; margin-top: 2px; margin-left: 8px; }
.diff-col.add { color: #67c23a; }
.diff-col.remove { color: #f56c6c; }
</style>
