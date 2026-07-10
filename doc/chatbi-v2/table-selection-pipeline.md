# 表选择管线：从用户问题到 SQL 生成的 Schema 投喂

> 本文档描述 ChatBI 如何从 50+ 张表中，逐步筛选出与用户问题相关的 2~5 张表，
> 并将其 schema 信息注入 LLM prompt 用于 SQL 生成。

## 核心原则

**不给 LLM 看全部表**。原因：
1. **Token 成本**：50+ 张表 × 平均 15 列 ≈ 750 列描述，约 3000+ token，白白消耗上下文窗口
2. **选错概率**：表越多，LLM 越容易 JOIN 不相关的表或选错字段
3. **安全边界**：缩小可见 schema 范围 = 缩小攻击面（LLM 无法生成查询未授权表的 SQL）

## 四步漏斗

```
用户问题: "评分低于四星的滞销商品清单及当前可用库存"
     │
     ▼
① 向量检索 (retriever.py :: retrieve)
   embed(问题) → Milvus top-K 召回 + score ≥ 0.5 过滤
   输入: 自然语言问题
   输出: 5~20 个候选表 (含假阳性)
   例: pd_products(0.82), pd_product_skus(0.71), pd_sku_stocks(0.68),
       pd_categories(0.55), pd_product_reviews(0.52)
     │
     ▼
② LLM 精筛 (retriever.py :: _llm_refine)
   把召回候选 + 问题交给 LLM 判断语义是否真正匹配
   关键 prompt 策略: "候选可能存在假阳性，宁缺毋滥，无真匹配返回空"
   输入: 向量召回的候选表列表 + 用户问题
   输出: 1~5 张语义匹配的表
   例: pd_products, pd_product_skus, pd_sku_stocks
     │
     ▼
③ 关系扩展 (schema_utils.py :: expand_with_relationships)
   沿语义层关系图谱 BFS 2 跳，补进 JOIN 需要的关联表
   设计依据: 用户问 pd_products 但可能需要 JOIN pd_product_reviews
   输入: 精筛后的表名 + 语义层关系定义
   输出: 精筛表 + 关联表 (最多 2 跳)
   例: +pd_product_reviews (pd_products → pd_product_reviews 的关系)
     │
     ▼
④ 构建 schema_context (schema_utils.py :: build_schema_context)
   只用筛选后的表构建 schema 文本，注入 SQL 生成 prompt
   格式: biz_orders(订单表): id[BIGINT] user_id[BIGINT] total_amount[DECIMAL] →biz_users(user_id)
   输入: 扩展后的表名列表 + 语义层完整列定义
   输出: 仅选中表的列名 + data_type + 关系提示
   例: 只有 4 张表的列定义进入 prompt，而非全部 50+ 张
```

## 各步骤详解

### ① 向量检索

**代码**: `backend/app/services/retriever.py :: retrieve()`

流程:
1. 将用户问题用 BGE-large-zh-v1.5 编码为 1024 维向量
2. 在 Milvus 中搜索 top-K（默认 20）个最相似的语义层条目
3. 用 `score_threshold`（默认 0.5）过滤低分候选

关键配置（`app/core/config.py`）:
```python
rag_vector_top_k: int = 20          # 向量召回数量
rag_similarity_threshold: float = 0.5  # 最低相似度阈值
```

召回的每个候选包含: `name`（表名）、`type`（model/metric）、`score`（相似度）、`text`（描述）

**为什么不用关键词匹配**: 用户说"滞销商品"，但表名叫 `pd_products`，没有"滞销"二字。语义向量能捕捉这种隐含关联。

### ② LLM 精筛

**代码**: `backend/app/services/retriever.py :: _llm_refine()`

向量检索召回的候选可能包含假阳性（score 刚过阈值但语义不相关）。LLM 精筛用一次轻量 LLM 调用来判断真正相关的表。

Prompt 核心策略:
- 声明候选来自向量检索，可能存在假阳性
- 要求判断语义是否真正匹配
- **宁缺毋滥**: 无真匹配返回空数组（在 BI 场景，返回错误数据比返回无数据更危险）
- 返回格式: `{"models": ["匹配的name列表"], "reason": "简短理由"}`

降级策略: LLM 精筛失败 → 返回原始向量召回结果（标记 `degraded=True`），不阻塞主流程。

### ③ 关系扩展

**代码**: `backend/app/ai/schema_utils.py :: expand_with_relationships()`

问题场景: 用户问"商品评分"，检索只命中 `pd_products`，但生成 SQL 时需要 JOIN `pd_product_reviews` 才能拿到评分。如果 schema_context 里没有 `pd_product_reviews`，LLM 就看不到评分字段。

解决: 沿语义层的关系定义（`Relationship`）做 BFS 双向遍历，2 跳以内补进关联表。

关系来源:
- 外键关系: `_scan_relationships()` 自动发现
- LLM 推断: `knowledge_graph.py` 由 LLM 从表名/列名推断
- 两者合并写入语义层的 `relationships` 字段

关键设计:
- **双向图**: 正向 `pd_products → pd_product_reviews` + 反向 `pd_product_reviews → pd_products`
- **限深 2 跳**: 防止 A→B→C→D 全库扩散
- **用结构化数据**: 从语义层的 `relationships` 字段读取，不硬编码命名规则

### ④ schema_context 构建

**代码**: `backend/app/ai/schema_utils.py :: build_schema_context()`

最终产出注入 SQL 生成 prompt 的文本，格式示例:
```
pd_products(商品表): id[BIGINT] name[VARCHAR] avg_rating[DECIMAL] sales_count[INTEGER] status[INTEGER] →pd_product_reviews(id)
pd_product_reviews(商品评价表): id[BIGINT] product_id[BIGINT] rating[INTEGER] →pd_products(product_id)
pd_product_skus(SKU表): id[BIGINT] product_id[BIGINT] →pd_products(id) →pd_sku_stocks(sku_id)
pd_sku_stocks(库存表): id[BIGINT] sku_id[BIGINT] available_stock[INTEGER]
```

每行包含:
- 表名 + 中文显示名
- 列名 + 中文别名 + data_type 约束
- 关系提示（`→目标表(关联列)`）

## 两条补充机制

### 追问表继承

**代码**: `backend/app/ai/chat_utils.py :: inherit_prev_tables()`

追问时（如"年度呢？"），向量检索可能只命中 `biz_orders`，遗漏上轮用到的 `mkt_group_buy_items`。继承机制: `当前检索结果 ∪ 上轮使用的表`，确保追问不丢表。

### schema_context 降级

**代码**: `backend/app/ai/chat_utils.py :: build_schema_context_fallback()`

如果 `build_schema_context()` 返回空（语义层结构异常），降级从向量检索的原始文本构建 schema context。质量不如语义层，但保证不阻塞。

## schema_context vs allowed_columns

| | schema_context | allowed_columns |
|--|--|--|
| **用途** | 注入 LLM prompt（SQL 生成的输入） | SQL 安全校验（防 LLM 幻觉列名） |
| **范围** | 只有筛选后的表 | 全部表（全量白名单） |
| **给谁看** | LLM | 后端 `sql_validator.py` |
| **为什么不同** | 给 LLM 看全部表会选错 + 浪费 token | 校验必须覆盖全表，否则 LLM 生成的合法列名被误拒 |

**设计决策**: `extract_allowed_columns()` 不传 `model_names`，取全表列名。这是有意为之 —— `allowed_columns` 的角色是安全校验的"白名单"，不是"LLM 可见范围"。如果只取选中表的列做白名单，LLM 生成的 SQL 里引用了语义层定义中存在但未被检索命中的列时，会被误判为非法列而拒绝执行。

## 完整数据流

```
用户问题
  │
  ├─[intent]──→ 意图识别 (TEXT_TO_SQL / GENERAL / CHART_MODIFY)
  │
  ├─[retrieve]──→ 向量检索 top-20 → LLM 精筛 → 2~5 张表
  │
  ├─[inherit]──→ + 追问时继承上轮表
  │
  ├─[expand]──→ + 关系图谱 2 跳扩展关联表
  │
  ├─[build_context]──→ 只用筛选后表的列定义构建 schema_context
  │
  ├─[allowed_columns]──→ 全表列名白名单 (安全校验用)
  │
  └─[generate_sql]──→ schema_context + Skills + Memory + fewshot + thinking_hint
                       → LLM 生成 SQL
                       → 白名单校验 → 执行 → 自愈循环 → 结果
```
