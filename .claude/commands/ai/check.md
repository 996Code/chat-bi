---
name: "AI: Check"
description: "全面审查 — 规格覆盖度 + 代码质量 + 安全 + 测试，一次跑完"
category: Workflow
tags: [ai-workflow, review, openspec, code-quality, verification]
---

# /ai:check — 全面审查

每次执行都跑完整审查，覆盖四个维度：规格覆盖度、代码正确性、安全性、测试覆盖。不区分轻量/完整，永远全量。

**输入**: `$ARGUMENTS` 为 OpenSpec change 名称或 phase 编号。为空时从 STATE.md 自动读取。

---

## 第 1 步：定位审查范围

### 1.1 确定 change 和 phase

```bash
CHANGE_NAME=$ARGUMENTS
PHASE_NUM=""

# 尝试从参数提取 phase 编号
if echo "$ARGUMENTS" | grep -qP '^\d+$'; then
  PHASE_NUM=$ARGUMENTS
  CHANGE_NAME=""
fi

# 从 STATE.md 补充
if [ -z "$CHANGE_NAME" ]; then
  CHANGE_NAME=$(grep -oP 'openspec/changes/\K[\w-]+' .planning/STATE.md 2>/dev/null || echo "")
fi
if [ -z "$PHASE_NUM" ]; then
  PHASE_NUM=$(grep -oP 'phase-\K\d+' .planning/STATE.md 2>/dev/null | head -1 || echo "1")
fi
```

### 1.2 收集所有源材料

```bash
# OpenSpec 产出
[ -n "$CHANGE_NAME" ] && cat "openspec/changes/$CHANGE_NAME/proposal.md" 2>/dev/null
[ -n "$CHANGE_NAME" ] && cat "openspec/changes/$CHANGE_NAME/design.md" 2>/dev/null
[ -n "$CHANGE_NAME" ] && find "openspec/changes/$CHANGE_NAME/specs" -name "spec.md" -exec cat {} \; 2>/dev/null
[ -n "$CHANGE_NAME" ] && cat "openspec/changes/$CHANGE_NAME/tasks.md" 2>/dev/null

# GSD 产出
cat ".planning/STATE.md" 2>/dev/null
find .planning/phases -name "PLAN.md" -exec cat {} \; 2>/dev/null

# 代码变更
git diff --stat HEAD~5 2>/dev/null
```

---

## 维度 1：规格覆盖度

**视角：需求 → 代码。OpenSpec 定了什么，代码有没有全部实现。**

### 2.1 提取验收标准

从 OpenSpec spec.md 提取所有 SHALL 语句和验收条件。从 tasks.md 提取所有 checkbox task。

### 2.2 逐条验证

对每条验收标准 / task，在代码中搜索对应实现：

| 验收标准 | 实现代码 | 测试覆盖 | 状态 |
|----------|----------|----------|------|
| <标准 1> | <文件:行> | <测试文件:行> | ✅/❌ |
| <标准 2> | <文件:行> | — | ❌ 无测试 |

### 2.3 三维度评分

- **Completeness**：所有 spec 验收标准和 tasks 是否都已实现
- **Correctness**：实现是否与 spec 描述一致（不是"有这功能"，而是"行为完全匹配"）
- **Coherence**：实现是否符合 design.md 的设计决策和架构方向

每个维度给出 ✅/❌ + 说明。缺少就是 ❌，没有"部分通过"。

---

## 维度 2：代码正确性

**视角：代码本身。逻辑对不对，边界有没有处理。**

逐文件审查所有变更的源文件（不限于当前 commit，覆盖当前 phase 涉及的所有文件）：

1. **逻辑正确性**：条件分支、循环、返回值、异常处理是否正确
2. **边界条件**：空输入、null/undefined、超大值、并发、时序
3. **类型安全**：类型转换、隐式 coerce、泛型使用
4. **资源管理**：连接/文件/锁是否正确释放，有无泄漏
5. **错误处理**：异常是否被正确捕获和传播，是否有吞异常

---

## 维度 3：安全性

**视角：攻击面。有没有可被利用的漏洞。**

1. **注入**：SQL 注入、命令注入、XSS、模板注入
2. **认证/授权**：权限检查是否完整，有无越权路径
3. **敏感数据**：密码/token/密钥是否明文暴露，日志中是否泄漏
4. **输入验证**：外部输入是否都经过校验和清洗
5. **依赖安全**：是否使用了已知有漏洞的依赖

---

## 维度 4：测试覆盖

**视角：信心度。测试能不能拦住回归。**

1. **关键路径覆盖**：核心业务逻辑是否有测试
2. **边界测试**：对维度 2 中发现的边界条件，是否有对应测试
3. **负面测试**：错误路径、异常输入是否有测试
4. **集成测试**：模块间交互是否有测试覆盖
5. **测试质量**：断言是否有意义（不是 `expect(true).toBe(true)`）

---

## 第 3 步：尝试调用 GSD Code Review

使用 Skill 工具调用 `gsd-code-review`，参数为 `<PHASE_NUM> --depth=standard`。

如果成功，GSD 产出的 REVIEW.md 可补充维度 2-3 的发现。如果失败（前置条件不足），直接使用上方手动审查结果，不卡住流程。

---

## 第 4 步：综合报告

```
## /ai:check 全面审查报告

**范围**: OpenSpec change=<CHANGE_NAME> | Phase=<PHASE_NUM>

### 维度 1：规格覆盖度
- Completeness: ✅/❌ <说明>
- Correctness: ✅/❌ <说明>
- Coherence: ✅/❌ <说明>

#### 验收标准
- [x] 标准 1：... — ✅ 代码:app.js:5 测试:app.test.js:8
- [x] 标准 2：... — ✅ 代码:app.js:6 测试:app.test.js:12
- [ ] 标准 3：... — ❌ 未实现

### 维度 2：代码正确性
- Critical: <N> 项
- Warning: <N> 项

<如有发现，逐条列出文件:行 + 描述>

### 维度 3：安全性
- Critical: <N> 项
- Warning: <N> 项

<如有发现，逐条列出>

### 维度 4：测试覆盖
- 覆盖率评估: ✅ 足够 / ⚠️ 有缺口 / ❌ 严重不足
- 缺口列表: <未覆盖的关键路径>

---

### 综合判断
- [  ] 审查通过（所有维度 ✅，或只有已知可接受的 Warning）
- [  ] 需要修复（有 Critical 或维度 1 ❌）

### 必须修复项（如有）
1. [CRITICAL] <描述> → <文件:行>
2. [CRITICAL] <描述> → <文件:行>

**下一步**: 需要修复 → /ai:do；全部通过 → 功能完成，可以提交
```

---

## 守则

- 每次执行都跑全部四个维度，不跳过、不降级
- 维度 1 的验收标准是硬性约束，缺少就是 ❌
- Critical 必须修复，不能跳过
- Warning 必须明确标记为"已知可接受"或"需要修复"
- 不在审查过程中修代码，只报告问题
- 实现与 spec 不一致时，先确认是代码错还是 spec 过时
- GSD code-review 是补充，不是前置条件，失败不阻塞
