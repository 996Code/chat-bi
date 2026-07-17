/**
 * G6 v5 配置: 节点/边样式预设、布局
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

/** 获取社区颜色 (深色, 用于节点填充) */
export function getCommunityColor(community: number): string {
  return COMMUNITY_COLORS[community % COMMUNITY_COLORS.length]
}

/** 获取社区光晕色 (用于 hub 节点 shadow) */
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

/** 根据中心度计算节点大小 */
export function getNodeSize(centrality: number): number {
  const clamped = Math.max(0, Math.min(1, centrality * 3))
  return NODE_SIZE_MIN + (NODE_SIZE_MAX - NODE_SIZE_MIN) * clamped
}

// ── 边置信度颜色 ────────────────────────────────────────────

/** 按置信度分级的边颜色 */
export const EDGE_CONFIDENCE_COLORS: Record<string, string> = {
  foreign_key: '#5B8FF9',     // FK: 深蓝 (最可信)
  ai_inferred: '#6DC8EC',     // AI推断: 浅蓝
  name_pattern: '#C4C4C4',    // 名称匹配: 灰色
  manual: '#5AD8A6',          // 手动: 绿色
}

/** 根据 relSource 获取边颜色 */
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

/** G6 力导向布局配置
 *
 * 用 d3-force + preLayout:true:
 * - preLayout 让布局在绘制前同步完成 (simulate), 之后不再迭代 → 不抖动
 * - 配合 fitView, render 时节点位置已确定 → 缩放正确
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

/** Tooltip 样式 (注入到 G6 tooltip 插件) */
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
