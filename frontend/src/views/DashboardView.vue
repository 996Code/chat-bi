<template>
  <div class="dash-view">
    <!-- 顶部操作栏 -->
    <div class="page-header">
      <el-button :icon="ArrowLeft" text @click="$router.push('/chat')">返回</el-button>
      <span class="title">看板</span>
      <el-button type="primary" size="small" :icon="Plus" @click="openCreateDialog">新建看板</el-button>
      <el-button
        :type="editMode ? 'warning' : ''" size="small"
        :icon="editMode ? Check : EditPen"
        @click="toggleEditMode"
      >{{ editMode ? '完成布局' : '编辑布局' }}</el-button>
      <el-button
        :icon="Refresh" text :loading="refreshAllLoading"
        @click="refreshAllWidgets"
      >刷新全部</el-button>
    </div>

    <!-- 看板 Tab 列表 -->
    <div v-if="dashList.length" class="dash-tabs">
      <div
        v-for="d in dashList"
        :key="d.id"
        :class="['dash-tab', { active: activeDashId === d.id }]"
        @click="switchDashboard(d.id)"
      >
        <span class="tab-name">{{ d.name }}</span>
        <el-dropdown trigger="click" @command="(cmd: string) => handleDashCommand(cmd, d)">
          <el-icon class="tab-more" @click.stop><MoreFilled /></el-icon>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="rename">重命名</el-dropdown-item>
              <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>

    <div v-loading="loading">
      <!-- 无看板 -->
      <el-empty v-if="!dashList.length && !loading" description="还没有看板, 点击上方新建">
        <el-button type="primary" @click="openCreateDialog">新建看板</el-button>
      </el-empty>

      <!-- 看板内容: gridstack 拖拽布局 (始终渲染, initGrid 控制内部 DOM) -->
      <div v-if="activeDashId" class="grid-container">
        <div ref="gridEl" class="grid-stack"></div>
      </div>

      <!-- 有看板但无 widget -->
      <el-empty
        v-if="activeDashId && !widgets.length && !loading"
        description="该看板暂无组件, 去对话页查询后保存"
      >
        <el-button type="primary" @click="$router.push('/chat')">去提问</el-button>
      </el-empty>
    </div>

    <!-- 新建/重命名看板弹窗 -->
    <el-dialog v-model="showDashDialog" :title="dashDialogMode === 'create' ? '新建看板' : '重命名看板'" width="400px">
      <el-input v-model="dashNameInput" placeholder="看板名称" maxlength="50" show-word-limit />
      <template #footer>
        <el-button @click="showDashDialog = false">取消</el-button>
        <el-button type="primary" :loading="dashDialogLoading" @click="submitDashDialog">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Refresh, Plus, MoreFilled, EditPen, Check } from '@element-plus/icons-vue'
import { dashboard, type DashboardItem, type DashboardWidget } from '@/api'
import { extractErrorDetail } from '@/utils/error'
import { GridStack } from 'gridstack'
import 'gridstack/dist/gridstack.min.css'
import * as echarts from 'echarts'

const dashList = ref<DashboardItem[]>([])
const activeDashId = ref('')
const widgets = ref<DashboardWidget[]>([])
const loading = ref(false)
const refreshAllLoading = ref(false)

// gridstack 实例 + 编辑模式
const gridEl = ref<HTMLElement>()
let grid: GridStack | null = null
const editMode = ref(false)

// 每个 widget 的实时数据 + 加载态 (keyed by widget id)
const widgetLoading = reactive<Record<string, boolean>>({})
const widgetError = reactive<Record<string, string>>({})
const widgetData = reactive<Record<string, { columns: any[]; rows: any[][]; row_count: number }>>({})
const widgetCharts = reactive<Record<string, any>>({})
const widgetRefreshedAt = reactive<Record<string, string>>({})

// 图表实例
const chartInstances: Record<string, echarts.ECharts> = {}

// 加载版本号: 每次 loadCurrentDashboard 递增, 防止异步回调操作已切换的看板
let loadVersion = 0

// 看板弹窗
const showDashDialog = ref(false)
const dashDialogMode = ref<'create' | 'rename'>('create')
const dashNameInput = ref('')
const dashDialogLoading = ref(false)
const renameTargetId = ref('')

function formatTime(ts: string): string {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return ''
  }
}

// ── 加载看板列表 ──

async function loadDashList() {
  try {
    const { data } = await dashboard.list()
    dashList.value = data
    if (data.length && !activeDashId.value) {
      activeDashId.value = data[0].id
      await loadCurrentDashboard()
    }
  } catch {
    dashList.value = []
  }
}

async function switchDashboard(id: string) {
  activeDashId.value = id
  await loadCurrentDashboard()
}

async function loadCurrentDashboard() {
  if (!activeDashId.value) return
  loading.value = true
  const currentVersion = ++loadVersion
  disposeAllCharts()
  // 清理状态
  Object.keys(widgetLoading).forEach(k => delete widgetLoading[k])
  Object.keys(widgetError).forEach(k => delete widgetError[k])
  Object.keys(widgetData).forEach(k => delete widgetData[k])
  Object.keys(widgetCharts).forEach(k => delete widgetCharts[k])
  Object.keys(widgetRefreshedAt).forEach(k => delete widgetRefreshedAt[k])

  try {
    const { data } = await dashboard.get(activeDashId.value)
    // 切换走了就放弃
    if (loadVersion !== currentVersion) return
    widgets.value = data.widgets || []
    // initGrid 会销毁旧 grid 并重建, 然后刷新每个 widget 数据
    await nextTick()
    initGrid()
    await nextTick()
    // 传版本号给 refreshWidget, 防止异步回调操作已切换的看板
    widgets.value.forEach(w => refreshWidget(w, currentVersion))
  } catch {
    widgets.value = []
    // 清空 grid 内容
    if (grid) {
      try { grid.destroy(true) } catch { /* ignore */ }
      grid = null
    }
  } finally {
    loading.value = false
  }
}

function disposeAllCharts() {
  Object.values(chartInstances).forEach(c => c.dispose())
  Object.keys(chartInstances).forEach(k => delete chartInstances[k])
}

// ── gridstack 初始化 ──

function initGrid() {
  // 彻底销毁旧 grid: destroy + 清空 DOM + 移除残留属性
  if (grid) {
    try { grid.destroy(false) } catch { /* 忽略 */ }
    grid = null
  }
  if (gridEl.value) {
    gridEl.value.innerHTML = ''
    // GridStack.init 在元素上存 el.grid, 必须清除否则 init 会复用旧实例
    delete (gridEl.value as any).grid
  }
  if (!gridEl.value) return

  grid = GridStack.init({
    column: 12,
    cellHeight: 70,
    margin: 8,
    disableDrag: !editMode.value,
    disableResize: !editMode.value,
    staticGrid: !editMode.value,
    animate: true,
  }, gridEl.value)

  for (const w of widgets.value) {
    const el = createWidgetEl(w)
    grid.makeWidget(el)
  }

  // 拖拽/缩放结束 → 保存布局
  grid.on('change', (_event: any, changedItems: any[]) => {
    if (!editMode.value) return
    const layout = changedItems.map(item => ({
      id: item.el.dataset.wid,
      x: item.x, y: item.y, w: item.w, h: item.h,
    })).filter(i => i.id)
    if (layout.length) saveLayout(layout)
  })
}

function createWidgetEl(w: DashboardWidget): HTMLElement {
  const el = document.createElement('div')
  el.className = 'grid-stack-item'
  el.dataset.wid = w.id
  el.setAttribute('gs-x', String(w.position_x || 0))
  el.setAttribute('gs-y', String(w.position_y || 0))
  el.setAttribute('gs-w', String(w.width || 6))
  el.setAttribute('gs-h', String(w.height || 5))
  el.innerHTML = `
    <div class="grid-stack-item-content widget-card">
      <div class="widget-header">
        <span class="widget-title">${escapeHtml(w.question)}</span>
        <div class="widget-actions">
          <button class="widget-btn refresh-btn" data-action="refresh" title="刷新">
            <svg viewBox="0 0 1024 1024" width="14" height="14"><path fill="currentColor" d="M909.1 209.3l-56.4 44.1C775.8 155.1 656.2 92 521.9 92 290 92 102.3 279.5 102 511.5 101.7 743.7 289.8 932 521.9 932c181.3 0 335.8-115 394.6-276.1 1.5-4.2-.7-8.9-4.9-10.3l-56.7-19.5a8 8 0 0 0-10.1 4.8c-1.8 5-3.8 10-5.9 14.9-17.3 41-42.1 77.8-73.7 109.4A344.77 344.77 0 0 1 655.9 829c-42.3 17.9-87.4 27-133.8 27-46.5 0-91.5-9.1-133.8-27A341.5 341.5 0 0 1 279 755.2a342.16 342.16 0 0 1-73.7-109.4c-17.9-42.4-27-87.4-27-133.9s9.1-91.5 27-133.9c17.3-41 42.1-77.8 73.7-109.4 31.6-31.6 68.4-56.4 109.3-73.7 42.3-17.9 87.4-27 133.8-27 46.5 0 91.5 9.1 133.8 27a341.5 341.5 0 0 1 109.3 73.8c9.9 9.9 19.2 20.4 27.8 31.4l-60.2 47a8 8 0 0 0 3 14.1l175.6 43c5 1.2 9.9-2.6 9.9-7.7l.8-180.9c-.1-6.6-7.8-10.3-13-6.2z"/></svg>
          </button>
          ${editMode.value ? `<button class="widget-btn delete-btn" data-action="delete" title="删除">
            <svg viewBox="0 0 1024 1024" width="14" height="14"><path fill="currentColor" d="M360 184h-8c4.4 0 8-3.6 8-8v8h304v-8c0 4.4 3.6 8 8 8h-8v72h72v-80c0-35.3-28.7-64-64-64H352c-35.3 0-64 28.7-64 64v80h72v-72zm504 72H160c-17.7 0-32 14.3-32 32v32c0 4.4 3.6 8 8 8h60.4l24.7 523c1.6 34.1 29.8 61 63.9 61h454c34.2 0 62.3-26.8 63.9-61l24.7-523H920c4.4 0 8-3.6 8-8v-32c0-17.7-14.3-32-32-32zM731.3 840H292.7l-24.2-512h487l-24.2 512z"/></svg>
          </button>` : ''}
        </div>
      </div>
      <div class="widget-body" data-wid="${w.id}">
        <div class="widget-loading">加载中...</div>
      </div>
      <div class="widget-footer" data-wid="${w.id}-footer"></div>
    </div>
  `

  // 绑定按钮事件
  el.querySelector('.refresh-btn')?.addEventListener('click', (e) => {
    e.stopPropagation()
    refreshWidget(w)
  })
  el.querySelector('.delete-btn')?.addEventListener('click', (e) => {
    e.stopPropagation()
    deleteWidget(w)
  })

  return el
}

function escapeHtml(s: string): string {
  const div = document.createElement('div')
  div.textContent = s
  return div.innerHTML
}

async function saveLayout(layout: { id: string; x: number; y: number; w: number; h: number }[]) {
  try {
    await dashboard.updateLayout(activeDashId.value, layout)
    ElMessage.success('布局已保存')
  } catch (e: any) {
    ElMessage.error('布局保存失败: ' + (extractErrorDetail(e)))
  }
}

function toggleEditMode() {
  editMode.value = !editMode.value
  if (grid) {
    grid.setStatic(!editMode.value)
    // 更新 DOM 显示删除按钮
    nextTick(() => initGrid())
  }
  ElMessage.info(editMode.value ? '已进入编辑模式, 可拖拽/缩放组件' : '已退出编辑模式')
}

// ── 实时查询单个 widget ──

async function refreshWidget(w: DashboardWidget, version?: number) {
  widgetLoading[w.id] = true
  widgetError[w.id] = ''
  // 刷新按钮旋转动画
  const refreshBtn = document.querySelector(`.grid-stack-item[data-wid="${w.id}"] .refresh-btn`)
  refreshBtn?.classList.add('spinning')
  try {
    const { data } = await dashboard.refreshWidget(activeDashId.value, w.id)
    // 看板已切换, 丢弃旧请求结果
    if (version != null && version !== loadVersion) return
    widgetData[w.id] = {
      columns: data.columns || [],
      rows: data.rows || [],
      row_count: data.row_count || 0,
    }
    widgetCharts[w.id] = data.chart_option || null
    widgetRefreshedAt[w.id] = new Date().toISOString()
    renderWidgetContent(w)
  } catch (e: any) {
    if (version != null && version !== loadVersion) return
    widgetError[w.id] = extractErrorDetail(e)
    renderWidgetContent(w)
  } finally {
    widgetLoading[w.id] = false
    refreshBtn?.classList.remove('spinning')
  }
}

function renderWidgetContent(w: DashboardWidget) {
  const bodyEl = document.querySelector(`.widget-body[data-wid="${w.id}"]`) as HTMLElement
  if (!bodyEl) return
  const footerEl = document.querySelector(`.widget-footer[data-wid="${w.id}-footer"]`) as HTMLElement

  // 清理旧图表
  if (chartInstances[w.id]) {
    chartInstances[w.id].dispose()
    delete chartInstances[w.id]
  }

  // 出错
  if (widgetError[w.id]) {
    bodyEl.innerHTML = `<div class="widget-error">${escapeHtml(widgetError[w.id])}</div>`
    return
  }

  const chartOpt = widgetCharts[w.id]
  const data = widgetData[w.id]

  // 有图表配置 → 渲染图表
  if (chartOpt && chartOpt.series) {
    bodyEl.innerHTML = `<div class="widget-chart" data-wid="${w.id}-chart" style="width:100%;height:100%;min-height:220px"></div>`
    nextTick(() => {
      const chartEl = document.querySelector(`[data-wid="${w.id}-chart"]`) as HTMLElement
      if (chartEl) {
        const chart = echarts.init(chartEl)
        chart.setOption(chartOpt)
        chartInstances[w.id] = chart
      }
    })
  }
  // 无图表但有数据 → 渲染表格
  else if (data?.columns?.length) {
    const rowsHtml = data.rows.slice(0, 20).map((row: any[]) =>
      `<tr>${data.columns.map((c: string, i: number) => `<td>${escapeHtml(String(row[i] ?? ''))}</td>`).join('')}</tr>`
    ).join('')
    const headerHtml = data.columns.map((c: string) => `<th>${escapeHtml(c)}</th>`).join('')
    bodyEl.innerHTML = `
      <div class="widget-table-wrap">
        <table class="widget-table"><thead><tr>${headerHtml}</tr></thead><tbody>${rowsHtml}</tbody></table>
        ${data.row_count > 20 ? `<div class="table-more">共 ${data.row_count} 行, 仅展示前 20 行</div>` : ''}
      </div>`
  } else {
    bodyEl.innerHTML = `<div class="widget-empty">暂无数据</div>`
  }

  // 页脚
  if (footerEl) {
    const parts: string[] = []
    if (data?.row_count != null) parts.push(`${data.row_count} 行`)
    if (widgetRefreshedAt[w.id]) parts.push(formatTime(widgetRefreshedAt[w.id]))
    footerEl.textContent = parts.join(' · ')
  }
}

async function refreshAllWidgets() {
  if (!widgets.value.length) return
  refreshAllLoading.value = true
  try {
    await Promise.all(widgets.value.map(w => refreshWidget(w, loadVersion)))
    ElMessage.success('已刷新全部')
  } finally {
    refreshAllLoading.value = false
  }
}

// ── 看板 CRUD ──

function openCreateDialog() {
  dashDialogMode.value = 'create'
  dashNameInput.value = ''
  showDashDialog.value = true
}

function handleDashCommand(cmd: string, d: DashboardItem) {
  if (cmd === 'rename') {
    dashDialogMode.value = 'rename'
    renameTargetId.value = d.id
    dashNameInput.value = d.name
    showDashDialog.value = true
  } else if (cmd === 'delete') {
    ElMessageBox.confirm(`确定删除看板「${d.name}」? 所有组件将一并删除。`, '删除确认', {
      type: 'warning',
    }).then(async () => {
      try {
        await dashboard.delete(d.id)
        dashList.value = dashList.value.filter(x => x.id !== d.id)
        if (activeDashId.value === d.id) {
          activeDashId.value = dashList.value[0]?.id || ''
          if (activeDashId.value) {
            await loadCurrentDashboard()
          } else {
            widgets.value = []
          }
        }
        ElMessage.success('已删除')
      } catch (e: any) {
        ElMessage.error('删除失败: ' + (extractErrorDetail(e)))
      }
    }).catch(() => {})
  }
}

async function submitDashDialog() {
  const name = dashNameInput.value.trim()
  if (!name) {
    ElMessage.warning('请输入看板名称')
    return
  }
  dashDialogLoading.value = true
  try {
    if (dashDialogMode.value === 'create') {
      const { data } = await dashboard.create({ name })
      dashList.value.unshift(data)
      activeDashId.value = data.id
      widgets.value = []
      ElMessage.success('已创建')
    } else {
      await dashboard.update(renameTargetId.value, { name })
      const target = dashList.value.find(x => x.id === renameTargetId.value)
      if (target) target.name = name
      ElMessage.success('已重命名')
    }
    showDashDialog.value = false
  } catch (e: any) {
    ElMessage.error('操作失败: ' + (extractErrorDetail(e)))
  } finally {
    dashDialogLoading.value = false
  }
}

// ── Widget 操作 ──

async function deleteWidget(w: DashboardWidget) {
  try {
    await ElMessageBox.confirm(`确定删除组件「${w.question}」?`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await dashboard.deleteWidget(activeDashId.value, w.id)
    widgets.value = widgets.value.filter(x => x.id !== w.id)
    if (chartInstances[w.id]) {
      chartInstances[w.id].dispose()
      delete chartInstances[w.id]
    }
    await nextTick()
    initGrid()
    ElMessage.success('已删除')
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (extractErrorDetail(e)))
  }
}

// 编辑模式切换时重新渲染 grid (显示/隐藏删除按钮)
watch(editMode, () => {
  if (grid && widgets.value.length) {
    nextTick(() => initGrid())
  }
})

// 窗口 resize 处理 — 遍历所有 ECharts 实例调用 resize()
function handleResize() {
  for (const chart of Object.values(chartInstances)) {
    if (!chart.isDisposed()) chart.resize()
  }
}

onMounted(() => {
  loadDashList()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  // 释放 ECharts 实例 + GridStack, 防止内存泄漏
  Object.values(chartInstances).forEach(c => { try { c.dispose() } catch { /* ignore */ } })
  Object.keys(chartInstances).forEach(k => delete chartInstances[k])
  if (grid) {
    try { grid.destroy(false) } catch { /* ignore */ }
    grid = null
  }
  window.removeEventListener('resize', handleResize)
})
</script>

<style scoped>
.dash-view { padding: 24px; }
.page-header { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; }
.title { font-size: 1.3rem; font-weight: bold; flex: 1; margin-left: 8px; }

/* 看板 Tab 列表 */
.dash-tabs {
  display: flex; gap: 4px; margin-bottom: 20px;
  border-bottom: 2px solid #ebeef5; overflow-x: auto;
}
.dash-tab {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 16px; cursor: pointer;
  border-bottom: 2px solid transparent; margin-bottom: -2px;
  transition: all 0.2s; white-space: nowrap;
  color: #606266; font-size: 0.9rem;
}
.dash-tab:hover { color: #667eea; background: #f5f7ff; border-radius: 6px 6px 0 0; }
.dash-tab.active { color: #667eea; font-weight: 600; border-bottom-color: #667eea; }
.tab-name { max-width: 120px; overflow: hidden; text-overflow: ellipsis; }
.tab-more { font-size: 14px; color: #c0c4cc; cursor: pointer; }
.tab-more:hover { color: #909399; }

/* gridstack 容器 */
.grid-container { width: 100%; }
:deep(.grid-stack) { background: #f5f7fa; border-radius: 8px; padding: 4px; }
:deep(.grid-stack-item-content) {
  background: #fff; border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow: hidden;
}

/* widget 卡片 (动态 innerHTML 创建, 用 :deep 穿透 scoped) */
:deep(.widget-card) { display: flex; flex-direction: column; height: 100%; padding: 8px 12px; box-sizing: border-box; }
:deep(.widget-header) {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 6px; flex-shrink: 0; gap: 8px;
}
:deep(.widget-title) {
  font-size: 0.85rem; font-weight: 600; color: #303133;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0;
}
:deep(.widget-actions) { display: flex; gap: 4px; flex-shrink: 0; }
:deep(.widget-btn) {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; border: none; cursor: pointer;
  color: #909399; background: transparent; border-radius: 6px;
  transition: all 0.2s; padding: 0; flex-shrink: 0;
}
:deep(.widget-btn svg) { display: block; }
:deep(.widget-btn:hover) { color: #667eea; background: #f0f2ff; }
:deep(.delete-btn:hover) { color: #f56c6c; background: #fef0f0; }
:deep(.widget-btn.refresh-btn.spinning svg) { animation: spin 0.8s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

:deep(.widget-body) { flex: 1; min-height: 0; overflow: auto; position: relative; }
:deep(.widget-loading), :deep(.widget-empty) {
  display: flex; align-items: center; justify-content: center;
  height: 100%; color: #909399; font-size: 0.8rem;
}
:deep(.widget-error) {
  display: flex; align-items: center; justify-content: center;
  height: 100%; color: #f56c6c; font-size: 0.8rem; text-align: center; padding: 8px;
}

/* 表格 */
:deep(.widget-table-wrap) { font-size: 0.75rem; }
:deep(.widget-table) { width: 100%; border-collapse: collapse; }
:deep(.widget-table th), :deep(.widget-table td) {
  border: 1px solid #ebeef5; padding: 3px 6px; text-align: left;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 120px;
}
:deep(.widget-table th) { background: #f5f7fa; font-weight: 600; color: #606266; }
:deep(.widget-table tbody tr:nth-child(even)) { background: #fafafa; }
:deep(.table-more) { text-align: center; font-size: 0.7rem; color: #909399; padding: 4px 0; }

:deep(.widget-footer) {
  font-size: 0.68rem; color: #c0c4cc; text-align: right;
  margin-top: 4px; flex-shrink: 0;
}
:deep(.widget-chart) { width: 100%; height: 100%; min-height: 200px; }
</style>
