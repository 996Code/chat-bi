---
name: "AI: Spec"
description: "需求定义 — OpenSpec 驱动，产出规格文件并自动桥接到 GSD"
category: Workflow
tags: [ai-workflow, spec, openspec, gsd]
---

# /ai:spec — 需求定义

将需求定义的完整流程封装为一步：先用 OpenSpec 的 spec-driven 方法论生成严谨的规格文件，再自动桥接到 GSD 的 phase 结构中。

**输入**: `$ARGUMENTS` 为功能名称（kebab-case）或一段需求描述。

---

## 第 1 步：理解需求

如果 `$ARGUMENTS` 为空，用 AskUserQuestion 工具问：
> "你要做什么？描述你想构建或修改的功能。"

从描述中推导一个 kebab-case 名称（如 "用户认证" → `add-user-auth`）。

**重要**：在没有理解用户想构建什么之前，不要继续。

---

## 第 2 步：调用 OpenSpec Propose

使用 Skill 工具调用 `openspec-propose`，参数为上面确定的名称。

这会生成以下文件：
- `openspec/changes/<name>/proposal.md` — 为什么要做、目标、非目标
- `openspec/changes/<name>/specs/<capability>/spec.md` — 增量规格（ADDED/MODIFIED/REMOVED）
- `openspec/changes/<name>/design.md` — 最小方案、取舍、风险
- `openspec/changes/<name>/tasks.md` — 可执行任务列表

---

## 第 3 步：自动桥接到 GSD

OpenSpec 完成后，自动检查并初始化 GSD 结构。**不再要求用户手动运行 `/gsd-new-project`**。

### 3.1 检查 GSD 是否已初始化

```bash
if [ -f ".planning/ROADMAP.md" ]; then echo "GSD_INITIALIZED"; else echo "GSD_NOT_INITIALIZED"; fi
```

### 3.2 如果 GSD 未初始化，执行轻量初始化

**自动执行**，不需要用户介入。创建以下最小结构：

1. **`.planning/PROJECT.md`** — 从 OpenSpec proposal 中提取项目概述
2. **`.planning/ROADMAP.md`** — 创建一个 milestone，包含当前 phase
3. **`.planning/STATE.md`** — 记录当前状态，包含 OpenSpec change 关联
4. **`.planning/config.json`** — 最小配置
5. **`.planning/phases/<name>/`** — phase 目录

轻量初始化产出示例：

```markdown
<!-- .planning/PROJECT.md -->
# 项目：<从 proposal 提取>

## 概述
<从 proposal.md 的 Summary 提取>

## 技术栈
<从 design.md 的技术选型提取>
```

```markdown
<!-- .planning/ROADMAP.md -->
# 路线图

## 里程碑 1：<从 proposal 提取>

| 阶段 | 名称 | 状态 | 描述 |
|------|------|------|------|
| 1 | <name> | 待规划 | <从 proposal 提取> |
```

```markdown
<!-- .planning/STATE.md -->
# 项目状态

## 当前位置
- **阶段**：<name>
- **状态**：规格已定义，待规划

## OpenSpec 关联
- **Change**：<name>
- **路径**：openspec/changes/<name>/

## 活动日志
- <日期>：/ai:spec 完成
```

**注意**：轻量初始化只创建最小必要文件。如果用户后续需要完整的 GSD 功能（research、intel 等），可以再运行 `/gsd-new-project`。

### 3.3 如果 GSD 已初始化

在 ROADMAP.md 中追加新 phase，更新 STATE.md。

### 3.4 读取 OpenSpec 产出摘要

```bash
echo "=== Proposal ===" && head -30 openspec/changes/<name>/proposal.md && echo "=== Design ===" && head -30 openspec/changes/<name>/design.md && echo "=== Tasks ===" && cat openspec/changes/<name>/tasks.md && echo "=== Specs ===" && find openspec/changes/<name>/specs -name "spec.md" -exec head -20 {} \;
```

---

## 第 4 步：输出

```
## /ai:spec 完成

**OpenSpec Change**: openspec/changes/<name>/
- proposal.md ✓
- design.md ✓
- tasks.md ✓
- specs/ ✓

**GSD 状态**: 已初始化 ✓
- .planning/PROJECT.md ✓
- .planning/ROADMAP.md ✓
- .planning/STATE.md ✓

**下一步**: 运行 /ai-plan <name> 将规格转为执行计划
```

---

## 守则

- 以 OpenSpec 的 spec-driven 方法论为核心：proposal → specs → design → tasks 的依赖顺序必须遵守
- 规格文件必须包含可验证的验收标准，不接受模糊表述
- 非目标和边界必须明确写进 proposal.md
- 如果需求不清晰，用 AskUserQuestion 追问，不要自行假设
- **GSD 轻量初始化是自动的**，不要让用户手动去跑 `/gsd-new-project`
- 桥接步骤从 OpenSpec 产出中提取信息填充 GSD 文件，不修改 OpenSpec 产出本身
