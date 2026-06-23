# Skills Business Rules

## Status: ADDED

## 概述

对标 Claude Code Skills（SKILL.md + 条件触发 + 内嵌 Shell + 热更新 + 三种来源）。ChatBI v2 的 Skills 是全局的 SKILL.md 文件，业务规则外置，修改后下次查询立即生效。

## 需求

### SKL-001: SKILL.md 格式

```markdown
---
name: sql-business-rules
description: SQL 生成的业务规则
version: 1.0
---

## 时间字段规则
1. 所有时间过滤使用 created_at，不要用 update_time
2. 时间范围使用 BETWEEN 而非 >= AND <=

## 非空规则
1. 凡是查询列里的业务字段，WHERE 中拼接 IS NOT NULL

## GMV 计算规则
GMV = SUM(total_amount) WHERE status IN ('paid', 'shipped')
```

**验收标准**：
- [ ] 支持 YAML frontmatter（name/description/version）
- [ ] 支持 Markdown 正文（业务规则描述）
- [ ] 支持多文件（sql-rules/SKILL.md, chart-rules/SKILL.md, business-rules/SKILL.md）

### SKL-002: 热更新

**对标 Claude Code**：Skills 加载时 memoize 缓存 + settingsChangeDetector.subscribe 实时更新

- 修改 SKILL.md → 下次查询立即使用新规则
- 不需重启服务
- 修改记录日志（谁 + 什么时间 + 改了什么）

**验收标准**：
- [ ] 修改 GMV 规则 → 下次查询"GMV"使用新定义
- [ ] 新增 SKILL.md → 系统自动加载（不需重启）
- [ ] Skills 变更记录可查看

### SKL-003: Skills 注入 Prompt

**对标 Claude Code**：getSystemPrompt() 中 Skills 内容注入 system prompt

Skills 内容注入到 SQL 生成的 system prompt 中：
- 放在 Prompt 的静态段（可缓存）
- 每次生成 SQL 前读取最新 SKILL.md 内容
- Skills 内容作为约束规则（不是建议）

**验收标准**：
- [ ] SKILL.md 中定义的 GMV 规则 → SQL 生成时严格遵循
- [ ] 修改 SKILL.md → Prompt 缓存失效 → 下次使用新规则

### SKL-004: Skills 编辑器（前端）

- 在线编辑 SKILL.md（Markdown 编辑器）
- 预览效果（"应用此规则后，'本月 GMV' 将生成以下 SQL: ..."）
- 版本管理 + 变更历史

**验收标准**：
- [ ] 支持在线编辑 + 预览效果
- [ ] 支持版本回滚