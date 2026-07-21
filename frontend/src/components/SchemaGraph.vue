<template>
  <div class="schema-graph-container">
    <!-- 加载状态 -->
    <div v-if="loading" class="graph-loading">
      <el-icon class="is-loading" :size="24"><Loading /></el-icon>
      <span>图谱加载中...</span>
    </div>

    <!-- 空状态 -->
    <div v-else-if="isEmpty" class="graph-empty">
      <el-empty description="暂无图谱数据，请先完成数据源扫描" :image-size="80" />
    </div>

    <!-- 图谱画布 (始终在 DOM 中, 保证 G6 渲染尺寸正确) -->
    <div ref="graphRef" class="graph-canvas" :style="{ visibility: loading || isEmpty ? 'hidden' : 'visible' }" />

    <!-- 工具栏 -->
    <div v-if="!loading && !isEmpty" class="graph-toolbar">
      <!-- 搜索框 -->
      <el-input
        v-model="searchQuery"
        placeholder="搜索表名..."
        size="small"
        clearable
        style="width: 140px; margin-right: 8px"
        @keyup.enter="handleSearch"
        @clear="handleSearchClear"
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>

      <el-button-group>
        <el-button size="small" @click="zoomIn" title="放大">
          <el-icon><ZoomIn /></el-icon>
        </el-button>
        <el-button size="small" @click="zoomOut" title="缩小">
          <el-icon><ZoomOut /></el-icon>
        </el-button>
        <el-button size="small" @click="fitView" title="适应画布">
          <el-icon><FullScreen /></el-icon>
        </el-button>
      </el-button-group>

      <!-- 模式切换: 移动 / 连线 -->
      <el-button-group style="margin-left: 8px">
        <el-button
          size="small"
          :type="interactionMode === 'move' ? 'primary' : 'default'"
          @click="setMode('move')"
          title="移动模式: 拖拽节点"
        >
          <el-icon><Rank /></el-icon> 移动
        </el-button>
        <el-button
          size="small"
          :type="interactionMode === 'connect' ? 'primary' : 'default'"
          @click="setMode('connect')"
          title="连线模式: 从节点拖拽到另一节点创建关系"
        >
          <el-icon><Connection /></el-icon> 连线
        </el-button>
      </el-button-group>

      <el-button size="small" type="primary" @click="showAddDialog" style="margin-left: 8px">
        <el-icon><Plus /></el-icon> 新增关系
      </el-button>
    </div>

    <!-- 节点详情面板 -->
    <div v-if="selectedNode" class="node-detail-panel">
      <div class="panel-header">
        <span>{{ selectedNode.label }}</span>
        <el-icon class="close-btn" @click="selectedNode = null"><Close /></el-icon>
      </div>
      <div class="panel-body">
        <div class="detail-item">
          <span class="label">表名:</span>
          <span class="value">{{ selectedNode.id }}</span>
        </div>
        <div class="detail-item">
          <span class="label">列数:</span>
          <span class="value">{{ selectedNode.columnCount }}</span>
        </div>
        <div class="detail-item">
          <span class="label">来源:</span>
          <span class="value">{{ selectedNode.source }}</span>
        </div>
        <div class="detail-item">
          <span class="label">中心度:</span>
          <span class="value">{{ selectedNode.centrality.toFixed(4) }}</span>
        </div>
        <div class="detail-item">
          <span class="label">连接数:</span>
          <span class="value">{{ selectedNode.degree }}</span>
        </div>
        <div class="detail-item">
          <span class="label">社区:</span>
          <span class="value">
            <el-tag size="small" :color="getCommunityColor(selectedNode.community)" style="color: #fff; border: none">
              #{{ selectedNode.community + 1 }}
            </el-tag>
          </span>
        </div>
      </div>
    </div>

    <!-- 新增关系对话框 -->
    <el-dialog v-model="addDialogVisible" title="新增关系" width="560px" @close="resetAddForm">
      <el-form :model="addForm" :rules="addFormRules" ref="addFormRef" label-width="80px">
        <el-form-item label="源表" prop="from_table">
          <el-select v-model="addForm.from_table" placeholder="选择源表" filterable @change="onTableChange">
            <el-option v-for="n in graphNodes" :key="n.id" :label="n.label + ' (' + n.id + ')'" :value="n.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标表" prop="target_model">
          <el-select v-model="addForm.target_model" placeholder="选择目标表" filterable @change="onTableChange">
            <el-option v-for="n in graphNodes" :key="n.id" :label="n.label + ' (' + n.id + ')'" :value="n.id" />
          </el-select>
        </el-form-item>

        <!-- ON 条件: 下拉框选择, 支持多个 -->
        <el-form-item label="ON 条件" prop="on_conditions" :error="onConditionsError">
          <div v-if="addForm.from_table && addForm.target_model && fromColumns.length > 0" class="on-conditions">
            <div v-for="(oc, idx) in addForm.on_conditions" :key="idx" class="on-condition-row">
              <el-select v-model="oc.source_column" placeholder="源列" filterable size="small" style="width: 40%">
                <el-option v-for="c in fromColumns" :key="c.name" :label="c.label" :value="c.name" />
              </el-select>
              <span class="on-eq">=</span>
              <el-select v-model="oc.target_column" placeholder="目标列" filterable size="small" style="width: 40%">
                <el-option v-for="c in toColumns" :key="c.name" :label="c.label" :value="c.name" />
              </el-select>
              <el-button v-if="addForm.on_conditions.length > 1" size="small" type="danger" plain :icon="Delete" @click="removeOnCondition(idx)" style="margin-left: 4px" />
            </div>
            <el-button size="small" type="primary" plain :icon="Plus" @click="addOnCondition" style="margin-top: 4px">添加条件</el-button>
          </div>
          <div v-else-if="addForm.from_table && addForm.target_model" style="color: #909399; font-size: 0.85rem">
            加载列信息中...
          </div>
          <div v-else style="color: #909399; font-size: 0.85rem">
            请先选择源表和目标表
          </div>
        </el-form-item>

        <el-form-item label="JOIN 类型">
          <el-select v-model="addForm.join_type">
            <el-option label="LEFT JOIN" value="LEFT" />
            <el-option label="INNER JOIN" value="INNER" />
            <el-option label="RIGHT JOIN" value="RIGHT" />
            <el-option label="FULL JOIN" value="FULL" />
          </el-select>
        </el-form-item>
        <el-form-item label="基数">
          <el-select v-model="addForm.type">
            <el-option label="N:1 (多对一)" value="N:1" />
            <el-option label="1:N (一对多)" value="1:N" />
            <el-option label="1:1 (一对一)" value="1:1" />
            <el-option label="N:N (多对多)" value="N:N" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleAddRelationship" :loading="addLoading">确认</el-button>
      </template>
    </el-dialog>

    <!-- 删除确认 -->
    <el-dialog v-model="deleteDialogVisible" title="确认删除" width="360px">
      <p>确定删除关系 <strong>{{ deleteTarget?.from }}</strong> → <strong>{{ deleteTarget?.to }}</strong> 吗？</p>
      <template #footer>
        <el-button @click="deleteDialogVisible = false">取消</el-button>
        <el-button type="danger" @click="handleDeleteRelationship" :loading="deleteLoading">删除</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading, ZoomIn, ZoomOut, FullScreen, Plus, Close, Search, Rank, Connection } from '@element-plus/icons-vue'
import { graph, type GraphData, type GraphNode, type TableColumn } from '@/api'
import {
  getCommunityColor,
  getCommunityGlow,
  getNodeSize,
  getEdgeColor,
  D3_FORCE_LAYOUT,
  NODE_GLOW_THRESHOLD,
  EDGE_FLOW_ANIMATION,
  EDGE_FLOW_THRESHOLD,
} from '@/utils/g6-config'

// ── Props ──────────────────────────────────────────────────

const props = defineProps<{
  dataSourceId: string
}>()

const emit = defineEmits<{
  (e: 'dataChanged'): void
}>()

// ── State ──────────────────────────────────────────────────

const graphRef = ref<HTMLDivElement>()
const loading = ref(false)
const graphData = ref<GraphData>({ nodes: [], edges: [] })
const selectedNode = ref<GraphNode | null>(null)
let g6Instance: any = null

// 交互模式: move=拖拽移动, connect=拖拽连线
const interactionMode = ref<'move' | 'connect'>('move')

// 搜索
const searchQuery = ref('')

// 边流动动画定时器
let flowAnimTimer: ReturnType<typeof setInterval> | null = null
let flowOffset = 0
// 缓存需要流动动画的边 ID (避免每帧遍历所有边)
let flowEdgeIds: string[] = []

// 新增关系
const addDialogVisible = ref(false)
const addLoading = ref(false)
const addFormRef = ref()
const addForm = ref({
  from_table: '',
  target_model: '',
  on_conditions: [{ source_column: '', target_column: '' }] as { source_column: string; target_column: string }[],
  join_type: 'LEFT',
  type: 'N:1',
})
const addFormRules = {
  from_table: [{ required: true, message: '请选择源表', trigger: 'change' }],
  target_model: [{ required: true, message: '请选择目标表', trigger: 'change' }],
}
const onConditionsError = ref('')

// 列信息缓存
const fromColumns = ref<TableColumn[]>([])
const toColumns = ref<TableColumn[]>([])
const columnsLoading = ref(false)

// 删除关系
const deleteDialogVisible = ref(false)
const deleteLoading = ref(false)
const deleteTarget = ref<{ from: string; to: string } | null>(null)

// ── Computed ───────────────────────────────────────────────

const isEmpty = computed(() => graphData.value.nodes.length === 0)
const graphNodes = computed(() => graphData.value.nodes)

// ── G6 初始化 ──────────────────────────────────────────────

async function initG6() {
  if (!graphRef.value) return

  // 动态导入 G6 (懒加载, ~500KB)
  const G6 = await import('@antv/g6')

  const container = graphRef.value
  const width = container.offsetWidth
  const height = container.offsetHeight

  // 构建行为列表 (根据当前模式)
  const behaviors = buildBehaviors()

  g6Instance = new G6.Graph({
    container,
    width,
    height,
    data: transformData(graphData.value),
    animation: false,
    node: {
      style: {
        size: (d: any) => getNodeSize(d.data?.centrality || 0),
        fill: (d: any) => getCommunityColor(d.data?.community || 0),
        stroke: '#fff',
        lineWidth: 2,
        labelText: (d: any) => d.data?.label || d.id,
        labelFontSize: 12,
        labelFill: '#333',
        labelPlacement: 'bottom',
        labelOffsetY: 6,
        cursor: 'pointer',
        // 枢纽节点光晕
        shadowColor: (d: any) => {
          if ((d.data?.centrality || 0) > NODE_GLOW_THRESHOLD) {
            return getCommunityGlow(d.data?.community || 0)
          }
          return 'transparent'
        },
        shadowBlur: (d: any) => {
          return (d.data?.centrality || 0) > NODE_GLOW_THRESHOLD ? 15 : 0
        },
      },
      state: {
        selected: {
          stroke: '#1890ff',
          lineWidth: 3,
          shadowColor: 'rgba(24,144,255,0.6)',
          shadowBlur: 20,
        },
        highlight: {
          stroke: '#D580FF',
          lineWidth: 3,
          shadowColor: 'rgba(213,128,255,0.5)',
          shadowBlur: 15,
        },
        dim: {
          fillOpacity: 0.3,
          strokeOpacity: 0.3,
          labelOpacity: 0.3,
        },
      },
    },
    edge: {
      type: 'quadratic',
      style: {
        stroke: (d: any) => getEdgeColor(d.data?.relSource || 'manual', d.data?.confidence || 0),
        lineWidth: 1.5,
        endArrow: true,
        endArrowSize: 6,
        cursor: 'pointer',
        // 边标签: JOIN 类型
        labelText: (d: any) => d.data?.joinType || '',
        labelFontSize: 9,
        labelFill: '#999',
        labelBackground: true,
        labelBackgroundFill: '#fff',
        labelBackgroundOpacity: 0.85,
        labelBackgroundRadius: 2,
        labelBackgroundPadding: [2, 4, 2, 4],
        // 高 confidence 边: 流动虚线
        lineDash: (d: any) => {
          if ((d.data?.confidence || 0) >= EDGE_FLOW_THRESHOLD) {
            return EDGE_FLOW_ANIMATION.lineDash
          }
          return undefined
        },
      },
      state: {
        selected: {
          stroke: '#1890ff',
          lineWidth: 2.5,
          shadowColor: 'rgba(24,144,255,0.4)',
          shadowBlur: 8,
        },
        highlight: {
          stroke: '#D580FF',
          lineWidth: 2,
        },
        dim: {
          strokeOpacity: 0.15,
          labelOpacity: 0.15,
        },
      },
    },
    layout: D3_FORCE_LAYOUT,
    plugins: [
      {
        type: 'minimap',
        size: [160, 100],
        position: 'right-bottom',
      },
      {
        type: 'tooltip',
        getContent: (_e: any, items: any[]) => {
          if (!items || items.length === 0) return '<div></div>'
          const item = items[0]
          const data = item.data || {}

          // 边 tooltip (有 source/target 且 data.on 存在)
          if (item.source !== undefined && item.target !== undefined && data.on !== undefined) {
            const confColor = getEdgeColor(data.relSource || 'manual', data.confidence ?? 0)
            return `<div style="background:rgba(0,0,0,0.75);color:#fff;padding:8px 12px;border-radius:4px;font-size:12px;line-height:1.6;max-width:320px;word-break:break-all">
                <div><b>${item.source} → ${item.target}</b></div>
                <div>ON: ${data.on || '-'}</div>
                <div>JOIN: ${data.joinType || 'LEFT'}</div>
                <div>基数: ${data.cardinality || 'N:1'}</div>
                <div>来源: <span style="color:${confColor}">${data.relSource || 'manual'}</span></div>
                <div>置信度: ${data.confidence ?? '-'}</div>
              </div>`
          }
          // 节点 tooltip
          if (data.label !== undefined || item.id) {
            const communityColor = getCommunityColor(data.community || 0)
            return `<div style="background:rgba(0,0,0,0.75);color:#fff;padding:8px 12px;border-radius:4px;font-size:12px;line-height:1.6;max-width:320px;word-break:break-all">
                <div><b>${data.label || item.id}</b></div>
                <div>表名: ${item.id}</div>
                <div>列数: ${data.columnCount ?? '-'}</div>
                <div>中心度: ${data.centrality?.toFixed(4) ?? '-'}</div>
                <div>连接数: ${data.degree ?? '-'}</div>
                <div>社区: <span style="color:${communityColor}">●</span> #${(data.community ?? 0) + 1}</div>
              </div>`
          }
          return '<div></div>'
        },
      },
    ],
    behaviors,
  })

  // 右键菜单: 删除关系
  g6Instance.on('edge:contextmenu', (e: any) => {
    e.preventDefault?.()
    const edgeId = e.target?.id
    if (edgeId) {
      const edgeData = g6Instance.getEdgeData(edgeId)
      if (edgeData) {
        deleteTarget.value = { from: edgeData.source, to: edgeData.target }
        deleteDialogVisible.value = true
      }
    }
  })

  // 双击节点: 聚焦 2-hop 子图
  g6Instance.on('node:dblclick', (e: any) => {
    const nodeId = e.target?.id
    if (nodeId) {
      focusSubgraph(nodeId)
    }
  })

  // hover-activate: 官方 config-params 示例配置 (dim 用 fill 改色, 不用 opacity)
  // G6 v5 的 opacity 在 state 中可能不触发视觉重绘, 改用 fill 改色 (官方验证可行)

  await g6Instance.render()

  // 渲染后确保画布尺寸正确
  if (graphRef.value) {
    g6Instance.resize(graphRef.value.offsetWidth, graphRef.value.offsetHeight)
  }

  // fitView 后如果缩放太小, 放大到合理比例 + 居中
  await g6Instance.fitView()
  const zoom = g6Instance.getZoom()
  if (zoom < 0.8) {
    g6Instance.zoomTo(0.8)
    await g6Instance.fitCenter()
  }

  // 启动边流动动画
  startFlowAnimation()
}

/** 构建行为列表 (根据交互模式) */
function buildBehaviors() {
  // G6 v5 click-select: 只用 selected state
  const clickSelect = {
    type: 'click-select',
    key: 'click-select',
    multiple: false,
    onClick: (event: any) => {
      const nodeId = event.target?.id
      if (nodeId) {
        const nodeData = g6Instance.getNodeData(nodeId)
        if (nodeData) {
          const d = nodeData.data || {}
          selectedNode.value = {
            id: nodeData.id,
            label: d.label || nodeData.id,
            community: d.community ?? 0,
            centrality: d.centrality ?? 0,
            columnCount: d.columnCount ?? 0,
            source: d.source ?? 'manual',
            degree: d.degree ?? 0,
          }
        }
      } else {
        selectedNode.value = null
      }
    },
  }

  // hover-activate: 官方 config-params 示例配置
  // enable 只对 node 生效 (避免 edge/combo 干扰)
  // state='highlight' 高亮邻居, inactiveState='dim' dim 非邻居
  const hoverActivate = {
    type: 'hover-activate',
    key: 'hover-activate',
    enable: (event: any) => event.targetType === 'node',
    degree: 1,
    state: 'highlight',
    inactiveState: 'dim',
  }

  if (interactionMode.value === 'connect') {
    return [
      { type: 'drag-canvas', key: 'drag-canvas' },
      { type: 'zoom-canvas', key: 'zoom-canvas' },
      {
        type: 'create-edge',
        key: 'create-edge',
        trigger: 'drag',
        onCreate: (edgeData: any) => {
          const { source, target } = edgeData
          if (source && target && source !== target) {
            addForm.value = {
              from_table: source,
              target_model: target,
              on: '',
              join_type: 'LEFT',
              type: 'N:1',
            }
            addDialogVisible.value = true
          }
          return undefined
        },
      },
      clickSelect,
      hoverActivate,
    ]
  }

  // 移动模式
  return [
    { type: 'drag-canvas', key: 'drag-canvas' },
    { type: 'zoom-canvas', key: 'zoom-canvas' },
    { type: 'drag-element', key: 'drag-element' },
    clickSelect,
    hoverActivate,
  ]
}

/** 切换交互模式 (动态替换行为, 不销毁 G6 实例) */
async function setMode(mode: 'move' | 'connect') {
  if (interactionMode.value === mode) return
  interactionMode.value = mode

  if (!g6Instance) return

  // G6 v5 支持 setBehaviors 动态替换行为列表, 无需销毁重建
  const behaviors = buildBehaviors()
  g6Instance.setBehaviors(behaviors)

  // 连线模式下光标改为 crosshair
  if (graphRef.value) {
    graphRef.value.style.cursor = mode === 'connect' ? 'crosshair' : 'default'
  }
}

/** 缓存需要流动动画的边 ID (渲染/数据更新后调用) */
function cacheFlowEdges() {
  flowEdgeIds = []
  if (!g6Instance) return
  try {
    const edges = g6Instance.getEdgeData() as any[]
    for (const edge of edges) {
      const data = edge.data || {}
      if ((data.confidence || 0) >= EDGE_FLOW_THRESHOLD) {
        flowEdgeIds.push(edge.id)
      }
    }
  } catch {
    // 静默
  }
}

/** 启动边流动动画 (lineDashOffset 递增) */
function startFlowAnimation() {
  if (flowAnimTimer) return
  cacheFlowEdges()
  if (flowEdgeIds.length === 0) return  // 无流动边, 不启动定时器
  flowAnimTimer = setInterval(() => {
    if (!g6Instance || flowEdgeIds.length === 0) return
    flowOffset += EDGE_FLOW_ANIMATION.lineDashOffset
    try {
      const updates = flowEdgeIds.map(id => ({
        id,
        style: { lineDashOffset: -flowOffset },
      }))
      g6Instance.updateEdgeData(updates)
    } catch {
      // 动画失败不影响主功能, 静默忽略
    }
  }, 200)
}

/** 停止边流动动画 */
function stopFlowAnimation() {
  if (flowAnimTimer) {
    clearInterval(flowAnimTimer)
    flowAnimTimer = null
  }
}

/** 转换数据为 G6 格式 */
function transformData(data: GraphData) {
  return {
    nodes: data.nodes.map(n => ({
      id: n.id,
      data: {
        label: n.label,
        community: n.community,
        centrality: n.centrality,
        columnCount: n.columnCount,
        source: n.source,
        degree: n.degree,
      },
    })),
    edges: data.edges.map((e, i) => ({
      id: `edge-${i}`,
      source: e.source,
      target: e.target,
      data: {
        on: e.on,
        confidence: e.confidence,
        relSource: e.relSource,
        joinType: e.joinType,
        cardinality: e.cardinality,
      },
    })),
  }
}

/** 搜索定位: 输入表名后聚焦到该节点 */
function handleSearch() {
  if (!g6Instance || !searchQuery.value.trim()) return
  const query = searchQuery.value.trim().toLowerCase()
  const node = graphData.value.nodes.find(
    n => n.id.toLowerCase().includes(query) || n.label.toLowerCase().includes(query)
  )
  if (!node) {
    ElMessage.warning(`未找到匹配的表: ${searchQuery.value}`)
    return
  }
  // 选中该节点 (click-select behavior 会自动处理 selected state)
  g6Instance.setElementState(node.id, ['selected'])
  g6Instance.focusElement(node.id)
  selectedNode.value = node
}

/** 搜索清空: 取消选中 */
function handleSearchClear() {
  if (!g6Instance) return
  const nodes = g6Instance.getNodeData() as any[]
  nodes.forEach((node: any) => {
    const current = g6Instance.getElementState(node.id) as string[]
    const kept = current.filter((s: string) => s !== 'selected')
    g6Instance.setElementState(node.id, kept)
  })
  const edges = g6Instance.getEdgeData() as any[]
  edges.forEach((edge: any) => {
    const current = g6Instance.getElementState(edge.id) as string[]
    const kept = current.filter((s: string) => s !== 'selected')
    g6Instance.setElementState(edge.id, kept)
  })
  selectedNode.value = null
}

/** 双击聚焦: 加载 2-hop 子图 */
async function focusSubgraph(center: string) {
  if (!props.dataSourceId) return
  try {
    const { data } = await graph.subgraph(props.dataSourceId, center, 2)
    if (data.nodes.length > 0) {
      g6Instance.setData(transformData(data))
      await g6Instance.render()
      await g6Instance.fitView()
      // 子图数据更新后刷新流动边缓存
      cacheFlowEdges()
      g6Instance.setElementState(center, ['selected'])
      const neighbors = g6Instance.getNeighborNodesData(center) || []
      neighbors.forEach((n: any) => {
        g6Instance.setElementState(n.id, ['selected'])
      })
    }
  } catch (err: any) {
    ElMessage.error('加载子图失败: ' + (err.message || err))
  }
}

// ── 数据加载 ───────────────────────────────────────────────

async function loadGraphData() {
  if (!props.dataSourceId) return
  loading.value = true
  try {
    const { data } = await graph.full(props.dataSourceId)
    graphData.value = data
    // 先关闭 loading, 让容器变为 visible, 再初始化/更新 G6
    loading.value = false
    await nextTick()
    if (g6Instance) {
      g6Instance.setData(transformData(data))
      await g6Instance.render()
      if (graphRef.value) {
        g6Instance.resize(graphRef.value.offsetWidth, graphRef.value.offsetHeight)
      }
      await g6Instance.fitView()
      const zoom = g6Instance.getZoom()
      if (zoom < 0.8) {
        g6Instance.zoomTo(0.8)
        await g6Instance.fitCenter()
      }
      // 数据更新后重新缓存流动边
      cacheFlowEdges()
    } else {
      await initG6()
    }
  } catch (err: any) {
    ElMessage.error('加载图谱数据失败: ' + (err.message || err))
    graphData.value = { nodes: [], edges: [] }
    loading.value = false
  }
}

// ── 工具栏操作 ─────────────────────────────────────────────

function zoomIn() {
  g6Instance?.zoomBy(1.2)
}

function zoomOut() {
  g6Instance?.zoomBy(0.8)
}

function fitView() {
  g6Instance?.fitView()
}

// ── 新增关系 ───────────────────────────────────────────────

function showAddDialog() {
  addForm.value = {
    from_table: '',
    target_model: '',
    on_conditions: [{ source_column: '', target_column: '' }],
    join_type: 'LEFT',
    type: 'N:1',
  }
  fromColumns.value = []
  toColumns.value = []
  onConditionsError.value = ''
  addDialogVisible.value = true
}

function resetAddForm() {
  addFormRef.value?.resetFields()
  fromColumns.value = []
  toColumns.value = []
  onConditionsError.value = ''
}

// ── ON 条件管理 ──────────────────────────────────────────────

async function onTableChange() {
  // 两表都选中时加载列信息
  fromColumns.value = []
  toColumns.value = []
  const f = addForm.value
  if (!f.from_table || !f.target_model) return

  columnsLoading.value = true
  try {
    const [fromResp, toResp] = await Promise.all([
      graph.tableColumns(props.dataSourceId, f.from_table),
      graph.tableColumns(props.dataSourceId, f.target_model),
    ])
    fromColumns.value = fromResp.data.columns
    toColumns.value = toResp.data.columns

    // 智能匹配: 如果两表有同名列 (如 id, xxx_id), 自动填充
    if (f.on_conditions.length === 1 && !f.on_conditions[0].source_column) {
      for (const fc of fromColumns.value) {
        for (const tc of toColumns.value) {
          if (fc.name === tc.name || fc.name === `${f.target_model.replace(/^[a-z]+_/, '')}_id` || tc.name === `${f.from_table.replace(/^[a-z]+_/, '')}_id`) {
            f.on_conditions[0] = { source_column: fc.name, target_column: tc.name }
            break
          }
        }
        if (f.on_conditions[0].source_column) break
      }
    }
  } catch {
    ElMessage.warning('加载列信息失败, 请手动输入 ON 条件')
  } finally {
    columnsLoading.value = false
  }
}

function addOnCondition() {
  addForm.value.on_conditions.push({ source_column: '', target_column: '' })
}

function removeOnCondition(idx: number) {
  addForm.value.on_conditions.splice(idx, 1)
}

async function handleAddRelationship() {
  const valid = await addFormRef.value?.validate().catch(() => false)
  if (!valid) return

  // 校验 ON 条件
  const f = addForm.value
  const filledConditions = f.on_conditions.filter(oc => oc.source_column && oc.target_column)
  if (filledConditions.length === 0) {
    onConditionsError.value = '至少填写一个 ON 条件'
    return
  }
  onConditionsError.value = ''

  addLoading.value = true
  try {
    await graph.addRelationship(props.dataSourceId, {
      from_table: f.from_table,
      name: `${f.from_table}_to_${f.target_model}`,
      target_model: f.target_model,
      on_conditions: filledConditions,
      join_type: f.join_type,
      type: f.type,
      source: 'manual',
      confidence: 1.0,
    })
    ElMessage.success('关系添加成功')
    addDialogVisible.value = false
    await loadGraphData()
    // 关系变更后通知父组件刷新反向关系计数
    emit('dataChanged')
  } catch (err: any) {
    ElMessage.error('添加关系失败: ' + (err.response?.data?.detail || err.message || err))
  } finally {
    addLoading.value = false
  }
}

// ── 删除关系 ───────────────────────────────────────────────

async function handleDeleteRelationship() {
  if (!deleteTarget.value) return
  deleteLoading.value = true
  try {
    await graph.deleteRelationship(props.dataSourceId, deleteTarget.value.from, deleteTarget.value.to)
    ElMessage.success('关系删除成功')
    deleteDialogVisible.value = false
    deleteTarget.value = null
    await loadGraphData()
    // 关系变更后通知父组件刷新反向关系计数
    emit('dataChanged')
  } catch (err: any) {
    ElMessage.error('删除关系失败: ' + (err.response?.data?.detail || err.message || err))
  } finally {
    deleteLoading.value = false
  }
}

// ── 生命周期 ───────────────────────────────────────────────

onMounted(() => {
  loadGraphData()
})

onBeforeUnmount(() => {
  stopFlowAnimation()
  if (g6Instance) {
    g6Instance.destroy()
    g6Instance = null
  }
})

watch(() => props.dataSourceId, () => {
  if (g6Instance) {
    g6Instance.destroy()
    g6Instance = null
    stopFlowAnimation()
  }
  selectedNode.value = null
  loadGraphData()
})

// ── 暴露给父组件的方法 ──────────────────────────────────────

function focusNode(nodeId: string) {
  if (!g6Instance) return
  // 选中该节点 (click-select behavior 自动处理)
  g6Instance.setElementState(nodeId, ['selected'])
  g6Instance.focusElement(nodeId)
  // 更新详情面板
  const nodeData = g6Instance.getNodeData(nodeId)
  if (nodeData) {
    const d = nodeData.data || {}
    selectedNode.value = {
      id: nodeData.id,
      label: d.label || nodeData.id,
      community: d.community ?? 0,
      centrality: d.centrality ?? 0,
      columnCount: d.columnCount ?? 0,
      source: d.source ?? 'manual',
      degree: d.degree ?? 0,
    }
  }
}

defineExpose({ focusNode })

// 窗口 resize
let resizeObserver: ResizeObserver | null = null
onMounted(() => {
  if (graphRef.value) {
    resizeObserver = new ResizeObserver(() => {
      if (g6Instance && graphRef.value) {
        g6Instance.resize(graphRef.value.offsetWidth, graphRef.value.offsetHeight)
      }
    })
    resizeObserver.observe(graphRef.value)
  }
})
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
})
</script>

<style scoped>
.schema-graph-container {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 400px;
}

.graph-canvas {
  width: 100%;
  height: 100%;
  min-height: 400px;
  background: #fafafa;
  border-radius: 4px;
}

.graph-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 400px;
  color: #909399;
  gap: 8px;
}

.graph-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 400px;
}

.graph-toolbar {
  position: absolute;
  top: 12px;
  left: 12px;
  display: flex;
  align-items: center;
  z-index: 10;
  background: rgba(255, 255, 255, 0.9);
  padding: 6px 10px;
  border-radius: 6px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  backdrop-filter: blur(4px);
}

.node-detail-panel {
  position: absolute;
  top: 12px;
  right: 12px;
  width: 240px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  z-index: 10;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid #e4e7ed;
  font-weight: 600;
  font-size: 14px;
}

.close-btn {
  cursor: pointer;
  color: #909399;
}

.close-btn:hover {
  color: #606266;
}

.panel-body {
  padding: 10px 14px;
}

.detail-item {
  display: flex;
  align-items: center;
  margin-bottom: 6px;
  font-size: 12px;
}

.detail-item .label {
  color: #909399;
  width: 56px;
  flex-shrink: 0;
}

.detail-item .value {
  color: #303133;
}

.on-conditions {
  width: 100%;
}

.on-condition-row {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 6px;
}

.on-eq {
  color: #909399;
  font-weight: bold;
  flex-shrink: 0;
}
</style>
