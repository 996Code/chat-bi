/**
 * API 封装层 — 前端与后端的所有 HTTP 通信接口
 *
 * 架构职责:
 *   1. 按业务模块组织 API 调用 (datasource / semantic / chat / auth / graph / ...)
 *   2. 提供 TypeScript 类型定义, 确保前后端接口契约一致
 *   3. 统一通过 apiClient (Axios 实例) 发送请求, 自动携带 JWT token
 *
 * 模块划分:
 *   - devAuth:    开发环境调试 token (仅 DEBUG=True 时可用)
 *   - auth:       正式认证 (注册/登录/刷新)
 *   - datasource: 数据源 CRUD + 扫描 + 健康检查
 *   - semantic:   语义模型 (表/列/指标/关系) 版本管理
 *   - chat:       自然语言问答 (Agent 全流程)
 *   - observability: 可观测性 (健康/审计/慢查询/对话)
 *   - skills:     SQL 生成规则管理
 *   - memory:     Agent 长期记忆管理 + 整理
 *   - dashboard:  看板 + widget CRUD
 *   - graph:      知识图谱 (AntV G6 渲染数据源)
 *
 * 设计决策:
 *   - 每个 API 函数返回 AxiosResponse<T> 的 Promise,
 *     调用方通过 .data 获取响应体, 通过 .status 获取状态码
 *   - 所有 HTTP 方法 (GET/POST/PUT/PATCH/DELETE) 统一通过 apiClient 调用
 *   - 超时时间在调用处单独设置 (如 chat.ask 需要 120s)
 *   - 类型定义与 API 函数放在同一文件, 避免跨文件引用,
 *     因为类型与接口高度耦合, 放在一起方便维护
 *
 * 数据流:
 *   组件 → API 函数 → apiClient → Axios 请求拦截器 (注入 token)
 *   → 后端 → Axios 响应拦截器 (401 刷新) → 组件拿到数据
 */
import apiClient from './client'

// ── 开发认证 (仅 DEBUG 模式) ──────────────────────────────

export const devAuth = {
  /**
   * 获取开发 token (仅 DEBUG=True, 生产环境返回 404)
   *
   * 开发调试用: 跳过登录页, 直接生成测试 token。
   * 默认值使用 admin_user 用户, 需确保该用户在 users 表中存在
   * (因为 audit_logs 有外键约束, 不存在的 user_id 会触发 500 错误)。
   * 调用方可以通过 params 覆盖默认值, 模拟不同用户的登录态。
   */
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

/**
 * 认证响应类型
 * access_token: 短期 token (默认 30 分钟), 用于 API 请求认证
 * refresh_token: 长期 token (默认 7 天), 用于刷新 access_token
 * token_type: 固定为 "bearer"
 */
export interface AuthResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export const auth = {
  /**
   * 注册 (创建独立 tenant + user)
   *
   * 后端会同时创建:
   *   1. 一个新的 tenant (租户)
   *   2. 一个初始 admin 用户
   * 注册成功后直接返回 access_token + refresh_token, 无需再登录
   */
  register(data: { email: string; username: string; password: string }) {
    return apiClient.post<AuthResponse>('/auth/register', data)
  },
  /**
   * 登录 (密码校验 + 登录锁定)
   *
   * 后端实现:
   *   - 密码校验 (bcrypt)
   *   - 登录失败计数 + 锁定 (超过阈值后临时禁止登录)
   *   - 成功登录后返回 token 对
   * 前端收到 token 后通过 useAuth.setToken() 存入 localStorage
   */
  login(data: { email: string; password: string }) {
    return apiClient.post<AuthResponse>('/auth/login', data)
  },
  /**
   * 刷新 access token
   *
   * 通常在 access_token 过期后 (401 响应) 调用。
   * 前端 api/client.ts 的响应拦截器会自动调用此接口,
   * 开发者一般不需要直接调用此函数。
   */
  refresh(refresh_token: string) {
    return apiClient.post<AuthResponse>('/auth/refresh', { refresh_token })
  },
}

// ── 数据源 ────────────────────────────────────────────────

/**
 * 数据源 (DataSource) 的数据类型定义
 *
 * 对应后端 datasources 表, 包含连接信息和扫描状态。
 * scan_status / scan_progress / scan_stage / scan_error 是一组
 * 关联字段, 用于跟踪异步扫描任务进度 (对标 V1 + 经验教训 #25)。
 *
 * scan_status 状态机: idle → scanning → done | failed
 * scan_progress: 0-100 的整数进度
 * scan_stage: 当前扫描阶段的文字描述 (如 "正在扫描表结构...")
 * scan_error: 扫描失败时的错误信息
 */

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
  /** 获取所有数据源列表 (不包含密码等敏感字段) */
  list() {
    return apiClient.get<DataSource[]>('/data-sources')
  },
  /** 获取单个数据源详情 (含连接信息) */
  get(id: string) {
    return apiClient.get<DataSource>(`/data-sources/${id}`)
  },
  /** 创建新数据源 (密码在后端加密存储, 不回传) */
  create(data: DataSourceCreate) {
    return apiClient.post<DataSource>('/data-sources', data)
  },
  /**
   * 触发扫描 (异步)
   *
   * 后端行为:
   *   - 立即返回 202 + scan_status=scanning
   *   - 后台启动异步任务扫描表结构/列信息/关系
   * 前端策略:
   *   - 调用后通过 setInterval 轮询 datasource.get() 获取进度
   *   - 轮询直到 scan_status 变为 done 或 failed
   *   - 扫描完成后自动触发语义层构建
   */
  scan(id: string) {
    return apiClient.post<DataSource>(`/data-sources/${id}/scan`)
  },
  /** 启停数据源 (DSO-08: 禁用时不影响已保存的看板/语义数据) */
  toggle(id: string, is_active: boolean) {
    return apiClient.patch<DataSource>(`/data-sources/${id}`, { is_active })
  },
  /**
   * 健康检查 (DSO-02)
   *
   * 返回:
   *   - ok: 连接是否正常
   *   - latency_ms: 连接延迟 (毫秒)
   *   - error: 连接失败时的错误信息 (ok=false 时)
   */
  health(id: string) {
    return apiClient.get<{ ok: boolean; latency_ms: number; error: string | null }>(`/data-sources/${id}/health`)
  },
  /**
   * 全量健康检查 (admin 功能, DSO-02)
   *
   * 对所有数据源执行健康检查, 返回统计结果:
   *   - checked: 检查总数
   *   - healthy: 健康的数量
   *   - unhealthy: 不健康的数量
   *   - recovered: 本次恢复的数量 (上次不健康, 本次健康)
   *   - newly_error: 本次新增异常的数量
   */
  checkAllHealth() {
    return apiClient.post<{ checked: number; healthy: number; unhealthy: number; recovered: number; newly_error: number }>('/data-sources/health-check/all')
  },
  /**
   * 全量元数据刷新 (admin 功能, DSO-04)
   *
   * 对所有数据源重新扫描并刷新元数据。
   * 超时 300s (5 分钟), 因为涉及大量数据源同步扫描。
   * 返回:
   *   - checked: 检查总数
   *   - refreshed: 需要刷新的数量
   *   - unchanged: 无需变化的数量
   *   - failed: 刷新失败的数量
   */
  refreshAllMetadata() {
    return apiClient.post<{ checked: number; refreshed: number; unchanged: number; failed: number }>('/data-sources/refresh-metadata/all', null, { timeout: 300000 })
  },
}

// ── 语义层 ────────────────────────────────────────────────

/**
 * 语义模型 (SemanticModel) — 数据源语义层的版本化快照
 *
 * 架构职责:
 *   每个数据源有一个或多个语义模型版本, 记录表/列/关系/指标
 *   的语义标注信息。语义层是 ChatBI 的核心抽象层:
 *   - 将物理数据库表/列映射为业务概念 (显示名、语义类型)
 *   - 自动推断表间关系 (FK、AI 推断、名称匹配)
 *   - 定义业务指标 (度量、复合指标)
 *   - 版本管理支持回滚和 diff 对比
 *
 * 版本管理:
 *   - 每次修改 (patch) 生成新版本, 旧版本保留 (append-only)
 *   - is_current 标记当前活动版本
 *   - 回滚操作本质是创建新版本 (不从旧版本继承)
 */

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

/**
 * 语义指标 (度量)
 *
 * type: single=单指标 (直接基于列), composite=复合指标 (基于其他指标)
 * formula: 计算公式, 如 "SUM(sales)" 或 "revenue - cost"
 * condition: 可选的过滤条件 WHERE 子句
 * co_occurrence: 在对话中与其他指标共同出现的频率 (用于推荐)
 * source: 来源 (auto_inferred / ai_inferred / rule_inferred / manual)
 */
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

/**
 * 语义表模型 — 物理表到业务概念的映射
 *
 * name: 物理表名
 * display_name: 业务显示名 (如 "orders" → "订单表")
 * source: 来源 (auto_inferred / ai_inferred / manual)
 * confidence: 推断置信度 (0-1)
 * columns: 列级别语义标注
 * relationships: 表间关系 (FK/JOIN 路径)
 * metrics: 该表相关的业务指标
 */
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

/**
 * 语义列 — 列级别的语义标注
 *
 * semantic_type: measure=度量 (可聚合), dimension=维度 (可分组),
 *                key=键 (主键/外键), null=未标注
 * confidence: 推断置信度 (0-1)
 * source: 来源 (auto_inferred / ai_inferred / manual)
 */
export interface SemanticColumn {
  name: string
  display_name: string
  data_type: string
  semantic_type: 'measure' | 'dimension' | 'key' | null
  description?: string
  source: string
  confidence: number
}

/**
 * 语义关系 — 表间 JOIN 关系
 *
 * target_model: 目标表名
 * join_type: JOIN 类型 (LEFT / INNER / RIGHT / FULL)
 * on: ON 条件字符串 (如 "a.id = b.a_id")
 * source: 来源 (foreign_key / ai_inferred / name_pattern / manual)
 * confidence: 推断置信度 (0-1)
 */
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
  /**
   * 获取当前版本语义模型 (按数据源查询)
   *
   * 查询参数 data_source_id 筛选, 返回 is_current=true 的版本。
   * 如果该数据源尚未扫描, 返回 null。
   * 这是语义层页面最常用的接口, 用于展示完整的语义标注。
   */
  current(data_source_id: string) {
    return apiClient.get<SemanticModel | null>('/semantic-models', {
      params: { data_source_id },
    })
  },
  /**
   * 版本历史列表
   *
   * 返回所有版本的基础信息 (id, version, is_current) 不包含 content。
   * 用于版本选择下拉框和版本对比页面的版本选择。
   * content 需要在选择后通过 diff 接口获取。
   */
  versions(sm_id: string) {
    return apiClient.get<
      { id: string; version: number; is_current: boolean }[]
    >(`/semantic-models/${sm_id}/versions`)
  },
  /**
   * 回滚到指定版本 (append-only 模式)
   *
   * 后端行为: 复制目标版本的 content 作为新版本, 不修改旧版本。
   * 这样确保所有版本可追溯, 回滚操作本身也产生一条审计日志。
   * 新版本会标记为 is_current=true。
   */
  rollback(sm_id: string, to_version: number) {
    return apiClient.post<SemanticModel>(
      `/semantic-models/${sm_id}/rollback`,
      null,
      { params: { to_version } },
    )
  },
  /**
   * 版本对比 diff
   *
   * 对比两个版本之间的差异, 返回格式与后端约定一致。
   * 用于语义层页面的版本对比功能, 展示表/列/关系的变化。
   */
  diff(sm_id: string, from: number, to: number) {
    return apiClient.get(`/semantic-models/${sm_id}/diff`, {
      params: { from, to },
    })
  },
  /**
   * 局部更新 (T015 行内编辑)
   *
   * 在语义层页面直接编辑表/列的显示名、描述、语义类型。
   * 每次 patch 生成一个新版本 (append-only)。
   * 支持:
   *   - 修改表显示名和描述
   *   - 修改列显示名、语义类型、描述
   */
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
  /**
   * 指标更新 (编辑/新增/删除)
   *
   * 专门用于业务指标的增删改操作。
   * 每次操作生成新版本, source=manual 标记为手动创建。
   * 支持:
   *   - 新增指标: 提供 metric_name 等字段, 不带 delete_metric
   *   - 编辑指标: 提供 metric_name 并修改其他字段
   *   - 删除指标: 提供 metric_name 并设置 delete_metric=true
   */
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

/**
 * 聊天请求参数
 *
 * question: 用户的自然语言问题
 * data_source_id: 可选, 指定在哪个数据源上查询 (不指定则由 Agent 自动选择)
 * conversation_id: 可选, 续接已有对话 (不指定则开启新对话)
 */
export interface ChatRequest {
  question: string
  data_source_id?: string
  conversation_id?: string
}

/**
 * 聊天响应 — Agent 全流程的输出结构
 *
 * 这是 ChatBI 最核心的数据类型, 涵盖 Agent 的完整执行结果:
 *   1. intent 识别: 用户的意图分类 (TEXT_TO_SQL / CLARIFICATION / GENERAL / ...)
 *   2. SQL 生成: 生成的 SQL 语句
 *   3. 查询执行: 执行结果 (columns + rows)
 *   4. 图表生成: 自动生成的 ECharts 配置
 *   5. 自愈流程: 如果 SQL 执行失败, Agent 会自动修复 (self_heal_rounds)
 *   6. 追问: 如果意图不明确, Agent 会反问用户 (ask_user)
 *   7. Token 统计: 各节点 token 消耗明细 (OBS-002)
 *   8. 降级标记: 检索/图表降级时标记 (degraded), 结果可能不精确
 *   9. 指标命中: 本次查询命中的业务指标 (metric_hits)
 *
 * 注意: 当 degraded=true 时, 前端应提示用户结果可能不精确。
 */

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
  metric_hits: { name: string; display_name?: string; table: string; co_occurrence?: number; source?: string; type?: string }[] | null  // 命中的业务指标 (source: rule_inferred/auto_inferred/ai_inferred/manual)
}

export const chat = {
  /**
   * 自然语言问答 — Agent 全流程
   *
   * 耗时说明: Agent 流程涉及 LLM 调用、SQL 执行、自愈等,
   * 超时设置为 120s (2 分钟), 远高于默认的 30s 超时。
   * 如果超时, 前端应提示用户重新提问或检查数据源状态。
   *
   * 调用方式: 同步 HTTP POST (非流式)
   * 流式问答见 STREAM_URL 常量, 使用 SSE + ReadableStream 解析
   */
  ask(req: ChatRequest) {
    return apiClient.post<ChatResponse>('/chat', req, { timeout: 120000 })
  },
}

// ── 可观测性 + 审计 ───────────────────────────────────────

/**
 * 可观测性 API — 系统状态监控、审计日志、对话管理
 *
 * 这些接口主要用于:
 *   - 系统健康检查页面 (ObservabilityView)
 *   - 对话历史页面 (HistoryView)
 *   - 数据源监控面板
 *
 * 每个接口的响应格式由后端 FastAPI 定义, 前端通过泛型推断类型。
 * 后端返回的审计日志遵循 RFC 3881 标准格式。
 */
export const observability = {
  /**
   * 系统状态详情 (T050)
   *
   * 返回所有后端服务的健康状态, 包括:
   *   - 数据库连接状态
   *   - LLM 服务状态
   *   - 缓存服务状态
   *   - 各组件版本号
   */
  healthDetail() {
    return apiClient.get('/health/detail')
  },
  /**
   * 审计日志列表 (T052)
   *
   * 支持分页和资源类型过滤。
   * 审计日志记录所有用户操作 (登录、查询、修改语义等),
   * 用于安全审计和问题排查。
   */
  auditLogs(params?: { limit?: number; resource_type?: string }) {
    return apiClient.get('/audit-logs', { params })
  },
  /**
   * 慢查询列表 (DSO-07)
   *
   * 返回执行时间超过阈值的 SQL 查询记录。
   * 用于 DBA 和系统管理员优化数据库性能。
   */
  slowQueries(limit?: number) {
    return apiClient.get('/slow-queries', { params: { limit } })
  },
  /**
   * 对话列表 (历史对话概览, T052)
   *
   * 返回所有对话的摘要信息, 用于对话历史页面。
   * 包含 conversation_id, title, turn_count, 时间戳等。
   */
  conversations() {
    return apiClient.get('/conversations')
  },
  /**
   * 对话详情 (T052)
   *
   * 返回指定对话的完整轮次数据, 包括:
   *   - 用户问题
   *   - AI 回复
   *   - 生成的 SQL
   *   - 查询结果 (样本)
   *   - Token 消耗
   *   - 自愈记录
   * 用于 ConversationDetailDrawer 组件展示。
   */
  conversationDetail(convId: string) {
    return apiClient.get(`/conversations/${convId}`)
  },
  /**
   * 对话标题 (轻量查询)
   *
   * 只返回对话标题, 不包含完整对话内容。
   * 用于记忆页面显示来源, 避免加载大量不必要的对话数据。
   */
  conversationTitle(convId: string) {
    return apiClient.get(`/conversations/${convId}/title`)
  },
  /**
   * 对话 trace 导出 (T050 dump-prompts)
   *
   * 返回对话各轮次的完整 prompt 和 token 统计。
   * 用于调试和优化 LLM 提示词, 分析 token 消耗模式。
   */
  conversationTrace(convId: string) {
    return apiClient.get(`/conversations/${convId}/trace`)
  },
  /**
   * 数据源状态监控 (DSO-05)
   *
   * 按数据源聚合最近 24 小时的运行指标:
   *   - 查询次数
   *   - 平均查询延迟
   *   - 错误率
   *   - 健康检查历史
   */
  datasourceMetrics() {
    return apiClient.get('/datasource-metrics')
  },
}

// ── 流式问答 (SSE) ────────────────────────────────────────

/**
 * SSE 流式问答端点
 *
 * 与同步 POST /chat 不同, 流式端点使用 Server-Sent Events (SSE)
 * 逐块返回 Agent 的中间结果 (思考过程、SQL、执行结果、图表等)。
 *
 * 前端使用原生 fetch + ReadableStream 解析, 不走 axios:
 *   - axios 对 SSE 流式响应支持不完善, 会等待整个响应完成
 *   - 原生 fetch 可以逐块读取 response.body 的 ReadableStream
 *   - 解析逻辑在 ChatView 组件中实现
 *
 * 流式响应的优势:
 *   - 用户可以看到 Agent 的实时思考过程 (thinking animation)
 *   - 首屏时间更短, 不需要等待 Agent 完全执行完毕
 *   - 可以提前展示部分结果 (如 SQL 生成后立即显示)
 */
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

/**
 * SQL 生成规则 (Skill)
 *
 * 每个 Skill 是一个命名的 SQL 生成规则, 包含:
 *   - name: 规则名称, 唯一标识
 *   - description: 规则描述
 *   - version: 版本号
 *   - content: 规则内容 (YAML/JSON 格式的提示词模板)
 *   - references: 可选, 引用的子文件 (如参考 SQL 示例), T060
 *
 * 规则系统允许用户自定义 SQL 生成行为, 是 ChatBI 的核心扩展点。
 * 规则可以引用外部 SQL 示例, 通过 few-shot 学习提升生成质量。
 */

export interface Skill {
  name: string
  description: string
  version: string
  content: string
  references?: Record<string, string>  // T060: reference 子文件 (key=文件名, value=内容)
}

export const skills = {
  /** 获取所有规则列表 */
  list() { return apiClient.get<Skill[]>('/skills') },
  /** 获取单个规则详情 (含完整 content) */
  get(name: string) { return apiClient.get<Skill>(`/skills/${name}`) },
  /** 保存规则 (新增或更新, 幂等操作) */
  save(data: Skill) { return apiClient.put<Skill>('/skills', data) },
  /** 删除规则 */
  delete(name: string) { return apiClient.delete(`/skills/${name}`) },
  /**
   * 预览规则效果 (T041)
   *
   * 用指定规则对示例问题跑一次 SQL 生成, 不保存结果。
   * 用于用户在保存前验证规则的正确性。
   * testQuestion 可选, 不传入时使用默认测试问题。
   * 超时 60s, 因为涉及 LLM 调用。
   */
  preview(content: string, testQuestion?: string) {
    return apiClient.post<{ generated_sql: string | null; error: string | null; usage: any }>(
      '/skills/preview', { content, test_question: testQuestion }, { timeout: 60000 },
    )
  },
}

// ── Agent 记忆管理 (T045) ──────────────────────────────────

/**
 * Agent 长期记忆 — 将对话中的知识沉淀为结构化记忆
 *
 * 记忆类型:
 *   - table_relationship: 表间关系记忆
 *   - metric: 业务指标记忆
 *   - sql_pattern: SQL 模式记忆
 *   - general: 一般性知识记忆
 *
 * 记忆来源:
 *   - 对话中自动提取 (auto_extracted)
 *   - 用户手动创建 (manual)
 *   - 整理后合并 (consolidated)
 *
 * 记忆与知识图谱的关系:
 *   - 记忆中的结构化信息 (tables, join_paths, co_occurrence)
 *     同步到知识图谱中
 *   - 整理 (consolidate) 操作触发图谱同步
 *   - 图谱同步冲突时, 通过 retryGraphSync 重试
 */

export interface Memory {
  id: string
  name: string
  description: string
  type: string
  content: string
  consolidated?: boolean
  created_at?: string
  conversation_id?: string
  // linkage 结构化字段 — 用于同步到知识图谱
  tables?: string[]
  co_occurrence?: number
  join_paths?: { on: string; join_type: string }[]
  scenes?: string[]
  aggregation?: string
}

/**
 * 整理状态 — 跟踪异步整理任务的进度
 *
 * status 状态机: idle → running → done | failed
 *   - idle: 未开始或已完成
 *   - running: 整理中
 *   - done: 整理完成, 可以查看结果
 *   - failed: 整理失败, 查看 error 获取原因
 *
 * result.graph_sync: 图谱同步结果
 *   - new_version: 同步后的图谱版本号
 *   - boosted_pairs: 提升置信度的关系对数
 *   - new_pairs: 新增的关系对数
 */
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
  /**
   * 获取记忆列表 (按数据源筛选)
   *
   * includeConsolidated: 是否包含已整理的记忆 (默认 false, 只显示未整理)
   */
  list(dataSourceId: string, includeConsolidated = false) { return apiClient.get<Memory[]>('/memory', { params: { data_source_id: dataSourceId, include_consolidated: includeConsolidated } }) },
  /**
   * 保存记忆 (新增或更新)
   *
   * dataSourceId 作为查询参数, 用于关联数据源。
   * name + description + content 是必填字段, 其他字段可选。
   */
  save(data: Partial<Memory> & { name: string; description: string; content: string }, dataSourceId: string) { return apiClient.put<Memory>('/memory', data, { params: { data_source_id: dataSourceId } }) },
  /**
   * 删除记忆
   */
  delete(id: string, dataSourceId: string) { return apiClient.delete(`/memory/${id}`, { params: { data_source_id: dataSourceId } }) },
  /**
   * 触发整理 (异步, 返回 202)
   *
   * 整理过程:
   *   1. 合并相似记忆 (基于语义相似度)
   *   2. 提取结构化信息 (tables, join_paths, co_occurrence)
   *   3. 同步到知识图谱
   * 前端通过 consolidateStatus 轮询进度。
   * ids 可选, 指定要整理的记忆 ID 列表 (不指定则整理所有未整理项)。
   */
  consolidate(dataSourceId: string, ids?: string[]) { return apiClient.post<ConsolidateStatus>('/memory/consolidate', ids ? { ids } : null, { params: { data_source_id: dataSourceId } }) },
  /**
   * 查询整理状态 (轮询用)
   *
   * 调用 consolidate 后, 前端应定期轮询此接口 (如每 2 秒一次)
   * 直到 status 变为 done 或 failed。
   */
  consolidateStatus(dataSourceId: string) { return apiClient.get<ConsolidateStatus>('/memory/consolidate/status', { params: { data_source_id: dataSourceId } }) },
  /**
   * 重试图谱同步
   *
   * 当整理后图谱同步出现版本冲突时 (graph_sync_conflict=true),
   * 调用此接口重试同步。通常发生在其他用户同时编辑了图谱。
   */
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
