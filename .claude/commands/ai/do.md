---
name: "AI: Do"
description: "执行当前 task — GSD 编排 + Superpowers 执行技法"
category: Workflow
tags: [ai-workflow, execute, gsd, superpowers]
---

# /ai:do — 执行当前 task

使用 GSD 的 execute-phase 执行当前 task，同时注入 Superpowers 的执行技法。

**输入**: `$ARGUMENTS` 为 phase 编号。为空时执行当前活动 phase。

---

## 执行技法（按 task 类型选用）

### TDD 技法（功能类 task）
1. 先写失败测试（Red）
2. 写最简代码让测试通过（Green）
3. 不改变行为地重构（Refactor）

### Brainstorm 技法（设计决策类 task）
1. 列出 2-3 种方案
2. 每种列优缺点
3. 选定最优方案并说明理由
4. 按选定方案实施

### Systematic Debugging 技法（修 bug 类 task）
1. 先复现问题
2. 形成根因假设
3. 设计最小验证实验
4. 验证或推翻假设
5. 实施最小修复
6. 验证修复有效且无回归

### Verification 技法（每个 task 完成前）
1. 测试是否通过
2. 是否满足 acceptance criteria
3. 是否引入回归
4. 代码是否干净

---

## 第 1 步：确认状态

```bash
if [ -f ".planning/STATE.md" ]; then cat .planning/STATE.md; else echo "NO_STATE"; fi
```

如果无状态文件，提示先运行 `/ai:plan`。

---

## 第 2 步：Plan-Task 同步检查

PLAN.md 在沟通中可能被修改，但执行时的 task 列表可能还是旧版本。此步骤确保 task 与最新 PLAN.md 一致。

### 2.1 读取当前 PLAN.md

```bash
find .planning/phases -name "PLAN.md" -exec cat {} \;
```

### 2.2 读取已执行的 task 状态

```bash
# 读取 STATE.md 中的 task 进度
grep -A 20 "task" .planning/STATE.md 2>/dev/null || echo "NO_TASK_PROGRESS"

# 读取 OpenSpec tasks.md 的 checkbox 状态
CHANGE_NAME=$(grep -oP 'openspec/changes/\K[\w-]+' .planning/STATE.md 2>/dev/null || echo "")
if [ -n "$CHANGE_NAME" ] && [ -f "openspec/changes/$CHANGE_NAME/tasks.md" ]; then
  cat "openspec/changes/$CHANGE_NAME/tasks.md"
fi
```

### 2.3 对比并同步

逐项对比 PLAN.md 中的 task 列表与当前执行状态：

1. **PLAN.md 新增了 task** → 标记为 pending，纳入执行队列
2. **PLAN.md 删除了 task** → 如果已完成则忽略，如果 pending 则从队列移除
3. **PLAN.md 修改了 task 描述/验收标准** → 更新对应 task 的描述和验收标准，已完成的不回退
4. **PLAN.md 调整了 task 顺序/依赖** → 更新执行顺序

输出同步结果：

```
## Plan-Task 同步结果

- 新增: <N> 个 task → <列出>
- 删除: <N> 个 task → <列出>
- 修改: <N> 个 task → <列出>
- 顺序调整: <是/否>
- 同步状态: ✅ 已同步 / ⚠️ 需人工确认
```

如果同步过程中发现歧义（如已完成的 task 被大幅修改），输出 ⚠️ 并暂停等待确认。

---

## 第 3 步：调用 GSD Execute Phase

使用 Skill 工具调用 `gsd-execute-phase`，参数为 phase 编号（如 `$ARGUMENTS`）或空。

执行过程中确保：
- **功能类 task** → TDD
- **设计类 task** → Brainstorm
- **修 bug 类 task** → Systematic Debugging
- **每个 task 完成** → Verification

---

## 第 4 步：同步 OpenSpec 状态

```bash
CHANGE_NAME=$(grep -oP 'openspec/changes/\K[\w-]+' .planning/STATE.md 2>/dev/null || echo "")
if [ -n "$CHANGE_NAME" ] && [ -f "openspec/changes/$CHANGE_NAME/tasks.md" ]; then
  echo "请同步更新 openspec/changes/$CHANGE_NAME/tasks.md 中的 checkbox"
fi
```

---

## 第 5 步：输出执行摘要

```
## /ai:do 执行报告

**Phase**: <phase-name>
**Tasks Done**: <completed>/<total>

### 完成的 Tasks
- [x] Task 1: ...
- [ ] Task 2: ... (pending)

**下一步**: 有 pending → /ai:do；全部完成 → /ai:check 全面审查；遇到 bug → /ai:debug
```
