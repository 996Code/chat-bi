---
name: "AI: Debug"
description: "系统化调试 — 假设-验证循环"
category: Workflow
tags: [ai-workflow, debug, gsd, superpowers]
---

# /ai:debug — 系统化调试

结合 GSD 的结构化 debug 会话和 Superpowers 的假设-验证循环。

**输入**: `$ARGUMENTS` 为问题描述或 debug session slug。为空时列出已有 debug 会话。

---

## 调试方法论（强制遵守）

1. **收集证据** — 复现问题，收集日志/堆栈，确认影响范围。**不要急着改代码**
2. **形成假设** — 提出 1-3 个可验证的根因假设，按可能性排序
3. **验证假设** — 对最可能的假设设计最小验证实验，证实或证伪
4. **最小修复** — 只改必须改的，不做顺手优化
5. **验证修复** — 原问题消失 + 无回归

**绝对禁止**：没假设就改代码 / 一次改多处看效果 / 改完不复测

---

## 执行

使用 Skill 工具调用 `gsd-debug`，参数为 `$ARGUMENTS`。

- `$ARGUMENTS` 是问题描述 → 新会话
- `$ARGUMENTS` 是 slug → 继续已有会话
- 空 → 列出已有会话

---

## 输出

```
## /ai:debug 调试报告

**Session**: <slug>
**问题**: <描述>
**根因**: <确认的假设>
**修复**: <做了什么>
**验证**: <结果>

**下一步**: /ai:do 继续执行 /ai:spec 重新定义范围
```
