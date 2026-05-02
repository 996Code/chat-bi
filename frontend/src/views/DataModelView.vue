<template>
  <div class="data-model-view">
    <!-- Header -->
    <div class="view-header">
      <div class="header-left">
        <el-button text @click="router.push('/')">
          <el-icon><ArrowLeft /></el-icon>
        </el-button>
        <h2>数据模型</h2>
      </div>
      <div class="header-right">
        <el-select v-model="selectedDsId" placeholder="选择数据源" style="width: 200px" @change="onDatasourceChange">
          <el-option v-for="ds in datasourceStore.datasources" :key="ds.id" :label="ds.name" :value="ds.id" />
        </el-select>
        <el-dropdown trigger="click" @command="handleSync">
          <el-button :disabled="!selectedDsId">
            <el-icon><Refresh /></el-icon> 同步表结构
            <el-icon><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="incremental">增量同步（推荐）</el-dropdown-item>
              <el-dropdown-item command="full">全量覆盖</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button type="primary" :disabled="!selectedDsId" @click="handleSave">
          <el-icon><Check /></el-icon> 保存
        </el-button>
      </div>
    </div>

    <div v-if="!selectedDsId" class="empty-select">
      <el-empty description="请先选择一个数据源" />
    </div>

    <div v-else class="editor-layout">
      <!-- Left: table list -->
      <div class="sidebar">
        <div class="sidebar-header">
          <span>数据表 ({{ activeTables.length }})</span>
          <el-button size="small" text @click="addCustomTable">
            <el-icon><Plus /></el-icon>
          </el-button>
        </div>
        <div class="table-list">
          <div
            v-for="(table, idx) in activeTables"
            :key="table.name"
            :class="['table-item', { active: selectedTableIndex === idx }]"
            @click="selectTable(idx)"
          >
            <el-icon class="table-icon"><Grid /></el-icon>
            <div class="table-info">
              <div class="table-name">{{ table.alias || table.name || '未命名表' }}</div>
              <div class="table-meta">{{ table.columns?.length || 0 }} 字段</div>
            </div>
            <el-button
              v-if="!table._autoScanned"
              size="small"
              text
              class="table-delete"
              @click.stop="removeTable(idx)"
            >
              <el-icon><Close /></el-icon>
            </el-button>
          </div>
          <div v-if="activeTables.length === 0" class="empty-hint">
            点击上方 + 添加自定义表
          </div>
        </div>

        <!-- Quick navigation to relationships and metrics -->
        <div class="sidebar-footer">
          <div
            :class="['nav-item', { active: activeTab === 'relationships' }]"
            @click="activeTab = 'relationships'"
          >
            <el-icon><Link /></el-icon> 关联关系 ({{ configRelationships.length }})
          </div>
          <div
            :class="['nav-item', { active: activeTab === 'metrics' }]"
            @click="activeTab = 'metrics'"
          >
            <el-icon><Histogram /></el-icon> 指标 ({{ configMetrics.length }})
          </div>
        </div>
      </div>

      <!-- Right: editor area -->
      <div class="editor-panel">
        <!-- Table editor -->
        <template v-if="activeTab === 'table' && selectedTable">
          <div class="panel-header">
            <h3>{{ selectedTable.alias || selectedTable.name }}</h3>
            <el-tag v-if="selectedTable._autoScanned" size="small" type="info">自动同步</el-tag>
            <el-tag v-else size="small" type="success">自定义</el-tag>
          </div>
          <div class="panel-body">
            <el-form label-width="70px" label-position="right" size="default">
              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="表名">
                    <el-input v-model="selectedTable.name" placeholder="如 users" :disabled="selectedTable._autoScanned" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="别名">
                    <el-input v-model="selectedTable.alias" placeholder="如 用户表" />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-form-item label="描述">
                <el-input v-model="selectedTable.description" type="textarea" :rows="2" placeholder="表用途说明" />
              </el-form-item>
            </el-form>

            <el-divider content-position="left">
              字段 ({{ selectedTable.columns?.length || 0 }})
            </el-divider>

            <el-table :data="selectedTable.columns || []" border size="small" style="width: 100%">
              <el-table-column label="字段名" min-width="120">
                <template #default="{ row }">
                  <el-icon v-if="row.primary" class="pk-icon"><Key /></el-icon>
                  <span :class="{ 'pk-col': row.primary }">{{ row.name }}</span>
                </template>
              </el-table-column>
              <el-table-column label="类型" width="120">
                <template #default="{ row }">
                  <code class="col-type">{{ row.type }}</code>
                </template>
              </el-table-column>
              <el-table-column label="可空" width="70" align="center">
                <template #default="{ row }">
                  <el-tag v-if="row.nullable" size="small" type="info">NULL</el-tag>
                  <el-tag v-else size="small" type="success">NOT NULL</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="别名" min-width="100">
                <template #default="{ row }">
                  <el-input v-model="row.alias" size="small" placeholder="别名" />
                </template>
              </el-table-column>
              <el-table-column label="描述" min-width="120">
                <template #default="{ row }">
                  <el-input v-model="row.comment" size="small" placeholder="字段说明" />
                </template>
              </el-table-column>
            </el-table>
          </div>
        </template>

        <!-- Relationships editor -->
        <template v-else-if="activeTab === 'relationships'">
          <div class="panel-header">
            <h3>关联关系</h3>
            <el-button size="small" type="primary" @click="addRelationship">
              <el-icon><Plus /></el-icon> 添加关联
            </el-button>
          </div>
          <div class="panel-body">
            <el-table :data="configRelationships" border size="small" style="width: 100%">
              <el-table-column label="从表" min-width="140">
                <template #default="{ row }">
                  <el-select v-model="row.from_table" size="small" placeholder="选择表">
                    <el-option v-for="t in activeTables" :key="t.name" :label="t.alias || t.name" :value="t.name" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="从字段" min-width="120">
                <template #default="{ row }">
                  <el-select v-model="row.from_column" size="small" placeholder="字段">
                    <el-option
                      v-for="c in getColumnsForTable(row.from_table)"
                      :key="c.name"
                      :label="c.name"
                      :value="c.name"
                    />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="关联到表" min-width="140">
                <template #default="{ row }">
                  <el-select v-model="row.to_table" size="small" placeholder="选择表">
                    <el-option v-for="t in activeTables" :key="t.name" :label="t.alias || t.name" :value="t.name" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="关联字段" min-width="120">
                <template #default="{ row }">
                  <el-select v-model="row.to_column" size="small" placeholder="字段">
                    <el-option
                      v-for="c in getColumnsForTable(row.to_table)"
                      :key="c.name"
                      :label="c.name"
                      :value="c.name"
                    />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="" width="60" align="center" fixed="right">
                <template #default="{ $index }">
                  <el-button size="small" text type="danger" @click="removeRelationship($index)">
                    <el-icon><Delete /></el-icon>
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="configRelationships.length === 0" description="暂无关联关系" :image-size="60" />
          </div>
        </template>

        <!-- Metrics editor -->
        <template v-else-if="activeTab === 'metrics'">
          <div class="panel-header">
            <h3>预定义指标</h3>
            <el-button size="small" type="primary" @click="addMetric">
              <el-icon><Plus /></el-icon> 添加指标
            </el-button>
          </div>
          <div class="panel-body">
            <el-table :data="configMetrics" border size="small" style="width: 100%">
              <el-table-column label="指标名称" min-width="140">
                <template #default="{ row }">
                  <el-input v-model="row.name" size="small" placeholder="如 总用户数" />
                </template>
              </el-table-column>
              <el-table-column label="SQL 表达式" min-width="200">
                <template #default="{ row }">
                  <el-input v-model="row.expression" size="small" placeholder="如 COUNT(DISTINCT id)" />
                </template>
              </el-table-column>
              <el-table-column label="描述" min-width="140">
                <template #default="{ row }">
                  <el-input v-model="row.description" size="small" placeholder="说明" />
                </template>
              </el-table-column>
              <el-table-column label="" width="60" align="center" fixed="right">
                <template #default="{ $index }">
                  <el-button size="small" text type="danger" @click="removeMetric($index)">
                    <el-icon><Delete /></el-icon>
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="configMetrics.length === 0" description="暂无预定义指标" :image-size="60" />
          </div>
        </template>
      </div>
    </div>

    <!-- Sync result dialog -->
    <el-dialog v-model="showSyncResult" title="同步结果" width="480px">
      <div v-if="syncResult" class="sync-result">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="同步模式">{{ syncResult.mode }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ syncResult.status }}</el-descriptions-item>
          <el-descriptions-item v-if="syncResult.added?.length" label="新增表">
            <el-tag v-for="t in syncResult.added" :key="t" size="small" type="success" style="margin: 2px">{{ t }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item v-if="syncResult.changed?.length" label="变更表">
            <el-tag v-for="t in syncResult.changed" :key="t" size="small" type="warning" style="margin: 2px">{{ t }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item v-if="syncResult.removed?.length" label="已删除表">
            <el-tag v-for="t in syncResult.removed" :key="t" size="small" type="danger" style="margin: 2px">{{ t }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="总表数">{{ syncResult.total_tables || activeTables.length }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, ArrowDown, Plus, Delete, Check, Refresh, Histogram, Key, Grid, Link, Close } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useDatasourceStore } from '@/stores/datasourceStore'
import api from '@/api'

const router = useRouter()
const datasourceStore = useDatasourceStore()

const selectedDsId = ref('')
const activeTab = ref<'table' | 'relationships' | 'metrics'>('table')
const selectedTableIndex = ref(0)

const configTables = ref<any[]>([])
const configRelationships = ref<any[]>([])
const configMetrics = ref<any[]>([])

const showSyncResult = ref(false)
const syncResult = ref<any>(null)

const activeTables = computed(() => configTables.value.filter(t => !t._deleted))
const selectedTable = computed(() => activeTables.value[selectedTableIndex.value] || null)

function getColumnsForTable(tableName: string): any[] {
  const table = configTables.value.find(t => t.name === tableName)
  return table?.columns || []
}

function selectTable(idx: number) {
  selectedTableIndex.value = idx
  activeTab.value = 'table'
}

function addCustomTable() {
  configTables.value.push({ name: '', alias: '', description: '', columns: [], _autoScanned: false })
  selectedTableIndex.value = activeTables.value.length - 1
  activeTab.value = 'table'
}

function removeTable(idx: number) {
  const table = activeTables.value[idx]
  if (table._autoScanned) {
    table._deleted = true
  } else {
    configTables.value.splice(configTables.value.indexOf(table), 1)
  }
  if (selectedTableIndex.value >= activeTables.value.length) {
    selectedTableIndex.value = Math.max(0, activeTables.value.length - 1)
  }
}

function addRelationship() {
  configRelationships.value.push({ from_table: '', to_table: '', from_column: '', to_column: '' })
  activeTab.value = 'relationships'
}

function removeRelationship(idx: number) {
  configRelationships.value.splice(idx, 1)
}

function addMetric() {
  configMetrics.value.push({ name: '', expression: '', description: '' })
  activeTab.value = 'metrics'
}

function removeMetric(idx: number) {
  configMetrics.value.splice(idx, 1)
}

async function handleSync(mode: string) {
  if (!selectedDsId.value) return
  try {
    const res = await api.post(`/data-models/${selectedDsId.value}/sync`, { mode })
    syncResult.value = res.data
    showSyncResult.value = true

    if (res.data.status !== 'up_to_date') {
      await onDatasourceChange()
    }
  } catch (e: any) {
    ElMessage.error(e.response?.data?.message || '同步失败')
  }
}

async function onDatasourceChange() {
  if (!selectedDsId.value) return
  configTables.value = []
  configRelationships.value = []
  configMetrics.value = []
  selectedTableIndex.value = 0
  activeTab.value = 'table'

  try {
    const res = await api.get(`/data-models/${selectedDsId.value}`)
    const cfg = res.data.config
    configTables.value = cfg.models || []
    configRelationships.value = cfg.relationships || []
    configMetrics.value = cfg.metrics || []
  } catch {
    ElMessage.info('该数据源尚未配置数据模型，请先同步表结构')
  }
}

async function handleSave() {
  if (!selectedDsId.value) return
  const hasContent = configTables.value.some((t) => !t._deleted && t.name) || configMetrics.value.length > 0
  if (!hasContent) {
    ElMessage.warning('至少添加一张表或指标')
    return
  }
  try {
    const config = {
      models: configTables.value,
      relationships: configRelationships.value,
      metrics: configMetrics.value,
    }
    try {
      await api.put(`/data-models/${selectedDsId.value}`, { config })
    } catch (e: any) {
      if (e.response?.status === 404) {
        await api.post('/data-models', { datasource_id: selectedDsId.value, config })
      } else {
        throw e
      }
    }
    ElMessage.success('模型已保存')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.message || '保存失败')
  }
}

onMounted(async () => {
  await datasourceStore.list()
})
</script>

<style scoped>
.data-model-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #f8fafc;
}

/* Header */
.view-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  background: white;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
  z-index: 5;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-left h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
}

.header-right {
  display: flex;
  gap: 8px;
  align-items: center;
}

.empty-select {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Layout: sidebar + editor */
.editor-layout {
  flex: 1;
  display: flex;
  overflow: hidden;
}

/* Sidebar */
.sidebar {
  width: 260px;
  min-width: 260px;
  border-right: 1px solid #e2e8f0;
  background: white;
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 14px;
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  border-bottom: 1px solid #e2e8f0;
}

.table-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px 0;
}

.table-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  cursor: pointer;
  transition: background 0.15s;
}

.table-item:hover {
  background: #f1f5f9;
}

.table-item.active {
  background: #eef2ff;
  border-right: 3px solid #6366f1;
}

.table-icon {
  color: #94a3b8;
  font-size: 16px;
  flex-shrink: 0;
}

.table-item.active .table-icon {
  color: #6366f1;
}

.table-info {
  flex: 1;
  min-width: 0;
}

.table-name {
  font-size: 13px;
  color: #1e293b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.table-meta {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
}

.table-delete {
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s;
  color: #94a3b8;
}

.table-item:hover .table-delete {
  opacity: 1;
}

.table-delete:hover {
  color: #ef4444;
}

.empty-hint {
  padding: 24px 14px;
  text-align: center;
  color: #94a3b8;
  font-size: 12px;
}

.sidebar-footer {
  border-top: 1px solid #e2e8f0;
  padding: 6px 0;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  font-size: 12px;
  color: #64748b;
  cursor: pointer;
  transition: background 0.15s;
}

.nav-item:hover {
  background: #f1f5f9;
}

.nav-item.active {
  background: #eef2ff;
  color: #6366f1;
  font-weight: 500;
}

/* Editor panel */
.editor-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 20px;
  border-bottom: 1px solid #e2e8f0;
  background: white;
}

.panel-header h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #1e293b;
}

.panel-body {
  flex: 1;
  overflow: auto;
  padding: 20px;
}

/* Table styling */
.pk-icon {
  color: #f59e0b;
  font-size: 14px;
  margin-right: 4px;
}

.pk-col {
  font-weight: 600;
  color: #1e293b;
}

.col-type {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 12px;
  color: #6366f1;
}

/* Sync result */
.sync-result {
  padding: 8px 0;
}
</style>
