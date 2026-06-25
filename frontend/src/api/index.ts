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
    return apiClient.post<{ access_token: string; token_type: string }>(
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
  /** 触发扫描 → 生成/更新语义层 (含 LLM 推断, 慢操作, 单独设 5 分钟超时) */
  scan(id: string) {
    return apiClient.post<{
      semantic_model_id: string
      version: number
      table_count: number
      models: string[]
    }>(`/data-sources/${id}/scan`, null, { timeout: 300000 })
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

export interface SemanticTableModel {
  name: string
  display_name: string
  description?: string
  source: string
  confidence: number
  columns: SemanticColumn[]
  relationships: SemanticRelationship[]
  metrics?: any[]
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
  rows: (string | number | null)[][]
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
  } | null
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
export const STREAM_URL = `${apiClient.defaults.baseURL}/chat/stream`

export interface ConversationItem {
  conversation_id: string
  title: string
  turn_count: number
  last_sql: string
  last_tables: string[]
  timestamp: string
}

// ── Skills 管理 (T041) ─────────────────────────────────────

export interface Skill {
  name: string
  description: string
  version: string
  content: string
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
  name: string
  description: string
  type: string
  content: string
}

export const memory = {
  list() { return apiClient.get<Memory[]>('/memory') },
  save(data: Memory) { return apiClient.put<Memory>('/memory', data) },
  delete(name: string) { return apiClient.delete(`/memory/${name}`) },
}

// ── 已保存查询 / 看板 (T051) ───────────────────────────────

export interface SavedQuery {
  id: string
  user_id: string | null
  conversation_id: string | null
  question: string
  sql_text: string
  result_summary: string | null
  chart_config: Record<string, any> | null
  created_at: string | null
}

export const savedQuery = {
  list() { return apiClient.get<SavedQuery[]>('/saved-queries') },
  get(id: string) { return apiClient.get<SavedQuery>(`/saved-queries/${id}`) },
  /** 导出为 CSV (UX-08, 需指定数据源重跑) */
  exportCsv(id: string, data_source_id: string) {
    return apiClient.get(`/saved-queries/${id}/export`, {
      params: { data_source_id },
      responseType: 'blob', timeout: 120000,
    })
  },
}

// ── 反馈系统 (FBK-001/002) ─────────────────────────────────

export interface FeedbackCreate {
  saved_query_id?: string
  feedback_type: 'like' | 'dislike' | 'sql_correction' | 'chart_correction' | 'comment'
  rating?: number
  corrected_sql?: string
  comment?: string
}

export const feedback = {
  /** 提交反馈 (点赞/点踩/纠正) */
  create(data: FeedbackCreate) {
    return apiClient.post('/feedback', data)
  },
}

// ── 异步查询 (PERF-03) ───────────────────────────────────────

export interface AsyncTask {
  id: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'cancelled'
  progress: number
  current_stage: string | null
  result: {
    reply?: string | null
    sql?: string | null
    columns: string[]
    rows: any[][]
    row_count: number
    chart?: Record<string, any> | null
  } | null
  error: string | null
  created_at: string | null
}

export const asyncQuery = {
  /** 提交异步查询 */
  create(data: { question: string; data_source_id: string }) {
    return apiClient.post<AsyncTask>('/async-query', data)
  },
  /** 轮询任务状态 */
  get(id: string) {
    return apiClient.get<AsyncTask>(`/async-query/${id}`)
  },
  /** 取消任务 */
  cancel(id: string) {
    return apiClient.delete(`/async-query/${id}`)
  },
}

// ── 备份恢复 (OPS-02) ────────────────────────────────────────

export const backupApi = {
  /** 备份元数据库 → 下载 .sql */
  create() {
    return apiClient.post('/backup', null, { responseType: 'blob', timeout: 300000 })
  },
  /** 恢复 (上传 .sql, 需 confirm) */
  restore(file: File) {
    const form = new FormData()
    form.append('file', file)
    return apiClient.post('/backup/restore?confirm=true', form, { timeout: 300000 })
  },
}

export { default as apiClient } from './client'
