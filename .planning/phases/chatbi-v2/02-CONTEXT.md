# Phase 2 上下文：语义层与知识图谱

## 需求来源

OpenSpec change：`chatbi-v2`
规格：`openspec/changes/chatbi-v2/specs/semantic-layer/spec.md`（SEM-001 ~ SEM-006）
设计：`doc/chatbi-v2/design.md`（State Store 结构 + 复合指标展开 + Skills 分层）

## 任务范围（T012–T018）

- T012 语义层 JSON Schema 定义
- T013 数据源自动扫描 → 生成初始语义层 JSON
- T014 语义层 CRUD API + 版本管理 + 回滚
- T015 语义层编辑器 UI
- T016 知识图谱 AI 推断（表结构/外键/命名模式）
- T017 知识图谱演化（历史查询挖掘 + 反馈回流）
- T018 复合指标支持（递归展开 + factor_metric_names）

## 决策

### 技术选型

| 维度 | 选择 | 依据 |
|------|------|------|
| Schema 定义 | Pydantic v2（已装 2.13） | 强类型 + JSON Schema 导出 + 校验，比手写 dict 安全 |
| 表结构扫描 | SQLAlchemy `inspect()` | 已验证可用，跨方言（PG/MySQL），不依赖原生 SQL |
| 语义层存储 | 复用已有 `SemanticModel` 表（content JSON + version + is_current） | Phase 1 已建表，无需改 schema |
| AI 推断 | 复用已有 LLM 配置（讯飞 MAAS）+ `prompt_cache` | 配置已就位 |
| 中文语义推断 | LLM 批量推断（字段名+注释+类型 → 中文 display_name/description） | 对标 SEM-001 第 2 步 |
| 前端编辑器 | Vue3 + 表单（查看/编辑/版本历史） | 复用已有 api client（JWT 拦截器） |

### 实现方式（对照 design.md + 海泰分析）

1. **T012 先行**（地基）：用 Pydantic 定义 `SemanticModelContent`（顶层）含 `models: list[Model]`，每个 Model 含 `columns/relationships/metrics/calculated_fields`。每个字段都带 `source`（auto_inferred/manual/foreign_key 等）+ `confidence`（0-1）。这是 Phase 2-4 的共同数据契约。
2. **T013 扫描**：`SQLAlchemy inspect(engine)` 拿表/列/外键 → 组装成 Model（source=manual，confidence=1.0 for 外键）→ LLM 批量补中文语义（source=auto_inferred，confidence 标注）→ 写入 SemanticModel.content。
3. **T014 CRUD + 版本**：每次写新版本（version+1，旧版本 is_current=False），is_current 唯一约束。回滚 = 把目标版本复制成新 version。**不改旧版本内容**（append-only，对标版本管理最佳实践）。
4. **T018 复合指标**：查询"客单价" → 检测 type=composite → 按 factor_metric_names 取子指标 formula → 拼装。**子指标只能是 single**（SEM-005 明确：不支持嵌套复合，对标海泰，避免无限递归）。

### 不做的事

- ❌ 不做嵌套复合指标（SEM-005 明确对标海泰限制——这是有意选择，别自作主张加递归）
- ❌ T016/T017 的 AI 推断不自动写回语义层（必须经人工审核，对标"反馈回流审核后回流"决策）
- ❌ 本 Phase 不做向量索引（那是 Phase 3 T020）—— T013 只生成 JSON，不建 Milvus 索引
- ❌ 本 Phase 不做 SQL 生成（那是 Phase 4）—— 语义层只是数据定义

## 规格参考

- `openspec/changes/chatbi-v2/specs/semantic-layer/spec.md` — SEM-001~006 全部 SHALL 语句
- `doc/chatbi-v2/design.md` — 复合指标展开示例 + State Store 结构（schema_context 字段）
- `doc/海泰ChatBI完整代码分析.md` — MetricContext metric_type single/composite + factor_ie_ids

## 关键约束（来自经验来源）

- **#15 元数据质量是准确率根本**：T013 扫描必须为表/列补中文描述，空描述 = LLM 只能猜 = SQL 错
- **#29 宁缺毋滥**（SEM-006）：本 Phase 不直接涉及检索，但 T014 CRUD 要保证语义层结构校验严格
- **SEM-002 的 source/confidence 双标注**：每个推断字段必须标 `source` + `confidence`，方便人工复核优先级（SEM-001 验收标准）
