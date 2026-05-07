<template>
  <div class="dashboard-view">
    <!-- Header -->
    <div class="dashboard-header">
      <div class="header-left">
        <el-button text @click="goTo('/')">
          <el-icon :size="18"><ArrowLeft /></el-icon>
        </el-button>
        <h2 class="dashboard-title">看板</h2>
      </div>
      <div class="header-right">
        <el-button type="primary" size="small" @click="showCreateDialog = true">
          <el-icon><Plus /></el-icon> 新建看板
        </el-button>
      </div>
    </div>

    <!-- Dashboard tabs -->
    <div v-if="dashboards.length > 0" class="dashboard-tabs">
      <el-tabs v-model="activeDashboardId" @tab-click="onTabChange">
        <el-tab-pane
          v-for="dash in dashboards"
          :key="dash.id"
          :label="dash.name"
          :name="dash.id"
        >
          <template #label>
            <span class="tab-label">{{ dash.name }}</span>
          </template>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- Main area -->
    <div class="dashboard-body">
      <!-- Empty state: no dashboard selected -->
      <div v-if="!currentDashboard" class="empty-state">
        <el-empty description="暂无看板数据，从聊天中添加查询到看板">
          <template #image>
            <el-icon :size="80" color="#c0c4cc"><DataBoard /></el-icon>
          </template>
          <el-button type="primary" @click="goTo('/')">去聊天</el-button>
        </el-empty>
      </div>

      <!-- Empty state: dashboard has no widgets -->
      <div v-else-if="!currentDashboard.widgets || currentDashboard.widgets.length === 0" class="empty-state">
        <el-empty description="暂无看板数据，从聊天中添加查询到看板">
          <el-button type="primary" @click="goTo('/')">去聊天</el-button>
        </el-empty>
      </div>

      <!-- Widget grid -->
      <div v-else class="widget-grid">
        <div
          v-for="widget in currentDashboard.widgets"
          :key="widget.id"
          class="widget-card"
        >
          <div class="widget-header">
            <span class="widget-question">{{ widget.question }}</span>
            <div class="widget-actions">
              <el-button size="small" text @click="refreshWidget(widget)">
                <el-icon><Refresh /></el-icon>
              </el-button>
              <el-button size="small" text class="widget-delete" @click="deleteWidget(widget)">
                <el-icon><Close /></el-icon>
              </el-button>
            </div>
          </div>
          <div class="widget-body">
            <ChartRenderer
              :chart-type="widget.chart_type || 'table'"
              :columns="widget.columns || []"
              :rows="widget.rows || []"
              @chart-type-change="(type: string) => onChartTypeChange(widget, type)"
            />
          </div>
          <div class="widget-footer">
            <span>共 {{ widget.row_count || 0 }} 条结果</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Create dashboard dialog -->
    <el-dialog v-model="showCreateDialog" title="新建看板" width="400px">
      <el-input v-model="newDashboardName" placeholder="输入看板名称" @keyup.enter="createDashboard" />
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="createDashboard">创建</el-button>
      </template>
    </el-dialog>

    <!-- Edit dashboard name dialog -->
    <el-dialog v-model="showEditDialog" title="修改看板名称" width="400px">
      <el-input v-model="editDashboardName" placeholder="输入看板名称" @keyup.enter="saveDashboardName" />
      <template #footer>
        <el-button @click="showEditDialog = false">取消</el-button>
        <el-button type="primary" @click="saveDashboardName">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { DataBoard, Plus, Refresh, Close, ArrowLeft } from '@element-plus/icons-vue'
import api from '@/api'
import ChartRenderer from '@/components/ChartRenderer.vue'

const router = useRouter()

interface Widget {
  id: string
  question: string
  query_sql: string
  datasource_id: string
  chart_type: string
  columns: string[]
  rows: Record<string, any>[]
  row_count: number
  position_x: number
  position_y: number
  width: number
  height: number
}

interface Dashboard {
  id: string
  name: string
  created_at: string
  updated_at: string
  widgets: Widget[]
}

const dashboards = ref<Dashboard[]>([])
const activeDashboardId = ref<string>('')
const showCreateDialog = ref(false)
const showEditDialog = ref(false)
const newDashboardName = ref('')
const editDashboardName = ref('')

const currentDashboard = computed(() => {
  return dashboards.value.find(d => d.id === activeDashboardId.value) || null
})

// Auto-refresh every 5 minutes
let refreshTimer: ReturnType<typeof setInterval> | null = null

function goTo(path: string) {
  router.push(path)
}

async function fetchDashboards() {
  try {
    const res = await api.get('/dashboards')
    dashboards.value = res.data.data || []
    if (dashboards.value.length > 0 && !activeDashboardId.value) {
      activeDashboardId.value = dashboards.value[0].id
      await fetchDashboardDetail(activeDashboardId.value)
    }
  } catch {
    ElMessage.error('获取看板列表失败')
    dashboards.value = []
  }
}

async function fetchDashboardDetail(id: string) {
  try {
    const res = await api.get(`/dashboards/${id}`)
    const detail = res.data
    const idx = dashboards.value.findIndex(d => d.id === id)
    if (idx >= 0) {
      dashboards.value[idx] = { ...dashboards.value[idx], widgets: detail.widgets || [] }
    }
    // Auto-refresh widgets with empty rows
    for (const w of (detail.widgets || [])) {
      if (!w.rows || w.rows.length === 0) {
        await refreshWidget(w)
      }
    }
  } catch (e: any) {
    ElMessage.error('加载看板失败')
  }
}

async function createDashboard() {
  if (!newDashboardName.value.trim()) {
    ElMessage.warning('请输入看板名称')
    return
  }
  try {
    const res = await api.post('/dashboards', { name: newDashboardName.value.trim() })
    dashboards.value.push(res.data)
    activeDashboardId.value = res.data.id
    newDashboardName.value = ''
    showCreateDialog.value = false
    ElMessage.success('看板创建成功')
  } catch (e: any) {
    ElMessage.error('创建失败')
  }
}

async function saveDashboardName() {
  if (!editDashboardName.value.trim()) {
    ElMessage.warning('请输入看板名称')
    return
  }
  try {
    await api.put(`/dashboards/${activeDashboardId.value}`, { name: editDashboardName.value.trim() })
    const idx = dashboards.value.findIndex(d => d.id === activeDashboardId.value)
    if (idx >= 0) {
      dashboards.value[idx].name = editDashboardName.value.trim()
    }
    showEditDialog.value = false
    ElMessage.success('名称已更新')
  } catch {
    ElMessage.error('更新失败')
  }
}

function onTabChange() {
  if (activeDashboardId.value) {
    fetchDashboardDetail(activeDashboardId.value)
  }
}

async function refreshWidget(widget: Widget) {
  if (!activeDashboardId.value) return
  try {
    const res = await api.post(`/dashboards/${activeDashboardId.value}/widgets/${widget.id}/refresh`)
    if (!currentDashboard.value) return
    const idx = currentDashboard.value.widgets.findIndex(w => w.id === widget.id)
    if (idx >= 0) {
      currentDashboard.value.widgets[idx] = res.data
    }
  } catch {
    ElMessage.warning('刷新失败')
  }
}

async function onChartTypeChange(widget: Widget, chartType: string) {
  if (!activeDashboardId.value) return
  try {
    await api.put(`/dashboards/${activeDashboardId.value}/widgets/${widget.id}`, { chart_type: chartType })
    widget.chart_type = chartType
  } catch {
    ElMessage.warning('图表类型更新失败')
  }
}

async function deleteWidget(widget: Widget) {
  try {
    await ElMessageBox.confirm('确定要删除这个组件吗？', '确认删除', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await api.delete(`/dashboards/${activeDashboardId.value}/widgets/${widget.id}`)
    if (!currentDashboard.value) return
    const idx = currentDashboard.value.widgets.findIndex(w => w.id === widget.id)
    if (idx >= 0) {
      currentDashboard.value.widgets.splice(idx, 1)
    }
    ElMessage.success('已删除')
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error('删除失败')
  }
}

onMounted(async () => {
  await fetchDashboards()
  refreshTimer = setInterval(() => {
    if (activeDashboardId.value) {
      fetchDashboardDetail(activeDashboardId.value)
    }
  }, 5 * 60 * 1000)
})

onUnmounted(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
  }
})
</script>

<style scoped>
.dashboard-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #f5f7fa;
}

.dashboard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-bottom: 1px solid #e4e7ed;
  background: white;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.dashboard-title {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
  margin: 0;
  cursor: pointer;
}

.dashboard-title:hover {
  color: #409eff;
}

.dashboard-tabs {
  background: white;
  padding: 0 20px;
  border-bottom: 1px solid #e4e7ed;
}

.tab-label {
  font-size: 14px;
}

.dashboard-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 400px;
}

.widget-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  grid-auto-rows: minmax(200px, auto);
  gap: 16px;
}

.widget-card {
  background: white;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: box-shadow 0.2s;
  min-height: 200px;
}

.widget-card:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

.widget-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid #ebeef5;
}

.widget-question {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.widget-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.widget-delete {
  color: #f56c6c;
}

.widget-body {
  flex: 1;
  padding: 12px 14px;
  overflow: auto;
}

.widget-footer {
  padding: 6px 14px;
  border-top: 1px solid #ebeef5;
  color: #909399;
  font-size: 12px;
}
</style>
