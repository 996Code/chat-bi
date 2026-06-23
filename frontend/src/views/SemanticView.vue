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

    <!-- 表列表 (左侧) + 详情 (右侧) -->
    <div v-loading="loading" v-if="model" class="content-layout">
      <el-card class="table-list">
        <template #header>
          <div style="display:flex;justify-content:space-between;align-items:center">
            <b>表 ({{ filteredModels.length }})</b>
            <el-switch v-model="showSystemTables" size="small" active-text="系统表" inline-prompt style="--el-switch-on-color:#909399" />
          </div>
        </template>
        <el-input v-model="search" placeholder="搜索表名..." clearable size="small" style="margin-bottom: 12px" />
        <div
          v-for="m in filteredModels"
          :key="m.name"
          class="table-item"
          :class="{ active: selected?.name === m.name }"
          @click="selected = m"
        >
          <div class="table-name">{{ m.display_name }}</div>
          <div class="table-meta">
            <span>{{ m.name }}</span>
            <span class="badges">
              <el-badge :value="m.columns.length" type="primary" />列
              <el-badge :value="m.relationships.length" type="success" />关系
            </span>
          </div>
        </div>
      </el-card>

      <el-card class="table-detail" v-if="selected">
        <template #header>
          <div class="detail-header">
            <div>
              <b style="font-size: 1.1rem">{{ selected.display_name }}</b>
              <code style="margin-left: 8px; color: #999">{{ selected.name }}</code>
            </div>
            <div>
              <el-tag size="small" :type="sourceTag(selected.source)">{{ sourceLabel(selected.source, selected.confidence) }}</el-tag>
            </div>
          </div>
        </template>

        <p v-if="selected.description" class="desc">{{ selected.description }}</p>

        <h4>列 ({{ selected.columns.length }})</h4>
        <el-table :data="selected.columns" size="small" border>
          <el-table-column prop="name" label="列名" min-width="130">
            <template #default="{ row }"><code>{{ row.name }}</code></template>
          </el-table-column>
          <el-table-column prop="display_name" label="中文名" min-width="130" />
          <el-table-column prop="data_type" label="类型" width="140" />
          <el-table-column label="语义" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="semanticTag(row.semantic_type)">{{ row.semantic_type || '-' }}</el-tag>
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
      </el-card>
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
            <el-button
              v-if="!v.is_current"
              size="small" type="warning" plain
              :loading="rollingBack === v.version"
              @click="doRollback(v.version)"
            >
              回滚到此版本
            </el-button>
          </div>
          <div class="version-id"><code>{{ v.id.slice(0, 8) }}</code></div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Refresh, Clock } from '@element-plus/icons-vue'
import { semantic, type SemanticModel, type SemanticTableModel } from '@/api'

const route = useRoute()
const model = ref<SemanticModel | null>(null)
const selected = ref<SemanticTableModel | null>(null)
const loading = ref(false)
// 版本历史 + 回滚 (T015)
const versionDrawer = ref(false)
const versionLoading = ref(false)
const versions = ref<{ id: string; version: number; is_current: boolean }[]>([])
const rollingBack = ref<number | null>(null)
const search = ref('')
const showSystemTables = ref(false) // 默认隐藏系统表 (ChatBI 元数据表, 用户不查)

// 系统表: ChatBI 自己的元数据表, 业务用户不关心, 默认隐藏
const SYSTEM_TABLES = new Set([
  'tenants', 'users', 'data_sources', 'semantic_models',
  'conversations', 'saved_queries', 'audit_logs', 'feedback',
])

const filteredModels = computed(() => {
  if (!model.value) return []
  const q = search.value.toLowerCase()
  return model.value.content.models.filter(m => {
    // 默认隐藏系统表 (开关打开才显示)
    if (!showSystemTables.value && SYSTEM_TABLES.has(m.name)) return false
    return m.name.toLowerCase().includes(q) || m.display_name.toLowerCase().includes(q)
  })
})

async function fetchData() {
  const dsId = route.query.data_source_id as string
  if (!dsId) return
  loading.value = true
  try {
    const { data } = await semantic.current(dsId)
    model.value = data
    if (data && data.content.models.length > 0) {
      // 默认选第一个业务表 (跳过系统表)
      selected.value = data.content.models.find(m => !SYSTEM_TABLES.has(m.name))
        ?? data.content.models[0]
    }
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e.response?.data?.detail || e.message))
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
    ElMessage.error('加载版本历史失败: ' + (e.response?.data?.detail || e.message))
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
    ElMessage.error('回滚失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    rollingBack.value = null
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
.content-layout { display: flex; gap: 16px; }
.table-list { width: 280px; flex-shrink: 0; }
.table-detail { flex: 1; }
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
h4 { margin: 16px 0 8px; color: #303030; }
.version-item {
  padding: 12px 0; border-bottom: 1px solid #ebeef5;
}
.version-head {
  display: flex; justify-content: space-between; align-items: center;
}
.version-id { font-size: 0.8rem; color: #999; margin-top: 6px; }
</style>
