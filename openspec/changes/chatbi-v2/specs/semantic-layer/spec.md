# Semantic Layer

## Status: ADDED

## 概述

语义层是 ChatBI v2 的核心基础设施。对标 WrenAI MDL + 海泰 ContextKey 枚举 + Claude Code Prompt 分层缓存。AI 自动扫描表结构推断语义，人工只需校验修正。

## 需求

### SEM-001: 自动扫描 + 初始语义层生成

数据源接入时：
1. 自动扫描表结构（表名、字段名、类型、注释、外键关系）
2. AI 自动推断列的中文语义（基于字段名 + 注释 + 同类型列的通用命名）
3. AI 自动推断实体关系（基于外键 + 表名模式 + 同名字段）
4. 生成初始语义层 JSON（Model + Relationship + Metric + Calculated Field 四部分）

**验收标准**：
- [ ] 新数据源接入后 5 分钟内生成初始语义层 JSON
- [ ] AI 推断的"可能是"和"确定是"有明确标注（方便人工复核优先级）
- [ ] 语义层 JSON 可人工编辑（非只读）

### SEM-002: 语义层 JSON Schema

```json
{
  "version": 1,
  "models": [{
    "name": "orders",
    "display_name": "订单表",
    "description": "存储所有订单信息",
    "source": "auto_inferred",
    "confidence": 0.85,
    "columns": [{
      "name": "total_amount",
      "display_name": "订单金额",
      "data_type": "DECIMAL(10,2)",
      "semantic_type": "measure",
      "description": "订单总金额（含税）",
      "source": "auto_inferred"
    }],
    "relationships": [{
      "name": "orders_to_users",
      "target_model": "users",
      "join_type": "LEFT",
      "on": "orders.user_id = users.id",
      "type": "N:1",
      "source": "foreign_key",
      "confidence": 1.0
    }],
    "metrics": [{
      "name": "order_count",
      "display_name": "订单数",
      "formula": "COUNT(id)",
      "description": "总订单数量"
    }, {
      "name": "gmv",
      "display_name": "GMV",
      "formula": "SUM(total_amount)",
      "condition": "status IN ('paid', 'shipped')",
      "description": "已支付+已发货的订单金额总和",
      "type": "single"
    }, {
      "name": "avg_order_value",
      "display_name": "客单价",
      "formula": "gmv / order_count",
      "type": "composite",
      "factor_metric_names": ["gmv", "order_count"]
    }],
    "calculated_fields": [{
      "name": "avg_item_price",
      "display_name": "平均单价",
      "formula": "total_amount / item_count"
    }]
  }]
}
```

**验收标准**：
- [ ] 定义了 "GMV = SUM(payment_amount) WHERE status='paid'" 后，用户问"GMV"直接用预定义
- [ ] 定义了 orders.user_id → users.id 关系后，问"每个用户的订单数"自动 JOIN
- [ ] 语义层修改后下次查询立即生效（不需重启服务）

### SEM-003: 知识图谱 AI 推断

**对标 Claude Code**：Memory 文件化 + Relevant Recall（按需召回）+ 反馈学习

三层知识获取：
1. **表结构推断**：基于外键、字段命名模式（xxx_id → xxx 表）、同名字段 → 自动推断实体关系
2. **历史查询挖掘**：分析 SQL 查询历史 → 频繁 JOIN 的表 → 自动补充隐式关系
3. **用户反馈演化**：用户纠正过的表选择 → 降低错误关系的 confidence；赞过的 SQL → 增强正面的关联关系

所有 AI 推断的关系标注 `source` 和 `confidence`，人工可校验和修正。

**验收标准**：
- [ ] 同一对表（orders + products）被频繁 JOIN → 自动补充为高置信关系
- [ ] 用户多次纠正"不要用 sales 表用 orders 表"→ 降低 sales 表的推荐优先级
- [ ] AI 推断的关系在语义层编辑器中标注来源和置信度

### SEM-004: 语义层版本管理

- 每次修改生成新版本号
- 支持回滚到历史版本
- 结构对标 Claude Code Agent Memory Snapshot：记录 updatedAt + syncedFrom

**验收标准**：
- [ ] 修改 Model 的 display_name → 版本号自动递增
- [ ] 可回滚到上一版本
- [ ] 版本详情展示变更前后对比（diff）

### SEM-005: 复合指标支持（对标海泰 MetricContext 递归结构）

**对标海泰**：`MetricContext.metric_type: "single" | "composite"` + `sub_metrics: List[MetricContext]`

复合指标由子指标组合计算：
- 例如：客单价 = GMV / 订单数
- `factor_metric_names` 指向子指标名称 → 查询时自动展开获取子指标 SQL
- 复合指标的子指标只能是单一指标（不支持嵌套复合指标，对标海泰限制：避免无限递归）

**验收标准**：
- [ ] 定义"客单价 = GMV / 订单数"→ 查询"客单价"时自动展开子指标 SQL
- [ ] 复合指标修改后自动级联更新引用它的指标

### SEM-006: 宁缺毋滥策略（对标海泰 metric_selection 空返回）

**对标海泰**：`若无真正匹配，返回空数组` — "在 BI 场景中，返回错误数据比返回无数据更危险"

语义层检索或指标匹配失败时：
- 不 fallback 到"最接近的"表或指标
- 返回明确提示："未找到匹配的指标/表，请换一种问法"
- 不强制 LLM 生成 SQL（对标 v1 教训：prompt 里写了"不要返回空"）

**验收标准**：
- [ ] 问了一个完全不相关的问题 → 返回"我无法将您的问题匹配到数据库中的表"
- [ ] 不返回随机编造的 SQL