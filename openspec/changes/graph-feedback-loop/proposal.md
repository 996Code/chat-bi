## Why

知识图谱的关系 confidence 目前只在**数据源扫描时**一次性写入（外键=1.0 / name_pattern=0.6 / LLM 推断=0.7），运行时**从不更新**——系统用得越多，图谱却不会变准。

而 `mine_implicit_relationships`（`knowledge_graph.py:315-370`）和 `apply_feedback_signals`（`knowledge_graph.py:373-420`）两个反哺算法**已实现且有 13 个单测**，却因 `knowledge_graph.py:313` 注释所说"无生产调用方"成了**死代码**。成功查询里的表共现、JOIN 路径这些高质量隐式信号白白流失。

本变更把这些已就绪的算法接入主管线，让图谱"越用越准"。

## What Changes

- **新增**：成功查询后，自动挖掘 SQL 里的表共现关系，对已存在的关系提升 confidence（复用 `mine_implicit_relationships`）
- **新增**：`apply_confidence_updates` helper——只改已存在 Relationship 的 confidence 数值并升 SemanticModel 版本（仿现有 `_apply_inferred_relationships` 写法）
- **新增**：节流机制——避免每次成功查询都写新版本导致版本爆炸（独立 `relationship_feedback` 表累计 + 阈值/定时批量合并）
- **修改**：`chat_stream.py:_persist` 在现有反哺点（SavedQuery/fewshot/记忆，line 774-845）之后，并列插入图谱反哺块
- **扩展**：`mine_implicit_relationships` 支持发现**全新表对**（当前 line 346-348 只 boost 已知关系，共现再多也忽略新对）——共现超阈值且不在已知关系时，触发单对关系推断后加入

### 非目标（明确不做）

- ❌ **不做显式用户纠错/点赞按钮**——用户发现表选错的第一反应是换说法重问，且已有 ask_user 主动确认机制；显式纠错是冗余交互。只用隐式信号（成功查询自动挖掘）。
- ❌ **不做 `apply_feedback_signals` 的显式信号路径接入**——该函数的 corrections/praises 入参无供给方，本变更只接 `mine_implicit_relationships`。
- ❌ **不改 SchemaGraph 的运行时构建逻辑**——只改它消费的 confidence 数据源。
- ❌ **不做置信度衰减**——confidence 只升不降（降需要纠错信号，属未来方向5）。

## Capabilities

### New Capabilities

- `graph-feedback-loop`: 成功查询反哺知识图谱的闭环——从查询 SQL 挖掘表共现信号，节流累计，批量合并回 SemanticModel 的 Relationship.confidence，让图谱越用越准。

### Modified Capabilities

（无——本变更不修改已有 spec 的 requirement，只新增能力。`graph-middleware` change 的 schema-graph capability 描述的是图谱构建/查询，反哺是新能力。）

## Impact

- **代码**：
  - `backend/app/services/knowledge_graph.py` — 扩展 `mine_implicit_relationships`（支持新表对）+ 新增 `apply_confidence_updates`
  - `backend/app/api/chat_stream.py` — `_persist`（line 842 后）插入反哺块
  - `backend/app/api/data_sources.py` — 复用 `_apply_inferred_relationships` 的版本化写入模式
  - 新增 `backend/app/db/models.py` 的 `RelationshipFeedback` 表（节流累计用）
  - `backend/app/core/config.py` — 节流阈值/批量合并参数
- **数据**：SemanticModel 版本数会随反哺增加（受节流控制）；新增 relationship_feedback 表
- **依赖**：无新外部依赖；复用现有 StateStore/SemanticModel/knowledge_graph 基建
- **测试**：扩展现有 `test_knowledge_graph_evolution.py`（13 个纯算法单测），补 e2e 接入测试
