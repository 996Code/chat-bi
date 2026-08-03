/**
 * G6 v5 配置 — 知识图谱可视化样式、布局、工具
 *
 * 架构职责:
 *   为 SchemaGraph.vue 组件提供 G6 图形渲染所需的配置常量
 * 和工具函数, 包括:
 *   - 社区颜色方案 (12 色调色板)
 *   - 节点尺寸计算 (按中心度缩放)
 *   - 边颜色 (按置信度/来源分级)
 *   - 力导向布局参数
 *   - Tooltip 样式
 *
 * 设计决策:
 *   - 社区颜色使用 AntV 官方推荐色板, 色相间隔均匀, 色盲友好
 *   - 节点大小与中心度 (centrality) 呈线性关系, 范围 36-72px
 *   - 边颜色按 relSource 分级: FK > AI推断 > 名称匹配 > 手动
 *   - 高置信度边 (>=0.8) 启用流动虚线动画, 视觉上突出重要关系
 *   - 力导向布局使用 d3-force + preLayout:true, 避免渲染后抖动
 *
 * G6 v5 使用 new Graph() + extensions 注册模式
 * 参考: https://g6-next.antv.antgroup.com/
 */

// ── 社区颜色 ────────────────────────────────────────────────

/** 社区颜色方案 (最多 12 色, 循环使用) */
export const COMMUNITY_COLORS = [
  '#5B8FF9', // 蓝
  '#5AD8A6', // 绿
  '#F6BD16', // 黄
  '#E86452', // 红
  '#6DC8EC', // 浅蓝
  '#945FB9', // 紫
  '#FF9845', // 橙
  '#1E9493', // 青
  '#FF99C3', // 粉
  '#269A99', // 深青
  '#BDD2FD', // 淡蓝
  '#BEDED1', // 淡绿
]

/**
 * 获取社区颜色 (深色, 用于节点填充)
 *
 * @param community - 社区编号 (从 0 开始)
 * @returns 十六进制颜色字符串
 */
export function getCommunityColor(community: number): string {
  return COMMUNITY_COLORS[community % COMMUNITY_COLORS.length]
}

/**
 * 获取社区光晕色 (用于 hub 节点发光效果)
 *
 * 将十六进制颜色转换为 rgba, 透明度 0.5。
 * 这样在节点周围产生柔和的发光效果, 突出中心节点。
 *
 * @param community - 社区编号
 * @returns rgba 颜色字符串
 */
export function getCommunityGlow(community: number): string {
  const color = getCommunityColor(community)
  const r = parseInt(color.slice(1, 3), 16)
  const g = parseInt(color.slice(3, 5), 16)
  const b = parseInt(color.slice(5, 7), 16)
  return `rgba(${r}, ${g}, ${b}, 0.5)`
}

// ── 节点尺寸 ────────────────────────────────────────────────

/** 节点大小范围 (按中心度缩放) */
export const NODE_SIZE_MIN = 36
export const NODE_SIZE_MAX = 72

/** 枢纽节点光晕阈值 (中心度 > 此值显示外发光) */
export const NODE_GLOW_THRESHOLD = 0.3

/**
 * 根据中心度计算节点大小
 *
 * 将中心度 (0-1 范围) 映射到 NODE_SIZE_MIN ~ NODE_SIZE_MAX 范围。
 * 中心度乘以 3 再 clamp, 使得大多数节点的尺寸差异更明显。
 * 中心度 >= 0.33 的节点达到最大尺寸。
 *
 * @param centrality - 中心度值 (0-1)
 * @returns 节点尺寸 (像素)
 */
export function getNodeSize(centrality: number): number {
  const clamped = Math.max(0, Math.min(1, centrality * 3))
  return NODE_SIZE_MIN + (NODE_SIZE_MAX - NODE_SIZE_MIN) * clamped
}

// ── 边置信度颜色 ────────────────────────────────────────────

/**
 * 按置信度分级的边颜色
 *
 * 不同的关系来源 (relSource) 使用不同的颜色, 帮助用户直观区分:
 *   - foreign_key: 深蓝 (最可信, 数据库原生 FK 约束)
 *   - ai_inferred: 浅蓝 (AI 推断, 基于语义分析)
 *   - name_pattern: 灰色 (名称匹配, 如同名列)
 *   - manual: 绿色 (用户手动创建)
 */
export const EDGE_CONFIDENCE_COLORS: Record<string, string> = {
  foreign_key: '#5B8FF9',     // FK: 深蓝 (最可信)
  ai_inferred: '#6DC8EC',     // AI推断: 浅蓝
  name_pattern: '#C4C4C4',    // 名称匹配: 灰色
  manual: '#5AD8A6',          // 手动: 绿色
}

/**
 * 根据 relSource 和 confidence 获取边颜色
 *
 * 优先使用 relSource 对应的预设颜色, 如果没有匹配则按 confidence 降级:
 *   - >= 0.8: 深蓝 (高可信)
 *   - >= 0.5: 浅蓝 (中等可信)
 *   - < 0.5: 灰色 (低可信)
 *
 * @param relSource - 关系来源标识
 * @param confidence - 置信度 (0-1)
 * @returns 十六进制颜色字符串
 */
export function getEdgeColor(relSource: string, confidence: number): string {
  if (EDGE_CONFIDENCE_COLORS[relSource]) {
    return EDGE_CONFIDENCE_COLORS[relSource]
  }
  if (confidence >= 0.8) return '#5B8FF9'
  if (confidence >= 0.5) return '#6DC8EC'
  return '#C4C4C4'
}

/** 边流动动画配置 (高 confidence 边) */
export const EDGE_FLOW_ANIMATION = {
  lineDash: [8, 4],
  lineDashOffset: 12,
}

/** 边流动动画启用的 confidence 阈值 */
export const EDGE_FLOW_THRESHOLD = 0.8

// ── 布局 ────────────────────────────────────────────────────

/**
 * G6 力导向布局配置
 *
 * 用 d3-force + preLayout:true:
 *   - preLayout 让布局在绘制前同步完成 (simulate), 之后不再迭代
 *   - 避免渲染后节点位置抖动, 提升用户体验
 *   - 配合 fitView, render 时节点位置已确定 → 缩放正确
 *
 * 参数调优说明:
 *   - linkDistance: 200px — 边长度, 太近节点重叠, 太远图松散
 *   - nodeStrength: -400 — 节点间斥力, 负值越大越分散
 *   - edgeStrength: 0.1 — 边拉力, 值越大边越短
 *   - collideStrength: 0.8 — 碰撞检测强度, 防止节点重叠
 *   - alphaDecay: 0.05 — 衰减速度, 值越小收敛越快
 *   - alphaMin: 0.001 — 停止迭代阈值
 */
export const D3_FORCE_LAYOUT = {
  type: 'd3-force',
  preLayout: true,
  preventOverlap: true,
  nodeSize: NODE_SIZE_MAX,
  linkDistance: 200,
  nodeStrength: -400,
  edgeStrength: 0.1,
  collideStrength: 0.8,
  alphaDecay: 0.05,
  alphaMin: 0.001,
  forceSimulation: null,
}

// ── Tooltip ─────────────────────────────────────────────────

/**
 * Tooltip 样式 (注入到 G6 tooltip 插件)
 *
 * G6 的 tooltip 插件会创建一个 .g6-tooltip 的 DOM 元素,
 * 这里定义其样式: 半透明黑底白字, 圆角, 最大宽度限制。
 * 注入到 G6 的 tooltip 配置中, 自动生效。
 */
export const TOOLTIP_STYLE = `
  .g6-tooltip {
    background: rgba(0, 0, 0, 0.75);
    color: #fff;
    padding: 8px 12px;
    border-radius: 4px;
    font-size: 12px;
    line-height: 1.6;
    max-width: 320px;
    word-break: break-all;
  }
`
