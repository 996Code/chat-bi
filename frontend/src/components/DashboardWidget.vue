<template>
  <div
    class="dashboard-widget"
    :class="{ dragging: isDragging, resizing: isResizing, 'drag-over': isDragOver }"
    :style="widgetStyle"
    @dragover.prevent="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
  >
    <!-- Drag handle: top header bar -->
    <div
      class="widget-header"
      draggable="true"
      @dragstart="onDragStart"
      @dragend="onDragEnd"
    >
      <div class="widget-header-left">
        <el-icon class="drag-handle" :size="14"><Rank /></el-icon>
        <span
          v-if="!isEditingName"
          class="widget-question"
          :title="widget.question"
          @dblclick="startEditName"
        >{{ widget.question }}</span>
        <el-input
          v-else
          ref="nameInputRef"
          v-model="editName"
          size="small"
          class="widget-name-input"
          @blur="saveName"
          @keyup.enter="saveName"
          @keyup.escape="cancelEditName"
        />
      </div>
      <div class="widget-actions">
        <el-button size="small" text @click="$emit('refresh', widget)">
          <el-icon><Refresh /></el-icon>
        </el-button>
        <el-button size="small" text class="widget-delete" @click="$emit('delete', widget)">
          <el-icon><Close /></el-icon>
        </el-button>
      </div>
    </div>

    <!-- Widget body -->
    <div class="widget-body">
      <div v-if="widget.error" class="widget-error">
        <el-icon :size="24" color="#f56c6c"><WarningFilled /></el-icon>
        <span>{{ widget.error_msg }}</span>
      </div>
      <ChartRenderer
        v-else
        :chart-type="widget.chart_type || 'table'"
        :columns="widget.columns || []"
        :rows="widget.rows || []"
        @chart-type-change="(type: string) => $emit('chart-type-change', widget, type)"
      />
    </div>

    <!-- Widget footer -->
    <div class="widget-footer">
      <span>{{ widget.row_count || 0 }} 条结果</span>
    </div>

    <!-- Resize handle: bottom-right corner -->
    <div
      class="resize-handle"
      @mousedown.prevent="onResizeStart"
    >
      <el-icon :size="12"><BottomRight /></el-icon>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick } from 'vue'
import { Refresh, Close, Rank, BottomRight, WarningFilled } from '@element-plus/icons-vue'
import ChartRenderer from '@/components/ChartRenderer.vue'

export interface WidgetData {
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
  error?: string
  error_msg?: string
}

const props = defineProps<{
  widget: WidgetData
  cellSize: number
  gap: number
  colCount: number
}>()

const emit = defineEmits<{
  refresh: [widget: WidgetData]
  delete: [widget: WidgetData]
  'chart-type-change': [widget: WidgetData, type: string]
  'position-change': [widget: WidgetData, positionX: number, positionY: number]
  'size-change': [widget: WidgetData, width: number, height: number]
  'name-change': [widget: WidgetData, name: string]
}>()

const isDragging = ref(false)
const isResizing = ref(false)
const isDragOver = ref(false)
const isEditingName = ref(false)
const editName = ref('')
const nameInputRef = ref<InstanceType<typeof import('element-plus')['ElInput']>>()

function startEditName() {
  editName.value = props.widget.question
  isEditingName.value = true
  nextTick(() => nameInputRef.value?.focus())
}

function saveName() {
  isEditingName.value = false
  const trimmed = editName.value.trim()
  if (trimmed && trimmed !== props.widget.question) {
    emit('name-change', props.widget, trimmed)
  }
}

function cancelEditName() {
  isEditingName.value = false
}

const widgetStyle = computed(() => {
  const colWidth = props.cellSize
  const rowHeight = 80
  const gapVal = props.gap
  const x = props.widget.position_x
  const y = props.widget.position_y
  const w = props.widget.width
  const h = props.widget.height

  return {
    gridColumn: `${x + 1} / span ${w}`,
    gridRow: `${y + 1} / span ${h}`,
    minWidth: `${2 * colWidth + gapVal}px`,
    minHeight: `${2 * rowHeight + gapVal}px`,
  }
})

// ===== Drag =====
function onDragStart(e: DragEvent) {
  if (!e.dataTransfer) return
  isDragging.value = true
  e.dataTransfer.effectAllowed = 'move'
  e.dataTransfer.setData('text/plain', props.widget.id)
  // Set a transparent drag image for cleaner UX
  const ghost = (e.target as HTMLElement).closest('.dashboard-widget') as HTMLElement
  if (ghost && e.dataTransfer.setDragImage) {
    e.dataTransfer.setDragImage(ghost, 20, 20)
  }
}

function onDragEnd() {
  isDragging.value = false
  isDragOver.value = false
}

function onDragOver(e: DragEvent) {
  e.preventDefault()
  if (e.dataTransfer) {
    e.dataTransfer.dropEffect = 'move'
  }
  isDragOver.value = true
}

function onDragLeave() {
  isDragOver.value = false
}

function onDrop(e: DragEvent) {
  isDragOver.value = false
  // The parent grid handles the actual repositioning
  // This just allows visual feedback for drop targets
}

// ===== Resize =====
function onResizeStart(e: MouseEvent) {
  isResizing.value = true
  const startX = e.clientX
  const startY = e.clientY
  const startWidth = props.widget.width
  const startHeight = props.widget.height
  const colWidth = props.cellSize
  const rowHeight = 80
  const gapVal = props.gap

  function onMouseMove(ev: MouseEvent) {
    const dx = ev.clientX - startX
    const dy = ev.clientY - startY

    const newWidth = Math.max(2, Math.min(props.colCount - props.widget.position_x,
      startWidth + Math.round(dx / (colWidth + gapVal))))
    const newHeight = Math.max(2, startHeight + Math.round(dy / (rowHeight + gapVal)))

    // Update grid position via style for instant feedback
    const el = document.querySelector(`.dashboard-widget[data-widget-id="${props.widget.id}"]`) as HTMLElement
    if (el) {
      el.style.gridColumn = `${props.widget.position_x + 1} / span ${newWidth}`
      el.style.gridRow = `${props.widget.position_y + 1} / span ${newHeight}`
    }
  }

  function onMouseUp(ev: MouseEvent) {
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
    isResizing.value = false

    const dx = ev.clientX - startX
    const dy = ev.clientY - startY

    const newWidth = Math.max(2, Math.min(props.colCount - props.widget.position_x,
      startWidth + Math.round(dx / (colWidth + gapVal))))
    const newHeight = Math.max(2, startHeight + Math.round(dy / (rowHeight + gapVal)))

    if (newWidth !== startWidth || newHeight !== startHeight) {
      emit('size-change', props.widget, newWidth, newHeight)
    }
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}
</script>

<style scoped>
.dashboard-widget {
  background: white;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: box-shadow 0.2s, opacity 0.2s;
  position: relative;
  user-select: none;
}

.dashboard-widget:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

.dashboard-widget.dragging {
  opacity: 0.4;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
}

.dashboard-widget.drag-over {
  border: 2px dashed #409eff;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.2);
}

.dashboard-widget.resizing {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
}

.widget-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid #ebeef5;
  cursor: grab;
  background: #fafafa;
  border-radius: 8px 8px 0 0;
}

.widget-header:active {
  cursor: grabbing;
}

.widget-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.drag-handle {
  color: #c0c4cc;
  cursor: grab;
  flex-shrink: 0;
}

.widget-header:hover .drag-handle {
  color: #909399;
}

.widget-question {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: text;
}

.widget-question:hover {
  color: #409eff;
}

.widget-name-input {
  flex: 1;
  min-width: 0;
}

.widget-name-input :deep(.el-input__inner) {
  font-size: 13px;
  font-weight: 500;
  padding: 0 4px;
  height: 24px;
  line-height: 24px;
}

.widget-actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

.widget-delete {
  color: #f56c6c;
}

.widget-body {
  flex: 1;
  padding: 8px 12px;
  overflow: auto;
  min-height: 0;
}

.widget-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
  color: #f56c6c;
  font-size: 13px;
  text-align: center;
  padding: 16px;
}

.widget-footer {
  padding: 4px 12px;
  border-top: 1px solid #ebeef5;
  color: #909399;
  font-size: 12px;
}

.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: se-resize;
  color: #c0c4cc;
  opacity: 0;
  transition: opacity 0.2s;
}

.dashboard-widget:hover .resize-handle {
  opacity: 1;
}

.resize-handle:hover {
  color: #409eff;
}
</style>
