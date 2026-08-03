<template>
  <!--
    SchemaGraph — 知识图谱可视化组件 (基于 AntV G6 v5)

    架构职责:
      1. 使用 G6.js 渲染数据源的表/关系/指标知识图谱
      2. 提供交互操作: 搜索、缩放、拖拽、连线、新增/删除关系
      3. 展示节点详情面板 (列数、关联数、指标、中心度)
      4. 展示边详情面板 (ON 条件、JOIN 类型、置信度、基数)
      5. 支持新增关系对话框 (选择源表/目标表/ON 条件/JOIN 类型/基数)

    图谱数据流:
      graph.full(dataSourceId) → 获取完整图数据 (nodes + edges)
      → transformData() 转换为 G6 格式
      → G6.Graph 渲染
      → 用户交互 (点击/拖拽/搜索) → 更新详情面板

    组件关系:
      - 被 SemanticView / DataSourceView 等页面调用
      - 通过 props.dataSourceId 接收数据源 ID
      - 通过 emit('dataChanged') 通知父组件关系变更
      - 通过 defineExpose({ focusNode }) 暴露方法给父组件
      - 使用 g6-config.ts 中的配置常量和工具函数
  -->
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

    <!--
      图谱画布 (始终在 DOM 中, 保证 G6 渲染尺寸正确)
      loading 或 isEmpty 时设置 visibility: hidden 而非 v-if,
      因为 G6 需要在 mount 时获取容器尺寸, 如果 DOM 被移除再插入,
      尺寸计算会出错。
    -->
    <div ref="graphRef" class="graph-canvas" :style="{ visibility: loading || isEmpty ? 'hidden' : 'visible' }" />

    <!-- 工具栏: 搜索、缩放、模式切换、新增关系 -->
    <div v-if="!loading && !isEmpty" class="graph-toolbar">
      <!-- 搜索框: 按表名或显示名搜索, 输入后回车定位 -->
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

      <!-- 缩放按钮组 -->
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

    <!-- 详情面板: 节点关系 / 边详情 (悬浮在画布右上角) -->
    <div v-if="selectedNode || selectedEdge" class="detail-panel">
      <!-- 节点详情 -->
      <template v-if="selectedNode">
        <div class="panel-header">
          <span class="panel-title">{{ selectedNode.label }}</span>
          <span class="panel-subtitle">{{ selectedNode.id }}</span>
          <el-icon class="close-btn" @click="selectedNode = null"><Close /></el-icon>
        </div>
        <div class="panel-body">
          <!-- 统计行: 列数、关联数、指标数、中心度 -->
          <div class="stat-row">
            <div class="stat-item"><span class="stat-num">{{ selectedNode.columnCount }}</span><span class="stat-label">列</span></div>
            <div class="stat-item"><span class="stat-num">{{ selectedNode.degree }}</span><span class="stat-label">关联</span></div>
            <div class="stat-item"><span class="stat-num">{{ nodeMetrics.length || selectedNode.metricCount || 0 }}</span><span class="stat-label">指标</span></div>
            <div class="stat-item"><span class="stat-num">{{ selectedNode.centrality.toFixed(2) }}</span><span class="stat-label">中心度</span></div>
          </div>

          <!-- 关系列表: 展示该节点的所有出入边, 点击可聚焦到对应边 -->
          <div class="rel-section" v-if="nodeRelationships.length > 0">
            <div class="section-title">关联关系</div>
            <div v-for="rel in nodeRelationships" :key="rel.source + '-' + rel.target" class="rel-item" @click="focusEdge(rel)">
              <span class="rel-arrow" :class="rel.direction === 'out' ? 'arrow-out' : 'arrow-in'">{{ rel.direction === 'out' ? '→' : '←' }}</span>
              <div class="rel-main">
                <span class="rel-name">{{ getTableLabel(rel.direction === 'out' ? rel.target : rel.source) }}</span>
                <span class="rel-meta">{{ rel.joinType }} · {{ rel.cardinality }}</span>
              </div>
            </div>
          </div>
          <div v-else class="rel-empty">无关联关系</div>

          <!-- 指标列表 (只读, 编辑入口在语义层页面) -->
          <div class="rel-section" v-if="nodeMetrics.length > 0">
            <div class="section-title">业务指标</div>
            <div v-for="m in nodeMetrics" :key="m.name" class="metric-item">
              <span class="metric-icon">ƒ</span>
              <div class="metric-main">
                <span class="metric-name">{{ m.display_name }}<span v-if="m.source === 'rule_inferred'" class="metric-source rule">⚙️</span><span v-else-if="m.source === 'auto_inferred' || m.source === 'ai_inferred'" class="metric-source ai">🤖</span></span>
                <span class="metric-meta">{{ m.formula }}<template v-if="m.condition"> WHERE {{ m.condition }}</template></span>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- 边详情 -->
      <template v-if="selectedEdge">
        <div class="panel-header">
          <span class="panel-title">关系详情</span>
          <el-icon class="close-btn" @click="selectedEdge = null"><Close /></el-icon>
        </div>
        <div class="panel-body">
          <!-- 边流向: 源表 → JOIN 类型 → 目标表 -->
          <div class="edge-flow">
            <span class="edge-table">{{ getTableLabel(selectedEdge.source) }}</span>
            <span class="edge-join">{{ selectedEdge.joinType }} JOIN</span>
            <span class="edge-table">{{ getTableLabel(selectedEdge.target) }}</span>
          </div>

          <!-- 属性标签: 基数、来源、置信度 -->
          <div class="edge-tags">
            <el-tag size="small" type="info">{{ selectedEdge.cardinality }}</el-tag>
            <el-tag size="small" :type="selectedEdge.relSource === 'manual' ? 'success' : 'warning'">{{ selectedEdge.relSource }}</el-tag>
            <el-tag size="small" v-if="selectedEdge.confidence < 1" type="warning">{{ (selectedEdge.confidence * 100).toFixed(0) }}%</el-tag>
          </div>

          <!-- ON 条件: 解析为可读的 sourceCol = targetCol 格式 -->
          <div class="on-section">
            <div class="section-title">ON 条件</div>
            <div v-for="(cond, idx) in parseOnConditions(selectedEdge.on)" :key="idx" class="on-row">
              <span class="on-col">{{ cond.sourceTable }}.<b>{{ cond.sourceCol }}</b></span>
              <span class="on-eq">=</span>
              <span class="on-col">{{ cond.targetTable }}.<b>{{ cond.targetCol }}</b></span>
            </div>
            <div v-if="parseOnConditions(selectedEdge.on).length === 0" class="on-raw">{{ selectedEdge.on }}</div>
          </div>

          <!-- 删除此关系按钮 -->
          <el-button size="small" type="danger" plain :icon="Delete" @click="confirmDeleteEdge" style="width: 100%; margin-top: 12px">删除此关系</el-button>
        </div>
      </template>
    </div>

    <!--
      新增关系对话框
      支持: 选择源表/目标表、多条件 ON 条件、JOIN 类型、基数
      选中表后自动加载列信息, 并智能匹配同名列
    -->
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

        <!-- ON 条件: 下拉框选择, 支持多个条件 (AND 连接) -->
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

    <!-- 删除确认对话框 -->
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
/**
 * SchemaGraph 组件脚本 — G6 图初始化、交互操作、数据管理
 *
 * 架构职责:
 *   1. G6 图实例的生命周期管理 (创建/更新/销毁)
 *   2. 图谱数据的加载和转换 (后端 GraphData → G6 格式)
 *   3. 交互行为 (点击选中、悬停高亮、拖拽、搜索、连线)
 *   4. 边流动动画 (高置信度边的虚线动画)
 *   5. 新增/删除关系操作
 *   6. 窗口 Resize 自适应
 *
 * G6 初始化流程:
 *   1. loadGraphData() → 调用 graph.full() 获取数据
 *   2. 首次加载: initG6() → 创建 G6.Graph 实例
 *   3. 后续加载: g6Instance.setData() → 更新已有实例
 *   4. 渲染后调整: fitView → zoomTo(0.8) → fitCenter
 *
 * 交互模式:
 *   - move (默认): 拖拽画布、缩放、选中、悬停高亮
 *   - connect: 从节点拖拽连线创建新关系
 * 模式切换使用 G6 v5 的 setBehaviors API, 无需销毁重建实例。
 */
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading, ZoomIn, ZoomOut, FullScreen, Plus, Close, Search, Rank, Connection, Delete } from '@element-plus/icons-vue'
import { graph, semantic, type GraphData, type GraphNode, type GraphEdge, type TableColumn, type SemanticMetric } from '@/api'
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
  dataSourceId: string    // 当前选中的数据源 ID
}>()

const emit = defineEmits<{
  (e: 'dataChanged'): void   // 关系变更后通知父组件刷新
}>()

// ── State ──────────────────────────────────────────────────

const graphRef = ref<HTMLDivElement>()           // 画布容器 DOM 引用
const loading = ref(false)                        // 加载状态
const graphData = ref<GraphData>({ nodes: [], edges: [] })  // 原始图数据
const selectedNode = ref<GraphNode | null>(null)  // 当前选中的节点
const selectedEdge = ref<GraphEdge | null>(null)  // 当前选中的边
// 选中节点的指标列表 (从语义层加载)
const nodeMetrics = ref<SemanticMetric[]>([])
let g6Instance: any = null                       // G6 实例 (非响应式)

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

// 表名 → 中文名 映射 (用于显示)
const tableLabelMap = computed(() => {
  const map: Record<string, string> = {}
  for (const n of graphData.value.nodes) {
    map[n.id] = n.label
  }
  return map
})

/**
 * 获取表显示名 (label + tableName 格式)
 * 如果节点有 label (中文名), 显示 "中文名 (表名)"
 * 否则只显示表名
 */
function getTableLabel(tableName: string): string {
  const label = tableLabelMap.value[tableName]
  return label ? `${label} (${tableName})` : tableName
}

// 选中节点的所有关联关系 (出入边)
const nodeRelationships = computed(() => {
  if (!selectedNode.value) return []
  const tableId = selectedNode.value.id
  return graphData.value.edges
    .filter(e => e.source === tableId || e.target === tableId)
    .map(e => ({
      ...e,
      direction: e.source === tableId ? 'out' : 'in',  // out=从该表出发, in=指向该表
    }))
    .sort((a, b) => a.direction.localeCompare(b.direction))
})

/**
 * 解析 ON 条件字符串
 *
 * 输入: "a.col1 = b.col1 AND a.col2 = b.col2"
 * 输出: [{sourceTable: "a", sourceCol: "col1", targetTable: "b", targetCol: "col1"}, ...]
 *
 * 支持多个条件 (AND 分隔), 每个条件为 "table.column = table.column" 格式。
 * 不匹配的格式会跳过。
 */
function parseOnConditions(on: string): { sourceTable: string; sourceCol: string; targetTable: string; targetCol: string }[] {
  if (!on) return []
  const conditions: { sourceTable: string; sourceCol: string; targetTable: string; targetCol: string }[] = []
  const parts = on.split(/\s+AND\s+/i)
  for (const part of parts) {
    const match = part.trim().match(/^(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)$/)
    if (match) {
      conditions.push({ sourceTable: match[1], sourceCol: match[2], targetTable: match[3], targetCol: match[4] })
    }
  }
  return conditions
}

/** 点击关系项: 高亮对应边并显示边详情 */
function focusEdge(rel: any) {
  if (!g6Instance) return
  // 找到对应边的 ID
  const edges = g6Instance.getEdgeData() as any[]
  for (const edge of edges) {
    if (edge.source === rel.source && edge.target === rel.target) {
      g6Instance.setElementState(edge.id, ['selected'])
      // 清除之前选中的边
      selectedEdge.value = {
        source: rel.source,
        target: rel.target,
        on: rel.on,
        confidence: rel.confidence,
        relSource: rel.relSource,
        joinType: rel.joinType,
        cardinality: rel.cardinality,
      }
      break
    }
  }
}

/** 确认删除选中的边 (弹出删除确认对话框) */
function confirmDeleteEdge() {
  if (!selectedEdge.value) return
  deleteTarget.value = { from: selectedEdge.value.source, to: selectedEdge.value.target }
  deleteDialogVisible.value = true
}

// ── G6 初始化 ──────────────────────────────────────────────

/**
 * 初始化 G6 图实例
 *
 * 流程:
 *   1. 动态导入 @antv/g6 (~500KB, 懒加载减小首屏体积)
 *   2. 获取容器尺寸 (width, height)
 *   3. 构建行为列表 (根据当前模式)
 *   4. 创建 G6.Graph 实例, 配置节点/边样式、布局、插件、行为
 *   5. 绑定事件 (click/hover/dblclick)
 *   6. render 渲染
 *   7. 调整缩放和居中
 *   8. 启动边流动动画
 *
 * G6 v5 配置要点:
 *   - node.state: 定义了 selected/highlight/dim 三种状态样式
 *   - edge.state: 定义了 selected/highlight/dim 三种状态样式
 *   - plugins: 使用 minimap (缩略图) + tooltip (提示框)
 *   - 布局使用 d3-force + preLayout, 避免渲染后抖动
 */
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
    animation: false,  // 关闭动画, 加快渲染速度
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
        // 枢纽节点光晕: 中心度高于阈值时显示外发光
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
      type: 'quadratic',  // 二次贝塞尔曲线, 避免平行边重叠
      style: {
        stroke: (d: any) => getEdgeColor(d.data?.relSource || 'manual', d.data?.confidence || 0),
        lineWidth: 1.5,
        endArrow: true,
        endArrowSize: 6,
        cursor: 'pointer',
        // 边标签: JOIN 类型 (如 LEFT, INNER)
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

  // 左键点击边: 显示边详情面板
  g6Instance.on('edge:click', (e: any) => {
    const edgeId = e.target?.id
    if (edgeId) {
      const edgeData = g6Instance.getEdgeData(edgeId)
      if (edgeData) {
        const d = edgeData.data || {}
        selectedNode.value = null  // 清除节点选中
        selectedEdge.value = {
          source: edgeData.source,
          target: edgeData.target,
          on: d.on || '',
          confidence: d.confidence ?? 0,
          relSource: d.relSource || 'manual',
          joinType: d.joinType || 'LEFT',
          cardinality: d.cardinality || 'N:1',
        }
      }
    }
  })

  // 双击节点: 聚焦 2-hop 子图 (加载该节点的邻居子图)
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

/**
 * 构建行为列表 (根据当前交互模式)
 *
 * G6 v5 的 behaviors 是插件化配置, 支持动态替换。
 * 根据 interactionMode 决定:
 *   - move 模式: drag-canvas + zoom-canvas + drag-element + click-select + hover-activate
 *   - connect 模式: drag-canvas + zoom-canvas + create-edge + click-select + hover-activate
 *
 * click-select 行为: 点击节点选中, 点击空白取消选中。
 *   - 选中的节点/边显示 selected state 样式 (蓝色边框)
 *   - 同时更新 selectedNode / selectedEdge 响应式变量
 *
 * hover-activate 行为: 鼠标悬停节点时, 高亮其邻居节点, 淡化非邻居。
 *   - 仅对 node 生效 (enable: event.targetType === 'node')
 *   - degree: 1 (一阶邻居)
 *   - state: 'highlight' (高亮样式)
 *   - inactiveState: 'dim' (淡化样式)
 */
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
          selectedEdge.value = null  // 清除边选中
          selectedNode.value = {
            id: nodeData.id,
            label: d.label || nodeData.id,
            community: d.community ?? 0,
            centrality: d.centrality ?? 0,
            columnCount: d.columnCount ?? 0,
            metricCount: d.metricCount ?? 0,
            source: d.source ?? 'manual',
            degree: d.degree ?? 0,
          }
        }
      } else {
        selectedNode.value = null
        selectedEdge.value = null
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
              on_conditions: [{ source_column: '', target_column: '' }],
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

/**
 * 切换交互模式 (动态替换行为, 不销毁 G6 实例)
 *
 * G6 v5 支持 setBehaviors 方法动态替换行为列表,
 * 无需销毁重建 Graph 实例, 避免闪烁和重新布局。
 * 切换时将光标改为 crosshair (连线模式) 或 default (移动模式)。
 */
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

/**
 * 启动边流动动画 (lineDashOffset 递增)
 *
 * 对高置信度边 (>= EDGE_FLOW_THRESHOLD) 应用流动虚线动画。
 * 动画效果: 虚线沿边方向移动, 产生"数据流动"的视觉效果。
 * 实现方式: 每隔 200ms 递增 lineDashOffset, 通过 updateEdgeData 更新。
 * 性能优化: 只更新 flowEdgeIds 缓存中的边, 避免每帧遍历所有边。
 */
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

/**
 * 转换数据为 G6 格式
 *
 * 后端返回的 GraphData 格式与 G6 期望的格式不同:
 *   - G6 的节点数据: { id: string, data: { ... } }
 *   - 后端返回: { id: string, label: string, community: number, ... }
 * 此函数将后端格式转换为 G6 格式, 所有非 id 字段放入 data 中。
 */
function transformData(data: GraphData) {
  return {
    nodes: data.nodes.map(n => ({
      id: n.id,
      data: {
        label: n.label,
        community: n.community,
        centrality: n.centrality,
        columnCount: n.columnCount,
        metricCount: n.metricCount,
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

/** 搜索清空: 取消所有选中状态 */
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

/**
 * 双击聚焦: 加载 2-hop 子图
 *
 * 当用户双击节点时, 加载该节点的 2 跳邻居子图,
 * 聚焦到该节点并高亮显示。
 * 子图数据通过 graph.subgraph API 获取。
 */
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

/**
 * 加载图谱数据
 *
 * 流程:
 *   1. 调用 graph.full() 获取完整图数据
 *   2. 保存到 graphData ref (用于计算属性)
 *   3. 关闭 loading 状态, 让容器变为 visible
 *   4. 如果 G6 实例已存在, 直接 setData 更新
 *   5. 如果 G6 实例不存在, 调用 initG6 初始化
 *   6. 渲染后调整缩放和居中
 *   7. 重新缓存流动边
 */
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

/**
 * 表选择变更时加载列信息
 *
 * 当源表或目标表选中时, 调用 graph.tableColumns 获取两表的列信息。
 * 加载后尝试智能匹配: 如果两表有同名列 (如 id, xxx_id), 自动填充 ON 条件。
 * 匹配规则:
 *   1. 列名完全相同 (如 a.id = b.id)
 *   2. 源表列名 = 目标表名去掉前綴 + _id (如 a.user_id = b.id)
 *   3. 目标表列名 = 源表名去掉前綴 + _id (如 a.id = b.item_id)
 */
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

/**
 * 提交新增关系
 *
 * 校验:
 *   1. 表单验证 (源表/目标表必填)
 *   2. 至少一个 ON 条件
 * 提交后:
 *   1. 调用 graph.addRelationship 创建关系
 *   2. 成功后重新加载图谱数据
 *   3. 通知父组件数据已变更 (emit('dataChanged'))
 */
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

/**
 * 监听 dataSourceId 变化
 *
 * 当用户切换数据源时:
 *   1. 销毁旧的 G6 实例
 *   2. 停止流动动画
 *   3. 清除选中状态
 *   4. 加载新数据源的数据
 */
watch(() => props.dataSourceId, () => {
  if (g6Instance) {
    g6Instance.destroy()
    g6Instance = null
    stopFlowAnimation()
  }
  selectedNode.value = null
  loadGraphData()
})

// ── 选中节点时加载该表的指标列表 ──────────────────────────────

/**
 * 加载选中节点的业务指标列表
 *
 * 从语义层获取当前数据源的语义模型, 查找选中表对应的指标。
 * 指标加载失败不影响主功能 (图谱展示仍然正常),
 * 所以静默处理错误 (不显示错误提示)。
 */
async function loadNodeMetrics() {
  nodeMetrics.value = []
  if (!selectedNode.value || !props.dataSourceId) return
  try {
    const { data } = await semantic.current(props.dataSourceId)
    const tableModel = data?.content?.models?.find(m => m.name === selectedNode.value!.id)
    if (tableModel?.metrics) {
      nodeMetrics.value = tableModel.metrics
    }
  } catch {
    // 静默失败, 指标展示不影响主功能
  }
}

watch(selectedNode, (val) => {
  if (val) loadNodeMetrics()
})

// ── 暴露给父组件的方法 ──────────────────────────────────────

/**
 * 聚焦到指定节点 (供父组件调用)
 *
 * 父组件可以通过模板 ref 调用此方法:
 *   <SchemaGraph ref="schemaGraphRef" />
 *   schemaGraphRef.value.focusNode('table_name')
 */
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
    metricCount: d.metricCount ?? 0,
    source: d.source ?? 'manual',
    degree: d.degree ?? 0,
  }
  }
}

defineExpose({ focusNode })

// 窗口 resize: 使用 ResizeObserver 监听容器尺寸变化, 自动调整画布大小
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

.detail-panel {
  position: absolute;
  top: 12px;
  right: 12px;
  width: 260px;
  max-height: calc(100% - 24px);
  overflow-y: auto;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  z-index: 10;
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid #ebeef5;
  position: sticky;
  top: 0;
  background: #fff;
  z-index: 1;
  border-radius: 8px 8px 0 0;
}

.panel-title {
  font-weight: 600;
  font-size: 14px;
  color: #303133;
}

.panel-subtitle {
  font-size: 11px;
  color: #909399;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.close-btn {
  cursor: pointer;
  color: #c0c4cc;
  flex-shrink: 0;
}

.close-btn:hover {
  color: #606266;
}

.panel-body {
  padding: 12px 14px;
}

/* 统计行 */
.stat-row {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
  padding: 8px 0;
  border-bottom: 1px solid #f2f6fc;
}

.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
}

.stat-num {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.stat-label {
  font-size: 10px;
  color: #909399;
  margin-top: 2px;
}

/* 关系列表 */
.rel-section {
  margin-top: 4px;
}

.section-title {
  font-size: 11px;
  font-weight: 600;
  color: #909399;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}

.rel-empty {
  font-size: 12px;
  color: #c0c4cc;
  text-align: center;
  padding: 12px 0;
}

.rel-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
  margin-bottom: 2px;
}

.rel-item:hover {
  background: #f5f7fa;
}

.metric-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  margin-bottom: 2px;
}
.metric-icon {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  background: #ecf5ff;
  color: #409eff;
  font-size: 11px;
  font-weight: 600;
  font-style: italic;
}
.metric-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.metric-name {
  color: #303030;
  font-weight: 500;
  font-size: 12px;
}
.metric-meta {
  color: #909399;
  font-size: 11px;
  font-family: 'SF Mono', Consolas, monospace;
  word-break: break-all;
  line-height: 1.4;
}
.metric-source {
  font-size: 10px;
  margin-left: 4px;
  vertical-align: middle;
}
.metric-source.rule {
  color: #409eff;
}
.metric-source.ai {
  color: #e6a23c;
}

.rel-arrow {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  font-size: 12px;
  font-weight: bold;
}

.arrow-out {
  background: #ecf5ff;
  color: #409eff;
}

.arrow-in {
  background: #f0f9eb;
  color: #67c23a;
}

.rel-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.rel-name {
  font-size: 12px;
  font-weight: 500;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rel-meta {
  font-size: 10px;
  color: #909399;
  margin-top: 1px;
}

/* 边详情 */
.edge-flow {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 0;
  margin-bottom: 8px;
  border-bottom: 1px solid #f2f6fc;
}

.edge-table {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  background: #f5f7fa;
  padding: 4px 8px;
  border-radius: 4px;
  word-break: break-all;
}

.edge-join {
  font-size: 10px;
  color: #909399;
  flex-shrink: 0;
}

.edge-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

/* ON 条件 */
.on-section {
  margin-top: 4px;
  padding-top: 8px;
  border-top: 1px solid #f2f6fc;
}

.on-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  font-size: 12px;
  color: #606266;
}

.on-col {
  word-break: break-all;
  line-height: 1.4;
}

.on-col b {
  color: #303133;
}

.on-eq {
  color: #c0c4cc;
  flex-shrink: 0;
}

.on-raw {
  font-size: 11px;
  color: #606266;
  word-break: break-all;
  line-height: 1.5;
  padding: 6px 8px;
  background: #f5f7fa;
  border-radius: 4px;
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
