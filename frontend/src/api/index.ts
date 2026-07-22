/**
 * API 封装层
 * 按模块组织: datasource / semantic / devAuth
 */
import apiClient from './client'

// ── 开发认证 (仅 DEBUG 模式) ──────────────────────────────

export const devAuth = {
  /** 获取开发 token (仅 DEBUG=True, 生产 404) */
  getToken(params: {
    tenant_id?: string
    user_id?: string
    email?: string
    role?: 'admin' | 'user' | 'read_only'
  }) {
    return apiClient.post<{ access_token: string; refresh_token: string; token_type: string }>(
      '/dev/token',
      {
        tenant_id: 'default_tenant',
        user_id: 'admin_user', // 必须是 users 表真实存在的用户 (audit_logs 有外键约束)
        email: 'admin@chatbi.local',
        role: 'admin',
        ...params,
      },
    )
  },
}

// ── 正式认证 (AUTH-01~03) ────────────────────────────────────

export interface AuthResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export const auth = {
  /** 注册 (创建独立 tenant + user) */
  register(data: { email: string; username: string; password: string }) {
    return apiClient.post<AuthResponse>('/auth/register', data)
  },
  /** 登录 (密码校验 + 登录锁定) */
  login(data: { email: string; password: string }) {
    return apiClient.post<AuthResponse>('/auth/login', data)
  },
  /** 刷新 access token */
  refresh(refresh_token: string) {
    return apiClient.post<AuthResponse>('/auth/refresh', { refresh_token })
  },
}

// ── 数据源 ────────────────────────────────────────────────

export interface DataSource {
  id: string
  tenant_id: string
  name: string
  db_type: string
  host: string
  port: number
  database: string
  username: string
  is_active: boolean
  // 扫描状态 (对标 V1 + 经验教训 #25: 异步任务进度)
  scan_status: 'idle' | 'scanning' | 'done' | 'failed'
  scan_progress: number  // 0-100
  scan_stage: string | null
  scan_error: string | null
}

export interface DataSourceCreate {
  name: string
  db_type: 'postgresql' | 'mysql'
  host: string
  port: number
  database: string
  username: string
  password: string
}

export const datasource = {
  list() {
    return apiClient.get<DataSource[]>('/data-sources')
  },
  get(id: string) {
    return apiClient.get<DataSource>(`/data-sources/${id}`)
  },
  create(data: DataSourceCreate) {
    return apiClient.post<DataSource>('/data-sources', data)
  },
  /** 触发扫描 (异步: 立即返回 202 + scan_status=scanning, 后台跑, 前端轮询 get 拿进度) */
  scan(id: string) {
    return apiClient.post<DataSource>(`/data-sources/${id}/scan`)
  },
  /** 启停数据源 (DSO-08) */
  toggle(id: string, is_active: boolean) {
    return apiClient.patch<DataSource>(`/data-sources/${id}`, { is_active })
  },
  /** 健康检查 (DSO-02) */
  health(id: string) {
    return apiClient.get<{ ok: boolean; latency_ms: number; error: string | null }>(`/data-sources/${id}/health`)
  },
  /** 全量健康检查 (admin, DSO-02) */
  checkAllHealth() {
    return apiClient.post<{ checked: number; healthy: number; unhealthy: number; recovered: number; newly_error: number }>('/data-sources/health-check/all')
  },
  /** 全量元数据刷新 (admin, DSO-04) */
  refreshAllMetadata() {
    return apiClient.post<{ checked: number; refreshed: number; unchanged: number; failed: number }>('/data-sources/refresh-metadata/all', null, { timeout: 300000 })
  },
}

// ── 语义层 ────────────────────────────────────────────────

export interface SemanticModel {
  id: string
  tenant_id: string
  data_source_id: string
  version: number
  is_current: boolean
  content: {
    version: number
    models: SemanticTableModel[]
    sample_questions: string[]
  }
}

export interface SemanticMetric {
  name: string
  display_name: string
  formula: string
  type: 'single' | 'composite'
  condition?: string
  description?: string
  factor_metric_names?: string[]
  co_occurrence: number
  source: string
}

export interface SemanticTableModel {
  name: string
  display_name: string
  description?: string
  source: string
  confidence: number
  columns: SemanticColumn[]
  relationships: SemanticRelationship[]
  metrics: SemanticMetric[]
}

export interface SemanticColumn {
  name: string
  display_name: string
  data_type: string
  semantic_type: 'measure' | 'dimension' | 'key' | null
  description?: string
  source: string
  confidence: number
}

export interface SemanticRelationship {
  name: string
  target_model: string
  join_type: string
  on: string
  type: string
  source: string
  confidence: number
}

export const semantic = {
  /** 当前版本 */
  current(data_source_id: string) {
    return apiClient.get<SemanticModel | null>('/semantic-models', {
      params: { data_source_id },
    })
  },
  /** 版本历史 */
  versions(sm_id: string) {
    return apiClient.get<
      { id: string; version: number; is_current: boolean }[]
    >(`/semantic-models/${sm_id}/versions`)
  },
  /** 回滚 (append-only: 复制成新版本) */
  rollback(sm_id: string, to_version: number) {
    return apiClient.post<SemanticModel>(
      `/semantic-models/${sm_id}/rollback`,
      null,
      { params: { to_version } },
    )
  },
  /** diff */
  diff(sm_id: string, from: number, to: number) {
    return apiClient.get(`/semantic-models/${sm_id}/diff`, {
      params: { from, to },
    })
  },
  /** 局部更新 (T015 行内编辑: 表/列语义 → 新版本) */
  patch(sm_id: string, data: {
    table_name: string
    display_name?: string
    description?: string
    column_name?: string
    column_display_name?: string
    column_semantic_type?: string
    column_description?: string
  }) {
    return apiClient.patch<SemanticModel>(`/semantic-models/${sm_id}`, data)
  },
  /** 指标更新 (编辑/新增/删除 → 新版本, source=manual) */
  patchMetric(sm_id: string, data: {
    table_name: string
    metric_name?: string
    metric_display_name?: string
    metric_formula?: string
    metric_type?: 'single' | 'composite'
    metric_condition?: string
    metric_description?: string
    metric_factor_metric_names?: string[]
    delete_metric?: boolean
  }) {
    return apiClient.patch<SemanticModel>(`/semantic-models/${sm_id}/metric`, data)
  },
}

// ── 聊天问答 ──────────────────────────────────────────────

export interface ChatRequest {
  question: string
  data_source_id?: string
  conversation_id?: string
}

export interface ChatResponse {
  success: boolean
  conversation_id: string | null
  intent: string | null
  question: string | null
  sql: string | null
  columns: string[]
  rows: (string | number | boolean | null)[][]
  row_count: number
  truncated: boolean
  chart: Record<string, any> | null
  error: string | null
  reply: string | null
  ask_user: { reason: string; question: string; options: string[] | null } | null
  stage: string
  llm_calls: number
  self_heal_rounds: number
  token_usage: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    llm_calls: number
    nodes?: Record<string, any>  // 各节点 token 明细 (OBS-002)
  } | null
  fewshot_count: number  // 命中的 few-shot 示例数 (RAG-004)
  degraded: boolean  // 检索/图表降级标记 (结果可能不精确)
}

export const chat = {
  /** 自然语言问答 → Agent 全流程 */
  ask(req: ChatRequest) {
    return apiClient.post<ChatResponse>('/chat', req, { timeout: 120000 })
  },
}

// ── 可观测性 + 审计 ───────────────────────────────────────

export const observability = {
  /** 系统状态 (T050) */
  healthDetail() {
    return apiClient.get('/health/detail')
  },
  /** 审计日志 (T052) */
  auditLogs(params?: { limit?: number; resource_type?: string }) {
    return apiClient.get('/audit-logs', { params })
  },
  /** 慢查询列表 (DSO-07) */
  slowQueries(limit?: number) {
    return apiClient.get('/slow-queries', { params: { limit } })
  },
  /** 对话列表 (T052) */
  conversations() {
    return apiClient.get('/conversations')
  },
  /** 对话详情 (T052) */
  conversationDetail(convId: string) {
    return apiClient.get(`/conversations/${convId}`)
  },
  /** 对话标题 (轻量, 供记忆页显示来源) */
  conversationTitle(convId: string) {
    return apiClient.get(`/conversations/${convId}/title`)
  },
  /** 对话 trace 导出 (T050 dump-prompts: 各轮 prompt + token 统计) */
  conversationTrace(convId: string) {
    return apiClient.get(`/conversations/${convId}/trace`)
  },
  /** 数据源状态监控 (DSO-05, 按数据源聚合最近 24h) */
  datasourceMetrics() {
    return apiClient.get('/datasource-metrics')
  },
}

// ── 流式问答 (SSE) ────────────────────────────────────────

/** SSE 流式问答端点 (前端用原生 fetch + ReadableStream 解析, 非走 axios) */
export const STREAM_URL = `${apiClient.defaults.baseURL || '/chat-bi/api/v1'}/chat/stream`

export interface ConversationItem {
  conversation_id: string
  title: string
  turn_count: number
  last_sql: string
  last_tables: string[]
  timestamp: string
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
}

// ── Skills 管理 (T041) ─────────────────────────────────────

export interface Skill {
  name: string
  description: string
  version: string
  content: string
  references?: Record<string, string>  // T060: reference 子文件 (key=文件名, value=内容)
}

export const skills = {
  list() { return apiClient.get<Skill[]>('/skills') },
  get(name: string) { return apiClient.get<Skill>(`/skills/${name}`) },
  save(data: Skill) { return apiClient.put<Skill>('/skills', data) },
  delete(name: string) { return apiClient.delete(`/skills/${name}`) },
  /** 预览规则效果 (T041: 用规则对示例问题跑一次 SQL 生成) */
  preview(content: string, testQuestion?: string) {
    return apiClient.post<{ generated_sql: string | null; error: string | null; usage: any }>(
      '/skills/preview', { content, test_question: testQuestion }, { timeout: 60000 },
    )
  },
}

// ── Agent 记忆管理 (T045) ──────────────────────────────────

export interface Memory {
  id: string
  name: string
  description: string
  type: string
  content: string
  consolidated?: boolean
  created_at?: string
  conversation_id?: string
  // linkage 结构化字段
  tables?: string[]
  co_occurrence?: number
  join_paths?: { on: string; join_type: string }[]
  scenes?: string[]
  aggregation?: string
}

export interface ConsolidateStatus {
  status: string       // idle | running | done | failed
  progress: number     // 0-100
  stage: string        // 当前步骤文字
  result?: {
    consolidated: number
    total: number
    detail: string
    graph_sync_conflict?: boolean
    graph_sync_error?: string
    graph_sync?: { new_version: number | null; boosted_pairs: number; new_pairs: number; detail?: string }
  }
  error?: string
}

export const memory = {
  list(dataSourceId: string, includeConsolidated = false) { return apiClient.get<Memory[]>('/memory', { params: { data_source_id: dataSourceId, include_consolidated: includeConsolidated } }) },
  save(data: Partial<Memory> & { name: string; description: string; content: string }, dataSourceId: string) { return apiClient.put<Memory>('/memory', data, { params: { data_source_id: dataSourceId } }) },
  delete(id: string, dataSourceId: string) { return apiClient.delete(`/memory/${id}`, { params: { data_source_id: dataSourceId } }) },
  /** 触发整理 (异步, 返回 202) */
  consolidate(dataSourceId: string, ids?: string[]) { return apiClient.post<ConsolidateStatus>('/memory/consolidate', ids ? { ids } : null, { params: { data_source_id: dataSourceId } }) },
  /** 查询整理状态 (轮询用) */
  consolidateStatus(dataSourceId: string) { return apiClient.get<ConsolidateStatus>('/memory/consolidate/status', { params: { data_source_id: dataSourceId } }) },
  /** 重试图谱同步 (整理后版本冲突时调用) */
  retryGraphSync(dataSourceId: string) { return apiClient.post<ConsolidateStatus>('/memory/consolidate/retry', null, { params: { data_source_id: dataSourceId } }) },
}

// ── 看板 (V1 Dashboard + Widget) ─────────────────────────────

export interface DashboardItem {
  id: string
  name: string
  created_at: string | null
  updated_at: string | null
}

export interface DashboardWidget {
  id: string
  dashboard_id: string
  question: string
  query_sql: string | null
  datasource_id: string
  chart_type: string
  columns: any[]
  rows: any[]
  row_count: number | null
  position_x: number
  position_y: number
  width: number
  height: number
  created_at: string | null
  updated_at: string | null
  /** refresh 端点返回的实时生成图表 (list/get 端点不含此字段) */
  chart_option?: Record<string, any> | null
}

export interface DashboardDetail {
  id: string
  name: string
  widgets: DashboardWidget[]
  created_at: string | null
  updated_at: string | null
}

export const dashboard = {
  /** 列出所有看板 */
  list() { return apiClient.get<DashboardItem[]>('/dashboards') },
  /** 新建看板 */
  create(data: { name: string }) { return apiClient.post<DashboardItem>('/dashboards', data) },
  /** 更新看板名称 */
  update(id: string, data: { name: string }) { return apiClient.put<DashboardItem>(`/dashboards/${id}`, data) },
  /** 删除看板 */
  delete(id: string) { return apiClient.delete(`/dashboards/${id}`) },
  /** 获取看板详情 (含所有 widget) */
  get(id: string) { return apiClient.get<DashboardDetail>(`/dashboards/${id}`) },
  /** 向看板添加 widget (只保存 SQL + 数据源, 不存结果快照 — 实时查询模式) */
  addWidget(dashboardId: string, data: {
    question: string
    query_sql: string
    datasource_id: string
    chart_type?: string
    position_x?: number
    position_y?: number
    width?: number
    height?: number
  }) { return apiClient.post<DashboardWidget>(`/dashboards/${dashboardId}/widgets`, data) },
  /** 删除 widget */
  deleteWidget(dashboardId: string, widgetId: string) {
    return apiClient.delete(`/dashboards/${dashboardId}/widgets/${widgetId}`)
  },
  /** 批量更新 widget 布局 (拖拽/缩放后保存) */
  updateLayout(dashboardId: string, items: { id: string; x: number; y: number; w: number; h: number }[]) {
    return apiClient.put(`/dashboards/${dashboardId}/widgets/layout`, items)
  },
  /** 刷新 widget (重跑 SQL, 用缓存配置注入数据) */
  refreshWidget(dashboardId: string, widgetId: string) {
    return apiClient.put<DashboardWidget>(`/dashboards/${dashboardId}/widgets/${widgetId}/refresh`)
  },
}

// ── 知识图谱 (NetworkX + AntV G6) ────────────────────────────

export interface GraphNode {
  id: string
  label: string
  community: number
  centrality: number
  columnCount: number
  metricCount: number
  source: string
  degree: number
}

export interface GraphEdge {
  source: string
  target: string
  on: string
  confidence: number
  relSource: string
  joinType: string
  cardinality: string
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface CommunityResult {
  communities: string[][]
  count: number
}

export interface HubTable {
  table: string
  centrality: number
}

export interface HubResult {
  hubs: HubTable[]
}

export interface ImpactResult {
  table: string
  impacted_tables: string[]
  count: number
}

export interface JoinPathItem {
  tables: string[]
  on_conditions: string[]
  join_types: string[]
  confidences: number[]
  total_weight: number
}

/** 反向关系 (其他表引用了此表) */
export interface ReverseRelationship {
  source: string
  target: string
  on: string
  joinType: string
  cardinality: string
  confidence: number
  relSource: string
}

export interface JoinPathResult {
  source: string
  target: string
  paths: JoinPathItem[]
  found: boolean
}

export interface TableColumn {
  name: string
  display_name: string
  data_type: string
  semantic_type: string | null
  label: string
}

export const graph = {
  /** 获取全图数据 (G6 渲染) */
  full(dataSourceId: string) {
    return apiClient.get<GraphData>('/graph', { params: { data_source_id: dataSourceId } })
  },
  /** 获取子图 (聚焦某表) */
  subgraph(dataSourceId: string, center: string, depth = 2) {
    return apiClient.get<GraphData>('/graph/subgraph', { params: { data_source_id: dataSourceId, center, depth } })
  },
  /** 获取社区列表 */
  communities(dataSourceId: string) {
    return apiClient.get<CommunityResult>('/graph/communities', { params: { data_source_id: dataSourceId } })
  },
  /** 获取枢纽表 */
  hubs(dataSourceId: string, topK = 10) {
    return apiClient.get<HubResult>('/graph/hubs', { params: { data_source_id: dataSourceId, top_k: topK } })
  },
  /** 影响分析 */
  impact(dataSourceId: string, table: string) {
    return apiClient.get<ImpactResult>('/graph/impact', { params: { data_source_id: dataSourceId, table } })
  },
  /** 获取反向关系 (其他表引用了此表) */
  reverseRelationships(dataSourceId: string, table: string) {
    return apiClient.get<{ table: string; relationships: ReverseRelationship[]; count: number }>('/graph/reverse-relationships', { params: { data_source_id: dataSourceId, table } })
  },
  /** JOIN 路径 */
  joinPath(dataSourceId: string, source: string, target: string) {
    return apiClient.get<JoinPathResult>('/graph/join-path', { params: { data_source_id: dataSourceId, source, target } })
  },
  /** 获取表列信息 (用于 ON 条件下拉框) */
  tableColumns(dataSourceId: string, table: string) {
    return apiClient.get<{ table: string; columns: TableColumn[] }>('/graph/table-columns', { params: { data_source_id: dataSourceId, table } })
  },
  /** 新增关系 */
  addRelationship(dataSourceId: string, data: {
    from_table: string
    name: string
    target_model: string
    join_type?: string
    on?: string
    on_conditions?: { source_column: string; target_column: string }[]
    type?: string
    source?: string
    confidence?: number
  }) {
    return apiClient.post<{ success: boolean; graph: GraphData }>('/graph/relationship', data, { params: { data_source_id: dataSourceId } })
  },
  /** 删除关系 */
  deleteRelationship(dataSourceId: string, from: string, target: string) {
    return apiClient.delete<{ success: boolean }>('/graph/relationship', { params: { data_source_id: dataSourceId, from, target } })
  },
}

export { default as apiClient } from './client'
