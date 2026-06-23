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
}

export { default as apiClient } from './client'
