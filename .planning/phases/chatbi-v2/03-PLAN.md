# Phase 4 计划：Agent 执行引擎（T025-T035）

> 批准于 2026-06-23。5 Wave 严格按依赖链。
> **执行约束：每个 Wave 实现前，先回看 `doc/Claude-Code-源码深度解读.md` 对标章节，1:1 对照 Claude Code 的循环结构/失败语义/熔断逻辑，不做成普通重试。**

## 架构决策（已定）
- **状态机骨架**：LangGraph StateGraph 1.2.6（pyproject 已声明，uv.lock 已锁）
- **SQL 校验**：sqlglot 30.11.0 AST（同上已装）
- **Agent 逻辑**：自建封装（取 deepagents 思路，不依赖三方库）

## 状态机结构（spec AEE-006 + 对标 Claude Code query.ts）
```
intent → schema_search → generate_sql → [validate] → execute_sql → self_check → visualize → final
                                                    ↑________ self_heal (max 2轮) ___↑
硬规则: 仅按序前进; 上游失败→final(failed); GENERAL/EXPLANATION→直接 final
```

---

## Claude Code 对标矩阵（每个 Wave 实现前必查）

| 本项目任务 | Claude Code 源码机制 | 源码解读章节 | 实现要点（不能做成普通重试） |
|---|---|---|---|
| **T025 主循环** | while(true) query() | §2.1-2.3 | 工具是循环延续（非单次调用）；每轮检查 compact；AsyncGenerator 流式；上游失败直接 break 不硬撑 |
| **T026 意图** | — | — | （对标海泰为主，无直接 Claude 对应） |
| **T027 预思考** | — | §5 Relevant Recall 思路 | 按需召回上下文，不全量灌入 |
| **T028 ask_user** | AskUserQuestionTool | **§3.5** | ask_user 是普通 tool（requiresUserInteraction=true），用户输入作 tool_result；**不是特殊机制** |
| **T029 SQL生成** | Prompt 分层 | **§4.1-4.3** | 静态(schema/skills)可缓存 / 动态(问题/历史)分离；用 prompt_cache 组装 |
| **T030 三层校验** | Fail-Closed 安全默认 | **§3.3** | 默认拒绝；只有显式声明安全的才放行；AST 非字符串前缀（v1 教训 #46） |
| **T031 执行** | 并发分批（只读才并发） | §3.4 | 执行最多1次；写操作串行 |
| **T032 自愈** | — | — | （对标海泰错误码为主）**但自愈 prompt 必须保留全部安全规则**（v1 教训 #32：自愈 prompt 只写"你只生成SQL"→能出 DROP） |
| **T033 自检** | — | — | （对标海泰为主）0行/异常数字检测 |
| **T034/T035 图表** | — | — | JSON 自愈对标海泰（括号补全） |
| **跨 Wave** | 熔断器 | **§6.4** | 连续失败停止（自愈3次/压缩3次），不无限重试 |
| **跨 Wave** | 上下文压缩+状态补偿 | **§6.1-6.3** | （Phase 5 T038 实现，T025 预留检查点）压缩后重注入语义层+当前SQL+filters |
| **跨 Wave** | dump-prompts 可观测 | **§4.5** | config.prompt_dump_enabled 已就位，T029 用 |

---

## Wave 划分

### Wave 1：地基（叶子节点）
- **T026 意图识别** `backend/app/ai/intent.py`：5意图 + Pydantic + confidence<0.6降级 + normalized_question剥离可视化
- **T030 SQL 三层校验** `backend/app/core/sql_validator.py`：sqlglot AST（非SELECT拒绝）+ 危险函数（LOAD_FILE/OUTFILE/SLEEP...）+ 白名单列

### Wave 2：SQL 生成 + 执行
- **T029 SQL 生成** `backend/app/ai/sql_agent.py`：prompt_cache分层组装 + 白名单列/data_type约束注入 + fewshot + 复合指标 + 生成后调T030
- **T031 SQL 执行** `backend/app/services/sql_executor.py`：复用datasource_engine池 + asyncio.to_thread + 超时30s + 分块1000 + 最多执行1次

### Wave 3：自愈 + 自检
- **T032 SQL 自愈** `backend/app/ai/sql_healer.py`：错误码映射(1146/1054/1064/1052...) + 专项纠正prompt + **保留安全规则** + 自愈后走同样三层校验 + 2轮上限 + 熔断器3次
- **T033 结果自检** `backend/app/ai/result_checker.py`：0行/异常大数字(笛卡尔积)/不一致 → 分析 → 自动修复 → 重执行 → 仍异常ask_user

### Wave 4：图表
- **T034 图表生成** `backend/app/ai/chart_agent.py`：LLM声明式ECharts JSON + chart_type_hint + JSON schema校验
- **T035 图表降级** 同文件：JSON截断→括号补全自愈→仍失败规则推断+WARNING

### Wave 5：编排（串联所有节点）
- **T025 主循环** `backend/app/ai/agent.py`：LangGraph StateGraph节点编排 + 严格前向边 + 失败路由 + SSE事件 + 轮数上限20/递归50
- **T027 预思考** `backend/app/ai/thinking.py`：选表理由+聚合+注意事项 → SSE thinking
- **T028 ask_user** `backend/app/ai/ask_user.py`：ask_user即tool（§3.5），schema不确定/结果异常触发

## 实施约束
1. 每个 Wave 实现前，Read 对标章节
2. 所有魔法数字走 config（已就位）
3. fail-closed：检索无结果不 fallback（§3.3）
4. 自愈 prompt 保留全部安全规则（v1 教训 #32）
5. AST 校验非字符串前缀（v1 教训 #46）
6. 审计全覆盖（复用 write_audit_log）
