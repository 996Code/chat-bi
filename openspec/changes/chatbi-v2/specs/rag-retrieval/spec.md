# RAG Retrieval

## Status: ADDED

## 概述

对标 WrenAI 三层 RAG（Schema/语义/知识）+ 海泰 Milvus 向量检索 + Claude Code Relevant Recall（按需召回最多 5 条）。v2 用 **Milvus + BGE-large-zh Embedding** 做两阶段检索——向量召回 → LLM 精筛。

**为什么选 Milvus 而非 pgvector**：
- Milvus 专为向量检索设计,内置 Embedding Function(插入数据时自动调 Embedding API 生成向量)
- HNSW 索引在大规模数据上性能更优
- 支持标量过滤(表名精确匹配用 Milvus 标量过滤而非向量检索)
- 对标海泰: "Milvus 而非 pgvector"

**关键借鉴 — 海泰的两阶段检索经验**：
1. **向量检索后的相似度过滤**（score >= 0.5）— 太低引入假阳性，太高可能漏掉。0.5 是平衡点
2. **LLM 二次筛选的 prompt 必须明确说明"候选来自向量检索，可能存在假阳性"**
3. **宁缺毋滥策略** — vector 召回结果与问题语义不匹配时返回空，不选 score 最高的

## 需求

### RAG-001: Embedding 索引构建

- BGE-large-zh-v1.5（1024 维）。对标海泰：支持 BGE-large-zh（1024维）和 Qwen3-Embedding（4096维）两种，v2 先只用 BGE
- 索引对象：Model（表名+描述+列名+描述+data_type）、Metric（名称+公式+描述）、Calculated Field、历史优质 Question-SQL Pair
- 数据源接入时自动构建
- 语义层修改 → 增量更新索引（非全量重建）
- **对标海泰 Milvus 内置 Embedding Function**：Milvus Collection 可注册 TextEmbedding Function → 插入数据时自动调 Embedding API 生成向量 → 无需手动调用 Embedding API

**验收标准**：
- [ ] 新数据源接入后 5 分钟内完成 Embedding 索引构建
- [ ] 语义层修改后增量更新（只更新变更的 Model/Metric）
- [ ] Embedding 与 LLM 共享同一 API（对标海泰独立 Embedding 配置）

### RAG-002: 两阶段检索

**阶段 1**：向量召回 — 用户问题 Embedding → Milvus top-K 相似度检索（K=20）+ 相似度过滤（score >= 0.5）

**对标海泰**：`client.search(limit=5, anns_field="ie_description_vector") + [candidate for candidate in candidates if candidate.score >= 0.5]`

**阶段 2**：LLM 精筛 — 召回结果 + 用户问题 → LLM 选出最相关的 Models + Columns + Metrics

**LLM 精筛 prompt 关键设计（对标海泰 metric_selection.py）**：
- 必须明确告诉 LLM："候选列表来自向量检索，可能存在假阳性"
- 要求判断：语义是否真正匹配、维度与对象是否兼容
- **宁缺毋滥策略**：若无真正匹配，返回空数组 — "在 BI 场景中，返回错误数据比返回无数据更危险"

**检索失败处理**（对标 v1 教训：fallback 到前 5 张表 → 幻觉）：
- 检索无结果 → 返回 "无法匹配到相关表，请换一种问法" → **不 fallback，不随机选表**
- 对标海泰"宁缺毋滥"：向量检索无高置信结果 → 返回空 → 让 Leader 进入 CLARIFICATION

**验收标准**：
- [ ] 用户问"销售额"→ 向量检索召回含 amount/price/revenue 的列 → LLM 确认 orders.total_amount
- [ ] Schema Linking 准确率 ≥ 85%
- [ ] 检索无结果时返回友好提示，不生成随机 SQL
- [ ] LLM 精筛 prompt 包含"候选来自向量检索，可能存在假阳性"说明

### RAG-003: 语义缓存 — ⚠️ 已裁剪，不纳入 v2

**决策**：不恢复。相似问题复用 SQL 场景有限，few-shot 历史匹配（RAG-004）已覆盖核心需求。

~~**对标 Claude Code**：Session Memory 直挂（省 API 调用）~~

~~- 用户问题 Embedding 后检查向量空间中是否有相似问题（余弦相似度 > 0.95）~~
~~- 命中 → 复用 SQL 重新执行（不重新生成 SQL）~~
~~- Redis 存储 embedding 向量~~

~~**验收标准**：~~
~~- [ ] 问"本月销售额"再问"这个月的营收"→ 语义缓存命中 → 复用 SQL~~
~~- [ ] 语义缓存响应 ≤ 500ms~~

### RAG-004: 历史问题匹配

**对标 Claude Code**：Relevant Recall — scanMemoryFiles → formatMemoryManifest → 轻量模型选择
**对标海泰**：`intent_history` 是唯一跨查询持久的状态键，追问时补全上下文

查询历史中的 Question-SQL Pair（经过人工审核回流的）作为 Few-shot 示例：
- 与当前问题最相似的历史 SQL → 注入 prompt 作为参考
- 最多注入 3 条（防止 prompt token 爆炸）

**验收标准**：
- [ ] 相似问题在历史中有人审核通过的 SQL → 注入 prompt 作为参考
- [ ] 最多注入 3 条 Few-shot 示例

### RAG-005: 精确查询表/列元数据（对标海泰 table_service + column_service）

**对标海泰**：表名精确匹配用 Milvus 标量过滤（非向量检索），列信息批量查询（避免 N+1）

- 表名精确匹配：SQL 中提取的表名 → 直接在 Milvus 标量字段中精确查询（`filter='table_schema=="xxx" and name=="yyy"'`），不是向量检索
- 列信息批量查询：一次查询所有涉及表的所有列，Python 端按 table_id 分组
- **data_type 字段必须在检索结果中返回**（对标海泰 data_type 约束：SQL 生成时防止对 VARCHAR 做 SUM、对 DATE 做数值计算）

**验收标准**：
- [ ] 列信息一次批量查询，不逐表查询（避免 N+1）
- [ ] data_type 字段随检索结果返回，供 SQL Agent 做类型约束