---
name: "AI: Plan"
description: "完整规划 — 生成 CONTEXT.md + PLAN.md"
category: Workflow
tags: [ai-workflow, plan, gsd]
---

# /ai:plan — 完整规划

从 OpenSpec 产出生成执行计划。优先走 GSD discuss + plan 全量流程；前置条件不足时直接从 OpenSpec 产出生成 CONTEXT.md + PLAN.md，结果一致。

**输入**: `$ARGUMENTS` 为 OpenSpec change 名称（kebab-case）。

---

## 第 1 步：加载 OpenSpec 上下文

### 1.1 验证 change 存在

```bash
openspec status --change "$ARGUMENTS" --json 2>/dev/null || echo "CHANGE_NOT_FOUND"
```

如果找不到，列出可用 changes：
```bash
openspec list --json
```

### 1.2 读取全部产出文件

按顺序读取：
1. `openspec/changes/$ARGUMENTS/proposal.md`
2. `openspec/changes/$ARGUMENTS/specs/*/spec.md`（所有 delta spec）
3. `openspec/changes/$ARGUMENTS/design.md`
4. `openspec/changes/$ARGUMENTS/tasks.md`

---

## 第 2 步：检查 GSD 状态

```bash
if [ -f ".planning/ROADMAP.md" ]; then echo "GSD_READY"; else echo "GSD_NOT_INITIALIZED"; fi
```

如果未初始化，提示先运行 `/ai:spec`（它会自动初始化 GSD）。

---

## 第 3 步：生成 CONTEXT.md

从 OpenSpec design.md 提取实现决策，写入 `.planning/phases/<phase-name>/<NN>-CONTEXT.md`：

```markdown
# Phase <N> 上下文：<CHANGE_NAME>

## 需求来源
OpenSpec change：`<CHANGE_NAME>`

## 决策
### 技术选型
<从 design.md 提取>

### 实现方式
<从 design.md 提取>

### 不做的事
<从 proposal.md 的非目标提取>

## 规格参考
- `openspec/changes/<CHANGE_NAME>/specs/<capability>/spec.md`
```

---

## 第 4 步：生成 PLAN.md

将 OpenSpec tasks.md 转化为 GSD 格式的执行计划，写入 `.planning/phases/<phase-name>/<NN>-PLAN.md`：

- 按 tasks.md 中的依赖关系分组为 Wave
- 每个 task 包含：read_first、type（setup/tdd/implement/verify）、acceptance_criteria、actions
- **TDD 类型的 task 必须标注 `type: tdd`**，在 actions 中按 Red→Green→Refactor 结构描述
- 验收标准从 spec.md 提取

**同时尝试调用 GSD 全量流程**：

使用 Skill 工具调用 `gsd-discuss-phase`，参数为当前 phase 编号。如果成功，再用 Skill 调用 `gsd-plan-phase`。GSD 产出的 CONTEXT.md 和 PLAN.md 可覆盖第 3、4 步的版本。

**关键：防止 discuss-phase 幻觉发散**。在调用 `gsd-discuss-phase` 之前，将以下约束注入 prompt 上下文：

```
## OpenSpec 已锁定约束（不可重议）

以下内容已通过 OpenSpec 规格流程确定，discuss 阶段不得重新讨论或质疑：

### 需求范围（来自 proposal.md）
<粘贴 proposal.md 的 What Changes 和 Impact 部分>

### 技术决策（来自 design.md）
<粘贴 design.md 的 Decisions 部分>

### 验收标准（来自 specs/*/spec.md）
<粘贴所有 spec.md 的 SHALL 语句>

discuss 阶段只能补充以下内容：
- 实现细节（如何实现，不是做什么）
- 代码组织结构
- 依赖关系和执行顺序
- 风险缓解的具体实现方案

严禁讨论：已锁定的需求范围、技术选型、验收标准
```

如果 GSD 流程因前置条件不足（缺少 SUMMARY.md、gsd-sdk 等）无法执行，**直接使用第 3、4 步生成的文件，不做降级提示**。最终产出物完全一致。

---

## 第 5 步：更新状态并输出

更新 `.planning/STATE.md`，标记 phase 为"计划已制定，待执行"。

```
## /ai:plan 完成

**GSD Phase**: .planning/phases/<phase-name>/
- CONTEXT.md ✓
- PLAN.md ✓（<N> 个 Wave，<M> 个 Task）

**下一步**: 运行 /ai:do 开始执行
```

---

## 守则

- 必须先完成 OpenSpec 上下文加载
- GSD 全量流程是锦上添花，不是硬性前置条件
- 最终产出物（CONTEXT.md + PLAN.md）必须存在，无论走哪条路径
- discuss-phase 只补充实现决策，不重议 OpenSpec 已锁定的需求
- TDD 类型的 task 必须在 PLAN.md 中按 Red→Green→Refactor 描述