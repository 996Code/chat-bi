---
name: "AI: Resume"
description: "恢复上下文 — 读取 OpenSpec + GSD 状态，推荐下一步"
category: Workflow
tags: [ai-workflow, resume, gsd, openspec]
---

# /ai:resume — 恢复上下文

跨会话恢复工作状态。读取 OpenSpec 和 GSD 状态，交叉对照一致性，推荐下一步。

**输入**: `$ARGUMENTS` 可选，phase 编号或 OpenSpec change 名称。

---

## 第 1 步：GSD 状态恢复

读取 GSD 状态文件：

```bash
if [ -f ".planning/STATE.md" ]; then cat .planning/STATE.md; else echo "NO_STATE"; fi
```

```bash
if [ -f ".planning/ROADMAP.md" ]; then cat .planning/ROADMAP.md; else echo "NO_ROADMAP"; fi
```

```bash
find .planning/phases -name "PLAN.md" -exec echo "PLAN: {}" \; 2>/dev/null
```

如果无状态文件，提示先运行 `/ai:spec`。

---

## 第 2 步：OpenSpec 状态

```bash
openspec list --json 2>/dev/null || echo "NO_OPENSPEC"
```

对每个 active change，读取状态：
```bash
openspec status --change "<name>" --json
```

---

## 第 3 步：交叉对照

检查 GSD 状态和 OpenSpec 状态是否一致：
- GSD STATE.md 中的 phase 与 OpenSpec change 对应
- GSD 的 task 完成度 vs OpenSpec 的 tasks.md checkbox
- 不一致时输出警告

---

## 第 4 步：输出

```
## /ai:resume 上下文恢复

### GSD 状态
- **Phase**: <name>
- **进度**: <done>/<total> tasks
- **中断点**: <如有>

### OpenSpec 状态
- **Change**: <name> — <N/M> tasks complete
- **一致性**: ✅/❌

### 推荐下一步
1. 有中断的 task → /ai:do
2. 有 pending review → /ai:check
3. 有 debug 会话 → /ai:debug
4. 新需求 → /ai:spec
5. 需要新 plan → /ai:plan
```