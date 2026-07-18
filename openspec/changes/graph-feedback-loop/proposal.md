## Why

系统当前有两个独立的"经验沉淀"机制，各自为政且都有缺口：

1. **记忆功能**（`recall.py` + `agent_memory.py`）：成功查询后 LLM 自主提炼自然语言经验（`chat_stream.py:819-842`），存为 markdown，有完整的抽取-存储-整合（`consolidate_memories`）-召回闭环。但只存"语义经验"（如"GMV 用 actual_amount"），**不沉淀执行链路**（表共现、JOIN 路径、典型 SQL 模式）。

2. **知识图谱 confidence**：只在数据源扫描时写入，运行时从不更新。`mine_implicit_relationships` 等反哺算法已实现且有 13 单测（`knowledge_graph.py:315-420`），却是零调用方的**死代码**。

两个机制重复造了"存储+整合"的轮子，且各自单薄。本变更**把两者集成为一体**：记忆承载完整的执行链路经验，手动整理时汇总更新到图谱 confidence。

## What Changes

- **扩展记忆内容**：成功查询后，除现有自然语言经验外，额外沉淀**执行链路经验**——表共现、JOIN 路径、典型 SQL 模式、查询场景。用结构化 markdown 存（人可读、可编辑）。
- **复用记忆基建**：链路经验走已有的 `extract → save_memory → consolidate_memories → recall_memories` 闭环，**不新建 DB 表、不写新定时任务**。
- **手动整理触发图谱汇总**：在已有的 `/memory/consolidate` 动作里追加一步——从链路经验里汇总表共现统计，调 `apply_confidence_updates` 更新 SemanticModel 的 Relationship.confidence（写新版本）。
- **复用已实现算法**：`mine_implicit_relationships`（汇总共现→boost 建议）+ 新增 `apply_confidence_updates`（写回 confidence）。
- **召回双路**：链路经验的自然语言部分注入 prompt（复用 `recall_memories`），结构化部分经图谱 confidence 影响 JOIN 路径计算。

### 非目标（明确不做）

- ❌ **不新建 `relationship_feedback` DB 表**——原方案的错误，复用记忆文件存储。
- ❌ **不写新的定时合并任务**——复用已有 `/memory/consolidate`（手动/半自动触发），不自动跑。
- ❌ **不做实时图谱同步**——整理时才汇总更新，最终一致即可（避免每次查询都写 SemanticModel 版本爆炸）。
- ❌ **不做显式用户纠错按钮**——只用隐式信号（成功查询自动沉淀）。
- ❌ **不做 confidence 衰减**——只升不降。

## Capabilities

### New Capabilities

- `memory-graph-integration`: 记忆与图谱的集成闭环——记忆承载执行链路经验（表共现/JOIN/SQL模式），手动整理时汇总更新图谱 confidence，让两个经验机制合一。

### Modified Capabilities

（无——本变更不改已有 spec requirement，只新增能力。记忆的 consolidate 动作是行为扩展不是 requirement 变更。）

## Impact

- **代码**：
  - `backend/app/ai/recall.py` — `extract_memory_from_turn` 扩展抽取链路经验（表共现/JOIN）；新增链路经验格式化
  - `backend/app/api/memory.py` — `/memory/consolidate` 整理后追加"汇总→更新图谱"步骤
  - `backend/app/services/knowledge_graph.py` — 新增 `apply_confidence_updates`；`mine_implicit_relationships` 从记忆读取而非入参
  - `backend/app/core/agent_memory.py` — 记忆文件支持 `memory_type="linkage"` 标记链路经验
- **数据**：记忆目录新增 linkage 类记忆文件；SemanticModel 版本在整理时递增（受手动触发控制，不爆炸）
- **依赖**：无新外部依赖；完全复用现有记忆+图谱基建
- **测试**：扩展 `test_knowledge_graph_evolution.py` + `test_recall.py`（如有），补集成测试
