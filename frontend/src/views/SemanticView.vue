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
        <template #header><b>表 ({{ model.content.models.length }})</b></template>
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Refresh } from '@element-plus/icons-vue'
import { semantic, type SemanticModel, type SemanticTableModel } from '@/api'

const route = useRoute()
const model = ref<SemanticModel | null>(null)
const selected = ref<SemanticTableModel | null>(null)
const loading = ref(false)
const search = ref('')

const filteredModels = computed(() => {
  if (!model.value) return []
  const q = search.value.toLowerCase()
  return model.value.content.models.filter(
    m => m.name.toLowerCase().includes(q) || m.display_name.toLowerCase().includes(q),
  )
})

async function fetchData() {
  const dsId = route.query.data_source_id as string
  if (!dsId) return
  loading.value = true
  try {
    const { data } = await semantic.current(dsId)
    model.value = data
    if (data && data.content.models.length > 0) {
      selected.value = data.content.models[0]
    }
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
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
</style>
