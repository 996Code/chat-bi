<template>
  <div class="data-dictionary">
    <div class="dict-header">
      <span class="title">数据字典</span>
      <el-button size="small" text @click="$emit('close')">
        <el-icon><Close /></el-icon>
      </el-button>
    </div>

    <div class="dict-content" v-loading="loading">
      <!-- Table list -->
      <el-tree
        :data="treeData"
        :props="treeProps"
        default-expand-all
        :expand-on-click-node="false"
      >
        <template #default="{ data }">
          <span class="tree-node">
            <el-icon v-if="data.columns"><Folder /></el-icon>
            <el-icon v-else><Document /></el-icon>
            <span>{{ data.label }}</span>
            <span v-if="data.description" class="node-desc">{{ data.description }}</span>
          </span>
        </template>
      </el-tree>

      <el-empty v-if="!loading && tables.length === 0" description="请先扫描数据源" size="small" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { Close, Folder, Document } from '@element-plus/icons-vue'
import api from '@/api'

const props = defineProps<{
  datasourceId: string | null
}>()

defineEmits<{
  close: []
}>()

const loading = ref(false)
const tables = ref<any[]>([])
const treeProps = { children: 'children', label: 'label' }

const treeData = computed(() =>
  tables.value.map(t => ({
    label: t.name,
    description: t.description || '',
    children: (t.columns || []).map((c: any) => ({
      label: `${c.name} (${c.type || c.data_type || ''})`,
      description: c.comment || (c.primary ? '主键' : '') || '',
    })),
  }))
)

watch(() => props.datasourceId, async (id) => {
  if (!id) {
    tables.value = []
    return
  }
  loading.value = true
  try {
    const res = await api.get(`/datasources/${id}/schema`)
    tables.value = res.data?.models || []
  } catch {
    tables.value = []
  } finally {
    loading.value = false
  }
}, { immediate: true })
</script>

<style scoped>
.data-dictionary {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: white;
  border-left: 1px solid #e4e7ed;
  width: 280px;
  overflow: hidden;
}

.dict-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #e4e7ed;
}

.dict-header .title {
  font-weight: 600;
  font-size: 14px;
}

.dict-content {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.tree-node {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  min-width: 0;
  max-width: 100%;
}

.tree-node span:last-child {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-desc {
  color: #909399;
  font-size: 11px;
  margin-left: 4px;
  flex-shrink: 0;
}
</style>
