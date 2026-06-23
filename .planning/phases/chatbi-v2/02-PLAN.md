# Phase 2 计划：语义层与知识图谱

> 从 OpenSpec spec（SEM-001~006）转化。按依赖分 4 个 Wave。
> 配套上下文见 `02-CONTEXT.md`。执行用 `/ai:do`，验收用 `/ai:check`。

## Wave 1：语义层定义（地基，零依赖）

### T012 语义层 JSON Schema 定义 — `type: tdd`

**read_first**: `openspec/changes/chatbi-v2/specs/semantic-layer/spec.md`（SEM-002 JSON 示例）、`doc/chatbi-v2/design.md`（复合指标展开）

**实现位置**: `backend/app/schemas/semantic_layer.py`（新建，`schemas/` 目前是空目录）

**Red** — 先写失败测试（`backend/tests/test_semantic_schema.py`）：
- Pydantic 模型能解析 SEM-002 的 JSON 示例（orders/gmv/客单价/relationship）
- 非法数据被拒（confidence 超出 0-1、metric.type 不是 single/composite）
- JSON Schema 可导出（`model_json_schema()`）

**Green** — 用 Pydantic v2 定义：
```
SemanticModelContent (顶层): version, models: list[Model]
Model: name, display_name, description, source, confidence,
       columns: list[Column], relationships: list[Relationship],
       metrics: list[Metric], calculated_fields: list[CalculatedField]
Column: name, display_name, data_type, semantic_type, description, source, confidence
Relationship: name, target_model, join_type, on, type(N:1/1:N/N:N), source, confidence
Metric: name, display_name, formula, condition?, description?,
        type(single/composite), factor_metric_names?(composite时必填)
CalculatedField: name, display_name, formula
```
- `confidence` 用 `Field(ge=0, le=1)`
- `metric.type="composite"` 时 `factor_metric_names` 必填（Pydantic 校验）

**Refactor**: 提取 `source` 和 `confidence` 到可选 mixin（减少重复），保持 spec 的字段名 1:1。

**acceptance_criteria**（来自 SEM-002）:
- [ ] 模型能完整表示 SEM-002 JSON 示例
- [ ] composite metric 必须有 factor_metric_names，否则校验失败
- [ ] confidence 超范围被拒

---

## Wave 2：扫描生成 + CRUD API（依赖 Wave 1）

### T013 数据源自动扫描 → 初始语义层 — `type: tdd`

**read_first**: `02-CONTEXT.md`、`backend/app/db/models.py`（DataSource/SemanticModel）、经验教训 `#15 元数据质量`

**Red** — `backend/tests/test_semantic_scan.py`：
- mock SQLAlchemy inspector → 扫描出表/列/外键 → 组装成 `SemanticModelContent`
- 外键 → Relationship（source="foreign_key", confidence=1.0）
- 普通列 → Column（source="manual"），LLM 补充部分 → source="auto_inferred"
- LLM 中文推断函数可被 mock（不真实调用），验证 source/confidence 标注正确

**Green** — `backend/app/services/semantic_scanner.py`（新建）：
- `scan_data_source(engine) -> SemanticModelContent`：用 `inspect(engine)` 取 tables/columns/foreign_keys
- `_infer_chinese_semantics(columns) -> dict`：调 LLM 批量推断 display_name/description（可 mock）
- 外键命名模式推断（`xxx_id → xxx` 表）补充隐式关系（confidence=0.6, source="name_pattern"）

**acceptance_criteria**（SEM-001）:
- [ ] 扫描结果含 Model + Column + Relationship + Metric（初始可为空）
- [ ] AI 推断字段标 source="auto_inferred" + confidence
- [ ] LLM 调用可独立测试（不强制真实调用）

> 注：冒烟测试"5 分钟内生成"需真实数据源，放 `/ai:check` 集成验证。

### T014 语义层 CRUD API + 版本管理 + 回滚 — `type: tdd`

**read_first**: `backend/app/core/auth.py`（require_admin/get_current_user）、`models.py`（SemanticModel）、SEM-004

**Red** — `backend/tests/test_semantic_api.py`（用 conftest 的 app + db_session）：
- `POST /semantic-models`（admin）→ 创建 v1
- `PUT` 修改 → 生成 v2，v1 is_current=False
- `GET ?data_source_id=` → 返回 is_current=True 版本
- `POST /{id}/rollback` → 复制目标版本为新 version + is_current=True
- 非 admin → 403（复用 require_admin，顺带补 T008 审计测试）
- 多租户：tenant A 看不到 tenant B 的语义层（**顺带补 T006 多租户测试**）

**Green** — `backend/app/api/semantic_models.py`（新建路由）+ `schemas/semantic_layer.py` 加 CRUD DTO：
- 版本管理：写时 version=max+1，旧版本 is_current=False（事务内）
- 回滚：读目标版本 content → 写新版本（不改旧内容，append-only）
- 每个写操作调 `write_audit_log`（补 T008 测试覆盖）

**acceptance_criteria**（SEM-004）:
- [ ] 修改 display_name → version 自增
- [ ] 可回滚到历史版本
- [ ] CRUD 受 RBAC + 多租户隔离

> **顺带清遗留债**：T006（多租户隔离）+ T008（审计日志）的单元测试在这里补——它们天然需要 HTTP 层 + 认证中间件，借 T014 的 API 一起测最自然（见 01-PLAN.md 遗留债建议）。

---

## Wave 3：复合指标（依赖 Wave 1 的 T012，可与 Wave 2 并行）

### T018 复合指标支持 — `type: tdd`

**read_first**: `doc/海泰ChatBI完整代码分析.md`（MetricContext 递归）、SEM-005、design.md 复合指标展开

**Red** — `backend/tests/test_composite_metric.py`：
- 给定"客单价 = gmv / order_count"（type=composite, factor_metric_names=["gmv","order_count"]）
- `expand_composite_metric(metric, all_metrics) -> ExpandedMetric`：
  - 取出 gmv（single: SUM(total_amount) WHERE status IN ('paid','shipped')）
  - 取出 order_count（single: COUNT(id)）
  - 组装 calc_expression = "gmv / order_count"
- composite 的子指标是 composite → 抛错（不支持嵌套，SEM-005）
- factor_metric_names 指向不存在的 metric → 抛错

**Green** — `backend/app/services/metric_expander.py`（新建）：
- `expand_composite_metric(name, metrics) -> ExpandedMetric`
- 返回 `table_full_names`（去重合并子指标的表，对标海泰 build_metric_context）

**acceptance_criteria**（SEM-005）:
- [ ] "客单价"自动展开 gmv + order_count 子指标 SQL
- [ ] 不支持嵌套复合（子指标是 composite → 报错，不是静默）
- [ ] factor 指向不存在的 metric → 明确报错（宁缺毋滥）

---

## Wave 4：前端 + 知识图谱 AI 推断（依赖 Wave 2/3）

### T015 语义层编辑器 UI — `type: implement`

**read_first**: `frontend/src/api/client.ts`、`frontend/src/views/ChatView.vue`、SEM-002/004

**实现**: `frontend/src/views/SemanticModelView.vue`（新建）+ `frontend/src/api/semantic.ts`：
- 查看：树形展示 models → columns/metrics/relationships，source/confidence 用颜色标注
- 编辑：表单编辑 display_name/description/formula
- 版本历史：版本列表 + diff（简化：展示 JSON 变更）
- 调 T014 的 API

**acceptance_criteria**（SEM-002/004 前端部分）:
- [ ] 能查看当前语义层
- [ ] 能编辑并保存（触发新版本）
- [ ] 能查看版本历史

### T016 知识图谱 AI 推断 — `type: implement`

**read_first**: SEM-003、`doc/Claude-Code-源码深度解读.md`（Memory 治理 + confidence）

**实现**: `backend/app/services/knowledge_graph.py`（新建）：
- `_infer_relationships_by_name(columns, tables)`：`xxx_id → xxx` 模式 + 同名字段 → 隐式关系（confidence=0.6, source="name_pattern"）
- `_infer_relationships_by_llm(...)`：LLM 判断表间关系（confidence=0.7, source="ai_inferred"）
- 结果**只返回建议**，不自动写入语义层（需人工审核）

**acceptance_criteria**（SEM-003）:
- [ ] `user_id` 字段 → 推断出 users 表关系
- [ ] 推断结果标注 source + confidence
- [ ] 不自动写回（人工审核后才进语义层）

### T017 知识图谱演化 — `type: implement`

**read_first**: SEM-003 第 2/3 条、`models.py`（AuditLog/Feedback/SavedQuery 可挖掘）

**实现**: `backend/app/services/knowledge_graph.py` 加：
- `mine_implicit_relationships(query_history)`：扫 SavedQuery.sql_text → 频繁 JOIN 的表对 → 提升关系 confidence
- `apply_feedback_signals(feedback)`：用户纠正过的表 → 降低该表 confidence；赞过的 → 提升
- 同样**只产出建议**，审核后回流

**acceptance_criteria**（SEM-003）:
- [ ] 频繁 JOIN 的表对 → confidence 提升
- [ ] 被多次纠正的表 → confidence 下降

> 注：T016/T017 的完整验收（"被多次纠正降低优先级"）需要 Phase 6 反馈系统数据，本 Phase 只实现算法 + 单元测试，端到端验证留到 Phase 6。

## 依赖图

```
Wave 1: T012 (schema 定义)
         │
         ├──→ Wave 2: T013 (扫描) ──→ T014 (CRUD+版本)
         │                              └──→ 顺带补 T006/T008 测试（清遗留债）
         │
         └──→ Wave 3: T018 (复合指标，可与 T013 并行)

Wave 4: T015 (前端，依赖 T014 API)
        T016 (知识图谱推断，依赖 T013 的扫描结果)
        T017 (演化，依赖 T016 + Phase 6 数据，算法先行)
```

## Phase 2 冒烟测试（来自 tasks.md）

- [ ] 语义层 JSON 自动生成（接真实数据源扫描）
- [ ] 编辑 → 版本回滚
- [ ] 复合指标（客单价=GMV/订单数）正确展开

## 进度追踪

执行时用 `openspec list` 查看进度，`/ai:do` 推进单个 task，完成后勾选 `openspec/changes/chatbi-v2/tasks.md`。
