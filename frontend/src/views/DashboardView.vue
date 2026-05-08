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
        <el-button
          v-if="currentDashboard"
          size="small"
          :loading="savingLayout"
          @click="saveLayout"
        >
          <el-icon><Check /></el-icon>
          {{ savingLayout ? '保存中...' : '保存布局' }}
        </el-button>
        <el-button
          v-if="currentDashboard"
          size="small"
          @click="showAddWidgetDialog = true"
        >
          <el-icon><Plus /></el-icon> 添加组件
        </el-button>
        <el-dropdown v-if="currentDashboard" trigger="click" @command="onDashboardCommand">
          <el-button size="small" text>
            <el-icon><MoreFilled /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="edit">
                <el-icon><Edit /></el-icon> 编辑信息
              </el-dropdown-item>
              <el-dropdown-item command="delete" divided>
                <el-icon><Delete /></el-icon> 删除看板
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button type="primary" size="small" @click="showCreateDialog = true">
          <el-icon><Plus /></el-icon> 新建看板
        </el-button>
      </div>
    </div>

    <!-- Dashboard tabs -->
    <div v-if="dashboards.length > 0" class="dashboard-tabs">
      <el-tabs v-model="activeDashboardId">
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
    <div class="dashboard-body" ref="gridContainerRef">
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
          <el-button @click="showAddWidgetDialog = true">添加组件</el-button>
        </el-empty>
      </div>

      <!-- Draggable widget grid -->
      <div
        v-else
        class="widget-grid"
        :style="gridStyle"
        @dragover="onGridDragOver"
        @drop="onGridDrop"
        @dragleave="onGridDragLeave"
      >
        <DashboardWidget
          v-for="widget in currentDashboard.widgets"
          :key="widget.id"
          :widget="widget"
          :cell-size="cellSize"
          :gap="gridGap"
          :col-count="colCount"
          :data-widget-id="widget.id"
          @refresh="refreshWidget"
          @delete="deleteWidget"
          @chart-type-change="onChartTypeChange"
          @position-change="onWidgetPositionChange"
          @size-change="onWidgetSizeChange"
          @name-change="onWidgetNameChange"
        />
        <!-- Drop placeholder -->
        <div
          v-if="dropPlaceholder.visible"
          class="drop-placeholder"
          :style="dropPlaceholderStyle"
        >
          <el-icon :size="24" color="#409eff"><Plus /></el-icon>
        </div>
      </div>
    </div>

    <!-- Create dashboard dialog -->
    <el-dialog v-model="showCreateDialog" title="新建看板" width="400px">
      <el-form label-position="top">
        <el-form-item label="看板名称">
          <el-input v-model="newDashboardName" placeholder="输入看板名称" />
        </el-form-item>
        <el-form-item label="绑定数据源" required>
          <el-select
            v-model="newDashboardDatasourceId"
            placeholder="请选择数据源"
            style="width: 100%"
          >
            <el-option
              v-for="ds in datasources"
              :key="ds.id"
              :label="ds.name"
              :value="ds.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!newDashboardName.trim() || !newDashboardDatasourceId" @click="createDashboard">创建</el-button>
      </template>
    </el-dialog>

    <!-- Edit dashboard info dialog -->
    <el-dialog v-model="showEditDialog" title="编辑看板信息" width="400px">
      <el-form label-position="top">
        <el-form-item label="看板名称">
          <el-input v-model="editDashboardName" placeholder="输入看板名称" />
        </el-form-item>
        <el-form-item label="绑定数据源" required>
          <el-select
            v-model="editDashboardDatasourceId"
            placeholder="请选择数据源"
            style="width: 100%"
          >
            <el-option
              v-for="ds in datasources"
              :key="ds.id"
              :label="ds.name"
              :value="ds.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEditDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!editDashboardName.trim() || !editDashboardDatasourceId" @click="saveDashboardInfo">保存</el-button>
      </template>
    </el-dialog>

    <!-- Add widget dialog -->
    <el-dialog v-model="showAddWidgetDialog" title="添加组件" width="480px">
      <el-form label-position="top">
        <el-form-item v-if="!currentDashboard?.datasource_id" label="选择数据源">
          <el-select
            v-model="addWidgetDatasourceId"
            placeholder="选择数据源"
            style="width: 100%"
          >
            <el-option
              v-for="ds in datasources"
              :key="ds.id"
              :label="ds.name"
              :value="ds.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="输入问题">
          <el-input
            v-model="addWidgetQuestion"
            placeholder="例如：各VIP等级的用户数量"
            type="textarea"
            :rows="2"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddWidgetDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!addWidgetQuestion.trim() && (!currentDashboard?.datasource_id && !addWidgetDatasourceId)" @click="addQuestionWidget">
          添加
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, reactive, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  DataBoard, Plus, ArrowLeft, Check,
  MoreFilled, Edit, Delete
} from '@element-plus/icons-vue'
import api from '@/api'
import { useDatasourceStore } from '@/stores/datasourceStore'
import DashboardWidget from '@/components/DashboardWidget.vue'

const router = useRouter()
const datasourceStore = useDatasourceStore()

// ===== Types =====
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
  datasource_id: string
  layout_config: Record<string, any> | null
  created_at: string
  updated_at: string
  widgets: Widget[]
}

// ===== Grid constants =====
const colCount = 12
const gridGap = 16
const cellSize = ref(100) // Will be computed from container width

// ===== State =====
const dashboards = ref<Dashboard[]>([])
const activeDashboardId = ref<string>('')
const showCreateDialog = ref(false)
const showEditDialog = ref(false)
const showAddWidgetDialog = ref(false)
const newDashboardName = ref('')
const newDashboardDatasourceId = ref('')
const editDashboardName = ref('')
const editDashboardDatasourceId = ref('')
const savingLayout = ref(false)
const gridContainerRef = ref<HTMLElement>()
const datasources = computed(() => datasourceStore.datasources)

// Add widget state
const addWidgetDatasourceId = ref('')
const addWidgetQuestion = ref('')

// Drop placeholder
const dropPlaceholder = reactive({
  visible: false,
  x: 0,
  y: 0,
  w: 2,
  h: 2,
})

const currentDashboard = computed(() => {
  return dashboards.value.find(d => d.id === activeDashboardId.value) || null
})

const gridStyle = computed(() => {
  return {
    gridTemplateColumns: `repeat(${colCount}, 1fr)`,
    gridAutoRows: '80px',
    gap: `${gridGap}px`,
  }
})

const dropPlaceholderStyle = computed(() => {
  return {
    gridColumn: `${dropPlaceholder.x + 1} / span ${dropPlaceholder.w}`,
    gridRow: `${dropPlaceholder.y + 1} / span ${dropPlaceholder.h}`,
  }
})

// Auto-refresh every 5 minutes
let refreshTimer: ReturnType<typeof setInterval> | null = null

// ===== Compute cell size from container =====
function updateCellSize() {
  if (gridContainerRef.value) {
    const containerWidth = gridContainerRef.value.clientWidth
    // cellSize = (containerWidth - (colCount - 1) * gap) / colCount
    cellSize.value = Math.floor((containerWidth - (colCount - 1) * gridGap) / colCount)
  }
}

// ===== Navigation =====
function goTo(path: string) {
  router.push(path)
}

// ===== Dashboard CRUD =====
async function fetchDashboards() {
  try {
    const res = await api.get('/dashboards')
    dashboards.value = res.data.data || []
    if (dashboards.value.length > 0) {
      if (!activeDashboardId.value) {
        activeDashboardId.value = dashboards.value[0].id
      }
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
    const dash = dashboards.value.find(d => d.id === id)
    if (dash) {
      dash.widgets = detail.widgets || []
      dash.layout_config = detail.layout_config || null
    }
    nextTick(() => {
      normalizeWidgetPositions()
      updateCellSize()
    })
  } catch {
    ElMessage.error('加载看板失败')
  }
}

async function createDashboard() {
  if (!newDashboardName.value.trim()) {
    ElMessage.warning('请输入看板名称')
    return
  }
  try {
    const res = await api.post('/dashboards', {
      name: newDashboardName.value.trim(),
      datasource_id: newDashboardDatasourceId.value,
    })
    dashboards.value.push(res.data)
    activeDashboardId.value = res.data.id
    newDashboardName.value = ''
    showCreateDialog.value = false
    ElMessage.success('看板创建成功')
  } catch {
    ElMessage.error('创建失败')
  }
}

async function saveDashboardInfo() {
  if (!editDashboardName.value.trim() || !editDashboardDatasourceId.value) {
    ElMessage.warning('请填写看板名称和数据源')
    return
  }
  try {
    await api.put(`/dashboards/${activeDashboardId.value}`, {
      name: editDashboardName.value.trim(),
      datasource_id: editDashboardDatasourceId.value,
    })
    const idx = dashboards.value.findIndex(d => d.id === activeDashboardId.value)
    if (idx >= 0) {
      dashboards.value[idx].name = editDashboardName.value.trim()
    }
    showEditDialog.value = false
    ElMessage.success('看板信息已更新')
  } catch {
    ElMessage.error('更新失败')
  }
}

function onDashboardCommand(command: string) {
  if (command === 'edit') {
    if (currentDashboard.value) {
      editDashboardName.value = currentDashboard.value.name
      editDashboardDatasourceId.value = (currentDashboard.value as any).datasource_id || ''
    }
    showEditDialog.value = true
  } else if (command === 'delete') {
    deleteDashboard()
  }
}

async function deleteDashboard() {
  if (!currentDashboard.value) return
  try {
    await ElMessageBox.confirm(`确定要删除看板「${currentDashboard.value.name}」吗？此操作不可恢复。`, '确认删除', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await api.delete(`/dashboards/${activeDashboardId.value}`)
    dashboards.value = dashboards.value.filter(d => d.id !== activeDashboardId.value)
    if (dashboards.value.length > 0) {
      activeDashboardId.value = dashboards.value[0].id
      await fetchDashboardDetail(activeDashboardId.value)
    } else {
      activeDashboardId.value = ''
    }
    ElMessage.success('看板已删除')
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error('删除失败')
  }
}

async function onTabChange() {
  console.log('[TAB] onTabChange, activeId:', activeDashboardId.value)
  if (activeDashboardId.value) {
    await fetchDashboardDetail(activeDashboardId.value)
    console.log('[TAB] fetchDashboardDetail done, widgets count:', currentDashboard.value?.widgets?.length)
  }
}

// ===== Widget operations =====
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
  widget.chart_type = chartType
}

async function onWidgetNameChange(widget: Widget, name: string) {
  widget.question = name
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

// ===== Grid drag & drop =====
function onGridDragOver(e: DragEvent) {
  e.preventDefault()
  if (!e.dataTransfer) return
  e.dataTransfer.dropEffect = 'move'

  if (!currentDashboard.value || !gridContainerRef.value) return

  // Calculate grid position from mouse position
  const rect = gridContainerRef.value.getBoundingClientRect()
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top

  // Compute column width from the grid container
  const containerWidth = rect.width
  const colWidth = (containerWidth - (colCount - 1) * gridGap) / colCount
  const rowHeight = 80

  const gridX = Math.max(0, Math.min(colCount - 2, Math.floor(x / (colWidth + gridGap))))
  const gridY = Math.max(0, Math.floor(y / (rowHeight + gridGap)))

  dropPlaceholder.visible = true
  dropPlaceholder.x = gridX
  dropPlaceholder.y = gridY
}

function onGridDrop(e: DragEvent) {
  e.preventDefault()
  dropPlaceholder.visible = false

  if (!currentDashboard.value || !gridContainerRef.value) return

  const widgetId = e.dataTransfer?.getData('text/plain')
  if (!widgetId) return

  const widget = currentDashboard.value.widgets.find(w => w.id === widgetId)
  if (!widget) return

  // Calculate target position
  const rect = gridContainerRef.value.getBoundingClientRect()
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top

  const containerWidth = rect.width
  const colWidth = (containerWidth - (colCount - 1) * gridGap) / colCount
  const rowHeight = 80

  const targetX = Math.max(0, Math.min(colCount - widget.width, Math.floor(x / (colWidth + gridGap))))
  const targetY = Math.max(0, Math.floor(y / (rowHeight + gridGap)))

  if (targetX !== widget.position_x || targetY !== widget.position_y) {
    // Check for collisions with other widgets
    const collision = hasCollision(widget.id, targetX, targetY, widget.width, widget.height)
    if (!collision) {
      widget.position_x = targetX
      widget.position_y = targetY
    } else {
      ElMessage.warning('该位置与其他组件重叠')
    }
  }
}

function onGridDragLeave(e: DragEvent) {
  // Only hide placeholder if actually leaving the grid
  const rect = gridContainerRef.value?.getBoundingClientRect()
  if (!rect) return
  if (
    e.clientX <= rect.left ||
    e.clientX >= rect.right ||
    e.clientY <= rect.top ||
    e.clientY >= rect.bottom
  ) {
    dropPlaceholder.visible = false
  }
}

// ===== Collision detection =====
function hasCollision(
  excludeId: string,
  x: number,
  y: number,
  w: number,
  h: number
): boolean {
  if (!currentDashboard.value) return false
  for (const widget of currentDashboard.value.widgets) {
    if (widget.id === excludeId) continue
    // Check AABB overlap
    const overlapX = x < widget.position_x + widget.width && x + w > widget.position_x
    const overlapY = y < widget.position_y + widget.height && y + h > widget.position_y
    if (overlapX && overlapY) return true
  }
  return false
}

// Find next available position (for new widgets)
function findNextPosition(w: number, h: number): { x: number; y: number } {
  if (!currentDashboard.value) return { x: 0, y: 0 }

  const maxY = currentDashboard.value.widgets.reduce(
    (max, widget) => Math.max(max, widget.position_y + widget.height),
    0
  )

  // Try each cell, row by row
  for (let y = 0; y <= maxY; y++) {
    for (let x = 0; x <= colCount - w; x++) {
      if (!hasCollision('', x, y, w, h)) {
        return { x, y }
      }
    }
  }

  // Place at bottom
  return { x: 0, y: maxY }
}

// ===== Widget position/size change handlers =====
function onWidgetPositionChange(widget: Widget, positionX: number, positionY: number) {
  if (hasCollision(widget.id, positionX, positionY, widget.width, widget.height)) {
    ElMessage.warning('该位置与其他组件重叠')
    return
  }
  widget.position_x = positionX
  widget.position_y = positionY
}

function onWidgetSizeChange(widget: Widget, width: number, height: number) {
  if (hasCollision(widget.id, widget.position_x, widget.position_y, width, height)) {
    ElMessage.warning('调整大小后与其他组件重叠')
    return
  }
  widget.width = width
  widget.height = height
}

// ===== Save layout =====
async function saveLayout() {
  if (!currentDashboard.value) return
  savingLayout.value = true
  try {
    // Collect all widget positions
    const widgets = currentDashboard.value.widgets.map(w => ({
      id: w.id,
      position_x: w.position_x,
      position_y: w.position_y,
      width: w.width,
      height: w.height,
    }))

    // Save widget name/chart_type + positions
    const widgetPromises = currentDashboard.value.widgets.map(w =>
      api.put(`/dashboards/${activeDashboardId.value}/widgets/${w.id}`, {
        question: w.question,
        chart_type: w.chart_type,
        position_x: w.position_x,
        position_y: w.position_y,
        width: w.width,
        height: w.height,
      })
    )
    await Promise.all(widgetPromises)

    // Save layout_config
    try {
      await api.put(`/dashboards/${activeDashboardId.value}`, {
        layout_config: {
          col_count: colCount,
          grid_gap: gridGap,
          row_height: 80,
        },
      })
    } catch {
      // layout_config may not be supported yet, non-critical
    }

    ElMessage.success('布局已保存')
  } catch {
    ElMessage.error('布局保存失败')
  } finally {
    savingLayout.value = false
  }
}

// ===== Add widget =====
async function addQuestionWidget() {
  if (!addWidgetQuestion.value.trim()) return
  if (!currentDashboard.value) return

  // Use dashboard's bound datasource if available, otherwise use selected one
  const dsId = currentDashboard.value.datasource_id || addWidgetDatasourceId.value
  if (!dsId) {
    ElMessage.warning('请选择数据源')
    return
  }

  try {
    // Find position for new widget
    const pos = findNextPosition(6, 3)

    await api.post(`/dashboards/${activeDashboardId.value}/widgets`, {
      question: addWidgetQuestion.value.trim(),
      datasource_id: dsId,
      chart_type: 'table',
      position_x: pos.x,
      position_y: pos.y,
      width: 6,
      height: 3,
    })

    // Refresh the dashboard to get the new widget
    await fetchDashboardDetail(activeDashboardId.value)

    addWidgetQuestion.value = ''
    showAddWidgetDialog.value = false
    ElMessage.success('组件已添加')
  } catch (e: any) {
    ElMessage.error('添加失败: ' + (e?.response?.data?.detail?.message || e?.message || '未知错误'))
  }
}

// ===== Auto-assign positions for widgets without proper grid data =====
function normalizeWidgetPositions() {
  if (!currentDashboard.value) return
  const widgets = currentDashboard.value.widgets
  if (!widgets || widgets.length === 0) return

  // Fix widgets with default size (1,1) — saved from chat without grid position
  for (const widget of widgets) {
    if (widget.width <= 1 && widget.height <= 1) {
      widget.width = 6
      widget.height = 3
    }
  }

  // Detect and fix overlapping widgets by re-assigning positions
  // Sort by position_y then position_x to preserve relative order
  const sorted = [...widgets].sort((a, b) => a.position_y - b.position_y || a.position_x - b.position_x)
  const placed: { x: number; y: number; w: number; h: number }[] = []

  for (const widget of sorted) {
    // Check if current position collides with already-placed widgets
    const collides = placed.some(p =>
      widget.position_x < p.x + p.w &&
      widget.position_x + widget.width > p.x &&
      widget.position_y < p.y + p.h &&
      widget.position_y + widget.height > p.y
    )

    if (collides) {
      const pos = findNextPosition(widget.width, widget.height)
      widget.position_x = pos.x
      widget.position_y = pos.y
    }

    placed.push({ x: widget.position_x, y: widget.position_y, w: widget.width, h: widget.height })
  }
}

// Tab switch: load detail for the new tab
watch(activeDashboardId, (newId, oldId) => {
  if (newId && newId !== oldId) {
    fetchDashboardDetail(newId)
  }
})

watch(currentDashboard, (newVal) => {
  if (newVal) {
    nextTick(updateCellSize)
  }
})

// ===== Lifecycle =====
onMounted(async () => {
  await datasourceStore.list()
  if (datasourceStore.datasources.length > 0) {
    addWidgetDatasourceId.value = datasourceStore.datasources[0].id
  }
  await fetchDashboards()

  refreshTimer = setInterval(() => {
    if (activeDashboardId.value) {
      fetchDashboardDetail(activeDashboardId.value)
    }
  }, 5 * 60 * 1000)

  // Listen for resize
  window.addEventListener('resize', updateCellSize)
  nextTick(updateCellSize)
})

onUnmounted(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
  }
  window.removeEventListener('resize', updateCellSize)
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

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dashboard-title {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
  margin: 0;
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

/* 12-column CSS Grid layout */
.widget-grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  grid-auto-rows: 80px;
  gap: 16px;
  position: relative;
  min-height: 400px;
}

/* Drop placeholder - visual indicator during drag */
.drop-placeholder {
  border: 2px dashed #409eff;
  border-radius: 8px;
  background: rgba(64, 158, 255, 0.05);
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  transition: all 0.15s ease;
}
</style>
