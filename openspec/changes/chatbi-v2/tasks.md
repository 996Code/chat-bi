# ChatBI v2 Implementation Tasks

## Phase 1: 基础设施（1 周）✅ 已完成

> 11 个任务全部实现，20 个单元测试通过。后端连接真库（PostgreSQL 18.4），LLM 配置已填入。

- [x] T001: 后端项目骨架 (FastAPI + SQLAlchemy async + PostgreSQL + Milvus) — ✅ 测试覆盖 (test_core.TestAppHealth)
- [x] T002: 前端项目骨架 (Vue 3 + TypeScript + ECharts + Vite + Pinia) — ✅ 骨架就绪（ChatView/router/api client）
- [x] T003: Docker Compose (PostgreSQL + Milvus + Redis + Python + Vue + Nginx + supervisord) — ✅ docker-compose.yml + deploy/
- [x] T004: 数据库模型 (Tenant/User/DataSource/SemanticModel/Conversation/AuditLog/SavedQuery/Feedback) — ✅ 测试覆盖 (test_infrastructure.TestDatabaseModels)
- [x] T005: JWT 认证 + RBAC (admin/user/read_only) — ✅ 测试覆盖 (test_core.TestSecurity: JWT roundtrip/refresh token)
- [x] T006: 多租户框架级隔离 (SQLAlchemy session event 自动注入 tenant_id) — ✅ 测试覆盖 (test_auth.py::TestMultiTenantIsolation, 负向越权测试; 并修复 TenantMixin 死代码 → 7 模型继承)
- [x] T007: 密钥安全 (启动时检测 CHANGE_ME 占位符 → 拒绝启动) — ✅ 测试覆盖 (test_core.test_secret_key_is_not_default_in_production_ctx)
- [x] T008: 审计日志 (覆盖成功+失败+拒绝，统一字段) — ✅ 测试覆盖 (test_auth.py::TestWriteAuditLog, 三态 + audit_enabled 开关 + sql_text 记录)
- [x] T009: Checkpointer 会话持久化 (PostgreSQL, 写入简单 恢复时重建，对标 Claude Code JSONL 思路) — ✅ 测试覆盖 (test_infrastructure.TestCheckpointer)
- [x] T010: Agent 记忆文件化基础设施 (每条记忆 .md + MEMORY.md 索引，对标 Claude Code memdir) — ✅ 测试覆盖 (test_infrastructure.TestAgentMemory)
- [x] T011: Prompt 分层缓存基础设施 (静态段/动态段 boundary + section 级缓存，对标 Claude Code systemPromptSections) — ✅ 测试覆盖 (test_infrastructure.TestPromptCache)

## Phase 2: 语义层与知识图谱（1 周）

- [x] T012: 语义层 JSON Schema 定义 (Model/Relationship/Metric/Calculated Field + composite metrics) — ✅ 测试覆盖 (test_semantic_schema.py, 13 个)
- [x] T013: 数据源自动扫描 → 生成初始语义层 JSON — ✅ 测试覆盖 (test_semantic_scan.py, 10 个) + 端到端真库验证 (njmind 5 张业务表, 列注释修复) (AI 自动推断 + confidence 标注)
- [x] T014: 语义层 CRUD API + 版本管理 + 回滚 — ✅ 测试覆盖 (test_semantic_api.py, 13 个全 HTTP) + 端到端 e2e_scan.py 通过 (真实 PG 扫 13 表)
- [ ] T015: 语义层编辑器 UI (查看/编辑/版本历史/diff) — *延后到前端集中 phase*
- [x] T016: 知识图谱 AI 推断 (表结构推断 + 外键 + 字段命名模式) — ✅ 测试覆盖 (test_knowledge_graph.py, 13 个; name_pattern 0.6 + ai_inferred 0.7, 去重不写回)
- [x] T017: 知识图谱演化 (历史查询挖掘 + 反馈回流 → 更新 confidence) — ✅ 算法 + 单元测试覆盖 (test_knowledge_graph_evolution.py, 13 个; e2e 留 Phase 6)
- [x] T018: 复合指标支持 (子指标递归展开 + factor_metric_names，对标海泰 MetricContext) — ✅ 测试覆盖 (test_composite_metric.py, 8 个; 含 to_schema_context 序列化)

## Phase 3: RAG 检索与向量化（1 周）

- [x] T019: Milvus 表结构 + BGE-large-zh Embedding 集成 — ✅ 本地 BGE-large-zh-v1.5 (1024维, 离线) + VectorStore 抽象 (Mock/Milvus 可切) + MilvusVectorStore (HNSW+IP, JSON标量过滤); 测试 11+18+6(真实模型slow)
- [x] T020: 语义层 → 向量索引构建 (数据源接入时自动触发) — ✅ build_index (model/metric→text→embed→upsert) 挂 scan endpoint; 端到端验证 13 表索引 13 条
- [x] T021: 语义层修改 → 增量更新索引 — ✅ rebuild_index (按 data_source 删旧+建新) 挂 rollback endpoint; 5 测试
- [x] T022: 两阶段检索 (向量召回 top-20 → LLM 精筛) — ✅ retrieve (向量召回 score≥0.5 + LLM 精筛, prompt含假阳性声明, 宁缺毋滥, 无召回不fallback); 8 测试
- [x] T023: 语义缓存 (余弦相似度 > 0.95 → 复用 SQL) — ✅ SemanticCache (用 VectorStore 抽象存 question→sql, 阈值0.95, 失败降级miss); 7 测试
- [x] T024: Few-shot 历史匹配 (最多 3 条审核通过的 SQL 注入 prompt) — ✅ find_fewshot_examples (相似历史SQL召回, 阈值0.5, 最多3条) + format_fewshot_prompt; 7 测试

## Phase 3 完成 ✅ (T019-T024 全部)
- 本地 BGE-large-zh-v1.5 (1024维, 离线) embedding
- VectorStore 抽象 (Mock/Milvus 可切) + MilvusVectorStore (HNSW+IP+JSON标量过滤)
- 扫描自动建索引 (T020) + 语义层变更重建索引 (T021)
- 两阶段检索: 向量召回 top-20 + LLM 精筛 (宁缺毋滥, 假阳性声明)
- 语义缓存 (相似问题复用SQL) + Few-shot 历史匹配
- 真实 Milvus 端到端验证: 13表索引13条, 检索召回5条 (销售额→biz_orders)
- 测试 178→213 passed

## Phase 4: Agent 执行引擎（2 周）

- [ ] T025: Agent while(true) 执行循环 (生成→校验→执行→自检→修正→再生成)
- [ ] T026: 意图识别 (5 种意图 + Pydantic 强约束 + confidence 降级 + 追问维度继承)
- [ ] T027: 预思考机制 (选表理由+聚合方式+注意事项 → SSE thinking 事件)
- [ ] T028: ask_user 关键节点暂停 (Schema不确定/结果异常时触发)
- [ ] T029: SQL 生成 (白名单列名 + data_type 约束 + Skills 注入 + 多轮历史)
- [ ] T030: SQL 三层校验 (AST 拒绝非 SELECT + 危险函数拒绝 + 白名单列名校验)
- [ ] T031: SQL 执行 + 连接池管理 + 超时控制 + 大结果分块
- [ ] T032: SQL 自愈 (10+ 错误码映射 + 专项纠正 prompt + 2 轮上限 + 熔断器)
- [ ] T033: 结果自检 (0行/异常数字/不一致 → 分析 → 提示或修正)
- [ ] T034: 图表生成 (LLM 声明式 ECharts option JSON + Skills 约束 + JSON schema 校验)
- [ ] T035: 图表降级 + JSON 自愈 (LLM 生成失败 → 规则推断 + 补全括号)

## Phase 5: 对话与上下文管理（1 周）

- [ ] T036: State Store (结构化状态存储: current_tables/sql/filters/result_summary)
- [ ] T037: Relevant Recall (按需召回最多 5 条 Agent 记忆，不全量灌入 prompt)
- [ ] T038: 上下文压缩 (Token 阈值 70% 触发 + 状态补偿 + 熔断器)
- [ ] T039: 对话摘要保留 (可展开查看 + 关键决策点标注)

## Phase 6: Skills 与反馈系统（1 周）

- [ ] T040: Skills 加载 (SKILL.md 解析 + System Prompt 注入 + 热更新)
- [ ] T041: Skills 编辑器 UI (在线编辑 + 预览效果 + 版本管理)
- [ ] T042: 反馈收集 (点赞/点踩 + 改 SQL + 纠正图表 + 写评论)
- [ ] T043: 负面信号检测 (关键词匹配 + 连续点踩 → 触发反馈表单)
- [ ] T044: 反馈审核队列 (admin 审核 → 回流知识库 or 拒绝)
- [ ] T045: Agent 记忆管理 UI (查看/编辑/删除记忆 对标 Claude Code MemoryFileSelector)

## Phase 7: 前端与交付（1 周）

- [ ] T046: 聊天主界面 (SSE 流式 + 思考链展开 + 表格/图表渲染)
- [ ] T047: Agent 暂停交互 UI (确认表单 + Agent 状态展示)
- [ ] T048: 输入编排器 (arrow key history + slash command + Token 预算)
- [ ] T049: Pipeline Trace 可视化 (Agent 调用链 + 每步耗时+token+状态)
- [ ] T050: 可观测性面板 (dump-prompts 导出 + /context token 统计)
- [ ] T051: 数据源管理页面 + 看板页面
- [ ] T052: 查询历史 + 审计日志页面
- [ ] T053: 一键部署脚本 + 操作手册
- [ ] T054: 端到端测试 (50 个 QA 对准确率测试 + 跨租户数据泄露测试)

## 依赖关系

```
Phase 1 (基础设施: Checkpointer+Agent记忆+Prompt缓存) ──────────┐
  │                                                               │
  ├→ Phase 2 (语义层: 含复合指标) ──→ Phase 3 (RAG) ──→ Phase 4 (Agent: 含ask_user) ──┤
  │                                                                                    │
  └→ Phase 4 (Agent) ──→ Phase 5 (对话管理: StateStore+压缩+Relevant Recall) ──→ Phase 6 (Skills+反馈)
                                                                              │
                                                                              └→ Phase 7 (前端+交付: 含Pipeline Trace+可观测面板)

Phase 2 和 Phase 3 可以并行（语义层定义 + 向量基础设施）
Phase 5 依赖 Phase 4 的 Agent 循环（压缩+状态管理需要 Agent 执行引擎就绪）
Phase 6 依赖 Phase 4 + Phase 5（反馈依赖 Agent 执行 + 对话管理）
Phase 7 贯穿全程（前端可以随各 Phase 逐步交付）
```

## 每个 Phase 的冒烟测试

- Phase 1: 注册登录 → 创建数据源 → 扫描表结构 → 框架级 tenant_id 过滤生效 → 会话持久化可恢复 → Prompt 分层缓存生效
- Phase 2: 语义层 JSON 自动生成 → 编辑 → 版本回滚 → 复合指标(客单价=GMV/订单数)正确展开
- Phase 3: "本月销售额" → 向量检索正确召回 orders 表
- Phase 4: "本月各品类销售额" → 完整 Agent 链路(预思考→选表→SQL→自检→图表) → ask_user 暂停可用
- Phase 5: 追问"上个月呢" → 复用上下文(不重新检索 schema) → 压缩触发 → 压缩后仍正确 → Relevant Recall 召回记忆
- Phase 6: 修改 SKILL.md → 下次查询使用新规则 → 点踩 → 触发反馈表单
- Phase 7: 50 个 QA 对准确率 ≥ 70% → Pipeline Trace 可用 → 可观测面板可用