# Claude Code 源码深度解读

> 基于 claude-code-analysis 项目（31 个 Markdown 分析文件）全面整理。
> 覆盖：整体架构、Query 主循环、Tool 工具体系、Prompt 管理、Memory 体系、
> 上下文压缩、Multi-Agent、会话持久化、Skills 机制、MCP 集成、Sandbox 沙箱、
> 安全体系、前端组件架构（含函数级实现）、产品信号机制、隐私治理。
> 文档规模：~6000 行，面向 ChatBI v2 重构参考。

---

## 目录

1. [整体架构 — 六层分层 + 多入口 + 统一内核](#1-整体架构)
2. [Query 主循环 — Agent 执行引擎的心脏](#2-query-主循环)
3. [Tool 工具执行体系 — 安全默认 + 并发分批 + ask 即工具](#3-tool-工具执行体系)
4. [Prompt 管理 — 分层拼装 + section 缓存 + 覆盖优先级 + 可观测](#4-prompt-管理)
5. [Memory 体系 — 四层文件化存储 + Relevant Recall + Agent Memory](#5-memory-体系)
6. [上下文压缩 — 压缩 + 状态补偿 + 熔断器 + Session Memory 直挂](#6-上下文压缩)
7. [Multi-Agent 多代理体系 — 三套并存 + 权限桥接 + task list 协作](#7-multi-agent-多代理体系)
8. [会话持久化 — append-only JSONL + 恢复修复 + metadata 尾部重挂](#8-会话持久化)
9. [Skills 技能机制 — 三种来源 + 条件触发 + 内嵌 Shell + 热更新](#9-skills-技能机制)
10. [MCP 外部工具集成 — 四种传输协议 + 认证雪崩防护 + 工具池融合](#10-mcp-外部工具集成)
11. [Sandbox 安全沙箱 — 四层结构 + Git bare repo 逃逸防护 + 配置热更新](#11-sandbox-安全沙箱)
12. [安全体系全景 — 同心圆防线 + Unicode 防御 + PII 屏障 + Trust 时序](#12-安全体系全景)
13. [产品信号机制 — 负面关键词检测 + 挫败感信号 + 反馈闭环](#13-产品信号机制)
14. [隐私治理 — 分级控制 + 类型屏障 + 规避路线](#14-隐私治理)
15. [前端组件体系 — 双中枢 + 平台控制面 + 函数级实现](#15-前端组件体系)
16. [ChatBI v2 完整借鉴清单](#16-chatbi-v2-完整借鉴清单)

---

## 1. 整体架构

### 1.1 核心定位

Claude Code **不是"命令行聊天工具"**，而是**面向代码工作流的本地 agent 平台**。

核心特征：
- **多入口**：CLI、REPL、SDK、MCP、bridge、remote
- **多层次**：命令、执行内核、工具、权限、memory、扩展
- **多形态协作**：单 agent、subagent、background、teammate、swarm

### 1.2 六层分层架构

```
┌──────────────────────────────────────────────────────┐
│ CLI 引导层                                            │
│ entrypoints/cli.tsx  ← 快路径分流（--version 等）      │
│ main.tsx             ← 总控入口，编排所有初始化         │
└──────────────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────┐
│ 初始化层                                              │
│ init.ts              ← Trust 前：只应用安全 env var    │
│ setup.ts             ← 环境、CWD、hooks、memory 启动   │
└──────────────────────────────────────────────────────┘
                │                    │
                ▼                    ▼
┌────────────────────┐  ┌──────────────────────────────┐
│ 控制面 / 命令层    │  │ TUI / REPL 层                 │
│ commands.ts        │  │ replLauncher.tsx / REPL.tsx   │
│ slash/menu         │  └──────────────────────────────┘
└────────────────────┘                 │
                                       ▼
                          ┌──────────────────────────────┐
                          │ 执行内核                      │
                          │ query.ts / QueryEngine.ts    │
                          └──────────────────────────────┘
                            │         │           │
                            ▼         ▼           ▼
                  ┌──────────────┐ ┌────────────┐ ┌──────────────┐
                  │ Tool/Perm    │ │ Memory/    │ │ 扩展层        │
                  │ Tool.ts      │ │ Persist    │ │ MCP/Plugin/   │
                  │ orchestration│ │ memdir/SM  │ │ Remote/Swarm  │
                  └──────────────┘ └────────────┘ └──────────────┘
```

### 1.3 默认交互主链路

```
entrypoints/cli.tsx
  → main.tsx → init.ts + setup.ts
  → launchRepl() → App + REPL
  → PromptInput / slash command / footer 菜单
  → query()                                              ← 核心主循环
     → services/api/claude.ts                             ← 调用 Claude API
     → runTools() / StreamingToolExecutor                 ← 执行工具
     → sessionStorage / SessionMemory / compact / hooks   ← 会话管理
     → 返回到 query 主循环
```

### 1.4 多种运行形态共用同一执行内核

```
REPL（有 UI）          ──┐
headless/SDK          ──┤
subagent（子任务）     ──┤──→ query.ts / QueryEngine.ts ──→ tool/memory/permission
background agent      ──┤
bridge/remote         ──┘
```

"本地 REPL 里怎么调工具"和"后台自动化任务里怎么调工具"走**完全相同代码路径**。不存在两套实现的行为差异。

### 1.5 三种 multi-agent 模型（总览）

```
一、普通 subagent
   主 agent 派出一个 subagent，执行完回传结果
   可同步、可后台、可 fork

二、Coordinator Mode
   主线程变成 orchestrator
   "You are Claude Code, an AI assistant that orchestrates
    software engineering tasks across multiple workers."
   持续派出 worker，worker 结果用 <task-notification> 回流

三、Swarm / Teammates
   显式创建 team（team file + task list + mailbox）
   lead + teammates，in-process / tmux / iTerm2 后端
   支持 mailbox、权限回流、task list 协作
```

---

## 2. Query 主循环

### 2.1 不是"调一次 API 就结束"的简单函数

`query.ts` 是系统的心脏。它是一个 **while(true) 循环**，让 Agent 可以无限轮调用工具直到任务完成：

```
┌──────────────────────────────────────────────────────────────┐
│                    query() 主循环                             │
│                                                              │
│  ① 组装 messages + system prompt                             │
│       ↓                                                      │
│  ② 调用 Claude API，流式接收                                  │
│       ↓                                                      │
│  ③ 提取模型输出的 tool_use blocks                            │
│       ↓                                                      │
│  ④ 没有 tool_use? → break（LLM 认为任务完成了）               │
│       ↓                                                      │
│  ⑤ 执行工具（按并发安全性分批）                                │
│       ↓                                                      │
│  ⑥ tool_result 追加到 messages → 回到 ①（下一轮 LLM 调用）    │
│       ↓                                                      │
│  ⑦ compact 检查 + hook 执行 + memory 更新                    │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 伪代码

```typescript
export async function* query(
  userMessages: Message[],
  systemPrompt: SystemPrompt,
  toolUseContext: ToolUseContext,
  deps: QueryDeps,
): AsyncGenerator<StreamEvent> {
  let messages = userMessages

  while (true) {
    // 1. 调用模型 API，流式输出
    for await (const event of deps.claudeApi.stream(messages, systemPrompt)) {
      yield event
    }

    // 2. 检查是否有 tool_use
    const toolUseBlocks = extractToolUseBlocks(messages)
    if (toolUseBlocks.length === 0) break

    // 3. 执行工具（按并发安全性分批）
    for await (const update of runTools(toolUseBlocks, ...)) {
      yield update
    }

    // 4. 结果回流到 messages → 下一轮循环
    messages = appendToolResults(messages, toolResults)

    // 5. 会话管理
    await executePostSamplingHooks(messages, toolUseContext)
    if (shouldAutoCompact(messages)) await compactConversation(messages, ...)
  }
}
```

### 2.3 关键设计点

**1. AsyncGenerator 模式**：返回 `AsyncGenerator<StreamEvent>`，调用方（UI / SDK）只需消费流。同一 query 函数支撑 REPL、SDK、subagent、bridge 所有形态。

**2. 工具是循环的延续**：不是"调 API→返回结果→结束"，而是"调 API→执行工具→回流→再调 API"。这让 Agent 能多轮调用工具直到目标达成。

**3. 每轮都检查 compact**：上下文快满时自动触发压缩，防止 token 爆炸。

**4. `normalizeMessagesForAPI()`**：把富文本、本地图片、长串元数据从混合队列里提炼成标准化 API 格式再交给模型。Transcript 是唯一交互真理——复杂 JS 状态的结果全部化为对话流文本。

---

## 3. Tool 工具执行体系

### 3.1 完整执行链路

```
模型输出 assistant message（含 tool_use blocks）
  │
  ▼
query.ts 收集 tool_use
  │
  ▼
StreamingToolExecutor 或 runTools()
  │  - 并发安全的工具不等 content 收完就启动
  │  - 状态机: queued → executing → completed → yielded
  │
  ▼
toolOrchestration.ts: partitionToolCalls()
  │  - 按 isConcurrencySafe 分组
  │  - 并发批次的 contextModifier 先收集，批次完再统一应用
  │
  ▼
toolExecution.ts: 对每个 tool_use 逐个执行
  ├─ 1. Zod schema 校验（失败不退出，扔给 LLM 纠正）
  ├─ 2. validateInput（语义级校验：黑名单目录等）
  ├─ 3. Backfill（隐式派生依赖注入）
  ├─ 4. pre-tool hooks（权限判定 allow/deny/ask 在此发生）
  ├─ 5. tool.call()
  └─ 6. 生成 tool_result / progress / attachment
  │
  ▼
tool_result 回流到 messages → 下一轮 API 调用
```

### 3.2 Tool 接口定义（完整）

```typescript
type Tool = {
  // 能力描述
  name: string
  aliases?: string[]               // 别名
  description(): string            // 动态描述（可含实时状态）
  prompt(): string                 // 注入 system prompt 的说明
  searchHint?: string              // 搜索提示
  inputSchema: ZodSchema           // 强类型 schema

  // 核心执行
  call(args, context, canUseTool, parentMessage, onProgress): Promise<ToolResult>

  // 安全属性（保守默认）
  isConcurrencySafe(input): boolean  // 默认 false — 不可并发
  isReadOnly(input): boolean         // 默认 false — 非只读
  isDestructive(input): boolean      // 默认 false — 非破坏性
  checkPermissions(input, ctx): PermissionResult
  preparePermissionMatcher(input): PermissionMatcher

  // UI 渲染（每个工具自定义终端展示）
  renderToolUseMessage(input, opts)
  renderToolResultMessage(result, opts)
  renderToolUseRejectedMessage(input)
  renderToolUseErrorMessage(error)
  renderToolProgressMessage?(progress)
  renderQueuedMessage?()

  // 运行控制
  requiresUserInteraction(): boolean
  interruptBehavior(): string
  backfillObservableInput(input, ctx)
  toAutoClassifierInput(input)
}
```

### 3.3 安全默认原则（Fail-Closed）

```typescript
const TOOL_DEFAULTS = {
  isEnabled: () => true,
  isConcurrencySafe: (_input) => false,   // 默认：不可并发
  isReadOnly: (_input) => false,           // 默认：非只读
  isDestructive: (_input) => false,         // 默认：非破坏性
  checkPermissions: () => ({ behavior: 'allow' }),
  toAutoClassifierInput: (_input) => '',   // 默认不自动分类（手工确认）
}
```

所有工具必须通过 `buildTool()` 构造，默认值全是保守的。**并发默认不安全、读写默认危险**。开发者必须显式声明安全属性才能放行。

### 3.4 并发/串行分批（`partitionToolCalls`）

```
LLM 返回 [A(Read), B(Read), C(Write), D(Read)]

partitionToolCalls() 分批：
  批次1: [A, B]  并发 — 都是 Read，安全
  批次2: [C]     串行 — Write，必须等前面完成
  批次3: [D]     串行 — 等 Write 完成后再执行

并发的 contextModifier 先收集、等批次完统一应用 — 防止竞态污染
```

### 3.5 AskUserQuestionTool — 工具不一定是机器操作

`requiresUserInteraction = true`, `isReadOnly = true`。

LLM 要问用户问题时，调用这个工具 → 屏幕弹出表单 → 用户输入作为 tool_result 返回给 LLM。

**"向用户提问"在系统里不是特殊机制，就是普通 Tool。**

这与 Claude Code 的交互模式完美契合——Agent 发现自己不确定时，"调用 AskUserQuestion 工具"来确认，用户回复后继续执行。

### 3.6 具体案例：BashPermissionRequest 的审批链路

```
BashPermissionRequest 入口
  → parseSedEditCommand(command) — 判断是否 sed 编辑
  → 是 sed → 切到 SedEditPermissionRequest
  → 否 → BashPermissionRequestInner

BashPermissionRequestInner:
  → usePermissionExplainerUI()      — 解释器面板
  → useShellPermissionFeedback()    — accept/reject 反馈模式
  → generateGenericDescription()    — 补全 classifier 描述
  → extractRules() / getFirstWordPrefix() — 生成可编辑 prefix
  → bashToolUseOptions()            — 基于后端建议/classifier/反馈生成最终选项
  → onSelect:
      - yes / yes-apply-suggestions / yes-prefix-edited
      - yes-classifier-reviewed / no
  → toolUseConfirm.onAllow() 或 handleReject()
  → logEvent() 记录分析数据
```

审批 UI 不是简单 yes/no，而是动态生成规则、反馈、session 级放行和 localSettings 级持久规则的**策略编辑器**。

---

## 4. Prompt 管理

### 4.1 六个层的 Prompt 体系

```
第1层：默认主系统提示
  src/constants/prompts.ts — getSystemPrompt()
  返回 string[]（每个 section 独立可缓存）
  静态主干 + SYSTEM_PROMPT_DYNAMIC_BOUNDARY + 动态 section

第2层：有效 prompt 组装器
  src/utils/systemPrompt.ts — buildEffectiveSystemPrompt()
  覆盖优先级: override > coordinator > agent > custom > default
  appendSystemPrompt 始终挂到最后

第3层：运行时上下文注入
  src/context.ts
  - CLAUDE.md（用户/项目指令，四层信任分级）
  - currentDate（当前日期）
  - git status（代码状态快照）
  - cache breaker

第4层：启动期附加指令
  src/main.tsx
  - --system-prompt / --system-prompt-file（完全替换）
  - --append-system-prompt / --append-system-prompt-file（尾部追加）
  - proactive / chrome / teammate addendum
  - appendSystemPrompt 是正式的"追加指令总线"

第5层：Prompt 缓存与失效管理
  src/constants/systemPromptSections.ts
  - systemPromptSection(name, compute) — 默认可缓存
  - DANGEROUS_uncachedSystemPromptSection(name, compute, reason) — 显式不可缓存
  - section 级别缓存 + boundary 分隔 + clear/compact/worktree 切换时失效

第6层：专项 prompt 家族
  compact prompt / session memory prompt / extract memories prompt / hooks prompt
  与主 prompt 风格完全不同 — 强约束、限工具、限格式
```

### 4.2 覆盖优先级（详细）

```typescript
export function buildEffectiveSystemPrompt({
  mainThreadAgentDefinition, toolUseContext,
  customSystemPrompt, defaultSystemPrompt,
  appendSystemPrompt, overrideSystemPrompt,
}): SystemPrompt {

  // 0. 完全覆盖
  if (overrideSystemPrompt) return asSystemPrompt([overrideSystemPrompt])

  // 1. Coordinator 模式
  if (isCoordinatorMode() && !mainThreadAgentDefinition) {
    return asSystemPrompt([getCoordinatorSystemPrompt(), ...append])
  }

  // 2. Agent 自定义 + Proactive
  if (agentSystemPrompt && proactiveActive) {
    return asSystemPrompt([
      ...defaultSystemPrompt,                          // 默认 + agent 指令
      `\n# Custom Agent Instructions\n${agentSystemPrompt}`,
      ...append,
    ])
  }

  // 3-4. Agent / Custom / Default 优先级
  return asSystemPrompt([
    ...(agentSystemPrompt ? [agentSystemPrompt]         // agent 替换默认
      : customSystemPrompt ? [customSystemPrompt]       // custom 替换默认
      : defaultSystemPrompt),                           // 默认
    ...append,
  ])
}
```

关键规则：
- `customSystemPrompt` **不会 append 到默认 prompt 后面**，而是**直接替代**
- `appendSystemPrompt` 不管前面来源是什么，**始终挂到最后**
- agent prompt 在普通模式下**也可能取代默认 prompt** — 强力角色切换

### 4.3 Prompt 缓存工程

```
┌────────────────────────────────────┐
│ 静态主干（可缓存）                  │
│ - 身份声明: "You are an interactive │
│   agent that helps users..."       │
│ - 执行规则: IMPORTANT 段           │
│ - 工具使用方式: getActionsSection  │
├────────────────────────────────────┤
│ SYSTEM_PROMPT_DYNAMIC_BOUNDARY     │ ← 缓存边界标记（不给模型看）
├────────────────────────────────────┤
│ 动态段（不可缓存）                  │
│ - session_guidance（会话指引）      │
│ - memory（MEMORY.md 索引内容）      │
│ - env_info（当前环境信息）          │
│ - language / output_style          │
│ - mcp_instructions（MCP 工具描述） │
│ - scratchpad                       │
│ - frc / summarize_tool_results     │
└────────────────────────────────────┘
```

设计意图：
- boundary 之前的 section 尽可能稳定 → prompt prefix cache 命中
- boundary 之后的允许更多 session 级变化 → 每轮重算
- `DANGEROUS_uncachedSystemPromptSection()` 的命名是**故意的**——让开发者知道"这段 prompt 每轮都会重新生成，确定要这么做吗？"

### 4.4 最大的工程亮点：fork child 复用父 prompt 原始字节

```typescript
// FORK_AGENT 注释（真实源码）
// The getSystemPrompt here is unused: the fork path passes
// `override.systemPrompt` with the parent's already-rendered system prompt bytes
// Reconstructing by re-calling getSystemPrompt() can diverge ...
// and bust the prompt cache
```

fork child 不是重新生成 system prompt，而是直接拿**父会话已经渲染好的 prompt 字节**。这不是为了逻辑正确性，而是为了**prompt cache 命中稳定性**。

### 4.5 可观测性

- **`dump-prompts`**：拦截请求，把完整 API 请求落到 `~/.claude/dump-prompts/<session>.jsonl`
- **`/context`**：把 effective system prompt 拆成 named entries，逐段统计 token
- 可以回答："哪段最贵、哪些段应该继续缓存、哪些段每次都在变"

---

## 5. Memory 体系

### 5.1 不是单库，是四层文件化存储

```
Auto Memory（全局持久记忆）
  ├─ MEMORY.md 索引（200行/25KB 截断保护）
  ├─ topic memories/*.md（每条记忆独立文件）
  └─ Relevant Recall（最多召回 5 条，不全量灌入）

Session Memory（当前会话摘要）
  ├─ 触发条件：token > 10000 + increment > 5000 + toolCalls > 3
  ├─ 找自然断点（!hasToolCallsInLastTurn）
  └─ 后台 forked subagent 更新（只允许 FileEditTool 操作精确路径）

Agent Memory（某类 agent 的持久记忆）
  ├─ user scope: ~/.claude/agent-memory/<agent-type>/
  ├─ project scope: <cwd>/.claude/agent-memory/<agent-type>/
  ├─ local scope: <cwd>/.claude/agent-memory-local/<agent-type>/
  └─ Snapshot: 可分发、可初始化、可升级的记忆资产

Team Memory（团队共享知识）
  ├─ pull / push / watcher / checksum / optimistic locking
  ├─ path validation + secret scanning（30+ 种密钥模式）
  └─ 按 repo 识别命名空间
```

### 5.2 底层存储：文件夹 + MEMORY.md 索引

```
~/.claude/projects/<project>/memory/
  ├── MEMORY.md               ← 索引（只维护链接+一行描述）
  ├── user-preference.md       ← 每条记忆一个独立 .md
  ├── project-context.md
  └── topic-xxx.md

设计意图：
  - 每条记忆单独写成一个 markdown 文件
  - MEMORY.md 只维护索引链接和一行描述
  - Agent 在 prompt 里默认只看到 MEMORY.md
  - 需要细节时再去读具体 memory 文件
  - 防止一个超长文件造成 prompt 爆炸 + 更新冲突
```

### 5.3 buildMemoryPrompt() 给模型的约束

```typescript
export function buildMemoryPrompt(params): string {
  // 1. 读取 MEMORY.md（同步，因为调用可能来自 React render 路径）
  // 2. truncateEntrypointContent(raw) — 200行/25KB 硬截断保护
  // 3. buildMemoryLines() — 注入记忆类型分类、禁止保存的内容、双步写入法
  // 4. 最终拼接: [规则] + [MEMORY.md 索引内容]
}
```

模型被要求像维护小型知识库一样操作 memory 目录：独立文件存储、维护索引、更新过时记忆、避免重复。

### 5.4 Relevant Recall — 不是全量灌入

```
memoryDir
  → scanMemoryFiles() 扫描文件头（只看 header/manifest）
  → 过滤 alreadySurfaced + 与当前工具活跃使用的
  → formatMemoryManifest() 生成"文件名 + 描述"清单
  → sideQuery(...) 调轻量模型做选择
  → 最多选 5 个 memory 文件
  → 返回绝对路径

设计要点：
  - 被选的是文件名，不是整段正文
  - 选择器只看 header/manifest，不先把正文塞进去
  - MEMORY.md 本身不在这里选（已单独注入 system prompt）
  - recentTools 会影响选择（避免重复）
  - alreadySurfaced 过滤已展示过的
```

### 5.5 Agent Memory 的运行时耦合

Agent 定义里声明了 `memory` 字段后，系统自动做三件事：

1. **prompt 注入**：`parseAgentFromJson/Markdown()` 路径中，`getSystemPrompt()` 强制追加 Agent Memory prompt
2. **工具注入**：`loadAgentsDir.ts` 自动把 `FileWriteTool`、`FileEditTool`、`FileReadTool` 注入 agent 工具列表
3. **Snapshot 检查**：agent 加载时检查 `snapshot.json` vs `.snapshot-synced.json`

```
agent runtime = agent definition
              + system prompt
              + tool set
              + permission context
              + memory directory
              + snapshot state
```

### 5.6 Agent Memory Snapshot — 把记忆做成可分发资产

```
<cwd>/.claude/agent-memory-snapshots/<agentType>/
  ├── snapshot.json              — 记录 updatedAt
  └── .snapshot-synced.json      — 本地最后同步时间

三种动作：
  - none: 没有 snapshot 或已经同步
  - initialize: 本地 memory 为空 → 从 snapshot 复制
  - prompt-update: snapshot 有新版 → 提示用户更新
```

### 5.7 Session Memory 更新：最严密沙箱化的子 Agent

```
主会话 shouldExtractMemory() == true
  → registerPostSamplingHook 触发
  → setupSessionMemoryFile()            — 0o700 目录 + 0o600 文件
  → buildSessionMemoryUpdatePrompt()    — "Only use Edit tool to update"
  → runForkedAgent({
      canUseTool: createMemoryFileCanUseTool(memoryPath)
    })

createMemoryFileCanUseTool():
  → 只允许 FileEditTool
  → 且 file_path 必须精确匹配 memoryPath（不允许路径穿越）
  → FileReadTool 和 FileWriteTool 都被 deny
```

连 FileReadTool 都不给——这个子 Agent 是真正的"只改一个文件，其他什么都别碰"。

### 5.8 为什么目录创建是 fire-and-forget

```
loadAgentMemoryPrompt()
  → ensureMemoryDirExists(memoryDir)  — 异步 fire-and-forget

原因：这段逻辑运行在同步 getSystemPrompt() 回调中，
某些调用来自 React render 路径，不能异步阻塞。
即使目录还没创建好，后面的 FileWriteTool 自己也会补 mkdir。
```

---

## 6. 上下文压缩

### 6.1 压缩不是截断，是"压缩 + 状态补偿"

四种 compact 策略并存：

| 策略 | 触发条件 | 核心文件 |
|------|----------|----------|
| 手动 compact | 用户执行 `/compact` | compact.ts |
| 自动 compact | Token 超过阈值（前置 13K buffer） | autoCompact.ts |
| Session Memory compact | 有 Session Memory 文件 → 直挂不调 API | sessionMemoryCompact.ts |
| Micro compact | 轻量缩减（实验性） | reactiveCompact.ts |

### 6.2 compactConversation() 完整流程

```
┌─────────────────────────────────────────────────────────────┐
│                  compactConversation()                      │
│                                                             │
│  ① 预处理：移除图片和大型附件                                │
│     stripImagesFromMessages()                               │
│     stripReinjectedAttachments()                            │
│                                                             │
│  ② 尝试 Session Memory 直挂（不额外调 API！）                │
│     trySessionMemoryCompaction()                            │
│     → 读取后台提取的 Session Memory 文件直接当摘要           │
│     → 如果成功，直接返回，省一次 API 调用                    │
│                                                             │
│  ③ 调 Forked Agent 生成摘要                                 │
│     → 借用主对话的 prompt cache（测试证明省了大量 token）     │
│     → suppressFollowUpQuestions（不在摘要后追问）            │
│                                                             │
│  ④ PTL（Prompt Too Long）重试保护                            │
│     → 摘要请求本身也超限 → truncateHeadForPTLRetry()         │
│     → 剥洋葱：一次剥掉 20% 旧分组，最多重试 N 次             │
│                                                             │
│  ⑤ 状态补偿（关键！）                                       │
│     → createPostCompactFileAttachments() — 恢复文件上下文    │
│     → createPlanAttachmentIfNeeded() — 恢复 Plan             │
│     → createSkillAttachmentIfNeeded() — 恢复 Skill           │
│     → getDeferredToolsDeltaAttachment() — 重新声明所有工具   │
└─────────────────────────────────────────────────────────────┘
```

### 6.3 压缩后的上下文面貌

```
[System 边界宣告]
  + [精简文本摘要]              ← 旧轮次被压缩成一段话
  + [正在查看的文件内文截取]     ← 状态补偿：文件上下文
  + [正在做的 Plan]             ← 状态补偿：执行计划
  + [仍然激活的 MCP/Skills]     ← 状态补偿：工具能力声明
```

**Agent 虽然"忘了"历史细节，但"知道"自己现在在做什么、手里有什么工具。**

### 6.4 Auto-Compact 触发 + 熔断器

```
shouldAutoCompact(messages, model, querySource, snipTokensFreed):
  → AUTOCOMPACT_BUFFER_TOKENS = 13_000  — 前置触发
  → 有效窗口 = getContextWindowForModel(model) - reservedForSummary(20K)

autoCompactIfNeeded():
  → 熔断器: tracking.consecutiveFailures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
    → 直接返回 { wasCompacted: false }，不再尝试
  → 这个熔断器每天省下约 250K 次死锁 API 调用
```

### 6.5 Session Memory 直挂（最精巧的优化）

```
trySessionMemoryCompaction():
  → 读取后台提取的 Session Memory 摘要文件
  → calculateMessagesToKeepIndex() — 精确裁切
    → 必须从后向前留足最低 token 原文（默认 10K-40K）
  → adjustIndexToPreserveAPIInvariants() — 面条并发防御
    → 截断位置不能落在 tool_use/tool_result 链中间
    → 不能切断 Assistant 的 Thinking 流
    → 碰到则向前平移 Index 合包
```

这种三层嵌套防御保证了：即使在一段高密度 tool_use + thinking 的混合流里，compact 之后 API 请求仍然合法。

### 6.6 Forked Agent 借主对话 Cache

```typescript
// 测试（2026年1月）证明: 借用主对话的 prompt cache 前缀
// 能省掉每次压缩所需的极大头部填充 Token 开销
const promptCacheSharingEnabled = getFeatureValue_CACHED_MAY_BE_STALE(
  'tengu_compact_cache_prefix', true
)
```

---

## 7. Multi-Agent 多代理体系

### 7.1 AgentTool — 统一入口 + 拓扑约束

```typescript
AgentTool.call():
  if (teamName && name):
    → spawnTeammate()        // swarm/teammate 路径
      → in-process / tmux / iTerm2 后端
  else:
    → runAgent()             // 普通 subagent 路径
      → query() 完整复用

拓扑约束:
  - teammate 不能无限嵌套 teammate
  - in-process teammate 不能再启动 background agent
```

### 7.2 Coordinator Mode — 主线程变成调度器

```
system prompt:
"You are Claude Code, an AI assistant that orchestrates
 software engineering tasks across multiple workers."

工作流:
  Research (Workers)      — 找文件、理解问题
  Synthesis (Coordinator) — 汇总研究、设计方案
  Implementation (Workers) — 按 spec 实施
  Verification (Workers)  — 跑验证

Worker 结果格式:
<task-notification>
  <task-id>agent-id</task-id>
  <status>completed|failed|killed</status>
  <summary>...</summary>
</task-notification>

→ 包装成 user-role message，coordinator 识别处理
```

### 7.3 Swarm — Team 是显式实体

```
TeamCreateTool:
  → 创建 team file（含 name/description/leadAgentId/members[]）
  → 重置 task list 目录
  → 设置 leader teamName
  → 更新 AppState.teamContext

协作机制:
  ① Task List: 共享任务池，teammate 调用 tryClaimNextTask() 主动认领
  ② Mailbox: .claude/teams/{team}/inboxes/{agent}.json
     - 带 lockfile 锁（防并发写入）
     - writeToMailbox() / readUnreadMessages() / markAsRead()
  ③ 权限桥接: leaderPermissionBridge
     - in-process teammate 借用 leader 的 ToolUseConfirmQueue
     - UI 带 workerBadge 标识
     - 如果 bridge 不可用，退回 mailbox 权限同步
  ④ SendMessageTool: 既是 resume 工具（继续已有 subagent），又是 mailbox 路由
```

### 7.4 in-process teammate 实现

```typescript
spawnInProcessTeammate():
  → 生成 agentId / taskId
  → 创建 abortController
  → 创建 teammateContext
  → registerTask(taskState)     // 注册到调度系统
  → InProcessTeammateTask        // 和 shell task、local agent task 同级

生命周期操作:
  - requestTeammateShutdown()
  - appendTeammateMessage()
  - injectUserMessageToTeammate()
  - findTeammateTaskByAgentId()
```

Teammate 不只是一次性执行器，而是一个可持续交互对象：可以继续发消息、切换到 transcript view、shutdown、从 agentId 反查 task。

### 7.5 权限模式切换的"双写"策略

```
sendModeChangeToTeammate(teammate, teamName, targetMode):
  → setMemberMode(...) — 先改本地配置（UI 立即可见）
  → writeToMailbox(...) — 再发 mailbox 通知远端 agent 更新运行态

cycleAllTeammateModes(teammates, teamName):
  → 先收集当前模式
  → 模式不一致 → 统一重置为 default
  → 模式一致 → 统一切换到下一模式
  → setMultipleMemberModes(...) + 逐个 mailbox 通知
```

---

## 8. 会话持久化

### 8.1 不是数据库快照，是 append-only JSONL 事件流

```
~/.claude/projects/<project>/
  ├── <session-id>.jsonl              ← 主 transcript
  └── <session-id>/
        └── subagents/
              └── agent-<id>.jsonl    ← subagent sidechain
```

### 8.2 写入路径：简单

```typescript
appendEntry(entry, sessionId):
  → 如果 session 文件还没 materialize → pendingEntries 排队
  → metadata (summary/title/tag/mode) → 写入主 transcript
  → content-replacement → 写入主或 sidechain
  → transcript message:
      → 主链: UUID 去重 → 写入 + messageSet.add(uuid) + persistToRemote
      → sidechain: 允许重复 UUID → 写入（fork 后需继承父上下文）
```

**主链去重，sidechain 保真，远端只跟主链走。**

### 8.3 写入队列：批量 flush

```typescript
drainWriteQueue():
  for (const [filePath, queue] of this.writeQueues):
    batch = queue.splice(0)
    content = batch.map(entry => jsonStringify(entry) + '\n').join('')
    await appendToFile(filePath, content)

appendToFile(filePath, data):
  → 权限: 0o600（文件）/ 0o700（目录）
  → 先 append，目录不存在则 mkdir 后重试
```

### 8.4 恢复路径：三重链路修复

```typescript
loadTranscriptFile(filePath):
  → 大文件: readTranscriptForLoad() — 只读 compact boundary 后有效部分
  → metadata: scanPreBoundaryMetadata() — boundary 前的 title/mode 不能丢
  → 按 entry type 分流到不同 Map
  → applyPreservedSegmentRelinks() — compact 保留段重连
  → applySnipRemovals() — 被 snip 的消息，幸存消息重接 parentUuid 到存活的祖先
  → buildConversationChain() — 补全 parallel tool result（恢复兄弟节点）
  → recoverOrphanedParallelToolResults() — 防止并发 tool 导致 result 丢失
  → checkResumeConsistency() — 检查 resume drift
```

**设计哲学：写入尽量简单，复杂性全部压到恢复路径。**

### 8.5 Metadata 尾部重挂

title、tag、mode 等元数据不是独立存储，而是写回 transcript。但为了确保**列表页的 lite reader（只读头尾 64KB）**能看到，会**周期性重挂到文件尾部**。

```
reAppendSessionMetadata():
  → 先从 tail 读取（吸收外部 SDK 改过的 title/tag）
  → append(last-prompt) → append(custom-title) → append(tag)
  → append(agent-name) → append(mode) → append(worktree-state)
```

### 8.6 Resume 完整恢复流水线

```
loadConversationForResume(source):
  → resolveSourceToLogOrJsonl(source)
  → restoreSkillStateFromMessages()  — 恢复 invoked skills
  → deserializeMessagesWithInterruptDetection()  — 检测中断 turn
  → filterUnresolvedToolUses() — 过滤未解决的 tool_use
  → 检测中断 → 注入 "Continue from where you left off."
  → processSessionStartHooks('resume') — 重新跑 session start hooks
  → 返回 { messages, fileHistory, contextCollapse, metadata ... }

ResumeConversation.tsx:
  → switchSession(sessionId)                      — 切换到旧 session
  → restoreAgentFromSession()                     — 恢复 agent identity
  → restoreSessionMetadata()                      — 恢复 title/tag/mode
  → restoreWorktreeForResume()                    — 恢复 worktree
  → adoptResumedSessionFile()                     — 绑定 transript 文件
  → render(<REPL initialMessages={result.messages} />)
```

"恢复"不是"把旧消息重新显示"，而是**一次运行时状态接管**。

### 8.7 远端 ingress：不是上传文件，是 append 链

```
persistToRemote(sessionId, entry):
  → PUT entry + Last-Uuid header（乐观并发控制）
  → 409 → 吸收服务端最新 UUID → 重试

hydrateRemoteSession(sessionId, ingressUrl):
  → 从远端拉回全部日志
  → 分别写入 foreground transcript + subagent transcript
```

本地 transcript 是运行时主副本，远端 ingress 是可回灌的增量副本。

---

## 9. Skills 技能机制

### 9.1 三种来源

| 类型 | 来源 | 说明 |
|------|------|------|
| File-based | `.claude/skills/` 目录 | 用户/项目级，最多来源 |
| Bundled | 源码内置 | 构建流程打包 |
| MCP Skills | MCP Server | 协议映射（**不执行内嵌 Shell**） |

### 9.2 发现与加载

```typescript
getSkillDirCommands(cwd):  // memoize 缓存
  → userSkillsDir = ~/.claude/skills
  → managedSkillsDir = 策略管理目录
  → projectSkillsDirs = cwd 向上爬取 .claude/skills/
  → additionalDirs = --add-dir 指定目录
  → legacyCommands = 旧版 /commands/ 目录

parallel:
  → loadSkillsFromSkillsDir(managedSkillsDir)   // 策略级
  → loadSkillsFromSkillsDir(userSkillsDir)      // 用户级
  → projectSkillsDirs.map(...)                   // 项目级（多目录）
  → additionalDirs.map(...)                      // --add-dir 级

合并 + 去重: deduplicateByRealpath() — fs.realpath 取 inode 级真实路径
```

### 9.3 SKILL.md 格式

```markdown
---
name: my-skill
description: 技能描述
when_to_use: 什么时候用
allowed_tools: [Bash, Read]
model: sonnet
effort: low | medium | high
user_invocable: true          # false = 仅供模型内部调用
paths: ["*.sql", "*.py"]     # 条件触发：文件变更时自动激活
context: inline | fork        # 执行模式
shell: bash | powershell      # 内嵌 Shell 类型
---

技能内容（Markdown）

可使用 !`command` 嵌入 Shell 命令，结果实时替换进 prompt
当前分支：!`git branch --show-current`
```

### 9.4 实例化流程

```typescript
createSkillCommand({ skillName, markdownContent, ... }):
  → getPromptForCommand(args, toolUseContext):
      1. substituteArguments(finalContent, args)       // 展开 CLI 参数
      2. 展开 ${CLAUDE_SKILL_DIR}、${CLAUDE_SESSION_ID}
      3. executeShellCommandsInPrompt(                 // 执行内嵌 Shell
           finalContent, toolUseContext, shell
         )
         → 扫描 !`command` 和 ```!\ncommand\n``` 两种语法
         → 权限检查: hasPermissionsToUseTool(shellTool, ...)
         → 调用 shellTool.call({ command })
         → 用函数形式替换（防止 $& 等特殊符号被输出污染）
         → loadedFrom === 'mcp' 时跳过（安全切断）
```

### 9.5 设计总结

| 特性 | 实现方式 |
|------|----------|
| 低门槛扩展 | Markdown + YAML，无需编写代码 |
| 实时系统上下文 | `!`command`` 内嵌 Shell，prompt 携带真实环境 |
| 条件精准触发 | `paths` 字段订阅文件变更（Hook 订阅模式） |
| 安全隔离 | MCP 来源跳过 Shell 执行，所有命令走统一权限 |
| 可组合 | 三种来源统一为 Command 对象，热加载 + memoize |

---

## 10. MCP 外部工具集成

### 10.1 四种传输协议

| 类型 | 适用场景 | 底层 |
|------|----------|------|
| `stdio` | 本地进程（最常用） | StdioClientTransport |
| `sse` | 远程 HTTP 长连接 | SSEClientTransport + OAuth |
| `ws` | WebSocket（IDE 集成） | WebSocketTransport |
| `http` | HTTP + claude.ai 代理 | StreamableHTTPClientTransport |

### 10.2 关键工程细节

**连接管理**：
- `connectToServer()` memoize 缓存，同配置只建一次连接
- `pMap(servers, connect, { concurrency: batchSize })` — 本地 3 并发，远程 20 并发

**超时控制**：
- `wrapFetchWithTimeout()` 用 `setTimeout` 而非 `AbortSignal.timeout()`
- 原因：`AbortSignal.timeout()` 在 Bun 中每请求泄漏 2.4KB 内存
- GET 请求不加超时（SSE 长连接不能被超时切断）

**描述截断**：`MAX_MCP_DESCRIPTION_LENGTH = 2048`，防止 OpenAPI 生成的 MCP 工具把 15-60KB 端点文档塞进工具描述

**认证雪崩防护**：
- 本地文件缓存 `~/.claude/mcp-needs-auth-cache.json`
- 某 Server 认证失败 → 标记 timestamp
- 后续 15 分钟内同 Server 所有调用直接短路返回 `needs-auth`
- 防止 100 个并发子调用同时发现 401，全部触发 token 刷新

**Session 过期检测**：
- `isMcpSessionExpiredError()`: 检测 HTTP 404 + JSON-RPC -32001
- 自动清连接缓存 + 重新 `connectToServer()`

**IDE 白名单**：`isIncludedMcpTool()` 只允许 `mcp__ide__getDiagnostics` 等少数高权限工具

**claude.ai 代理**：`createClaudeAiProxyFetch()` — 401 自动刷新 OAuth Token 后重试

### 10.3 工具池融合

```typescript
assembleToolPool(permissionContext, mcpTools):
  → builtInTools + allowedMcpTools
  → sort + uniqBy('name')     // 内建优先（名冲突时保留内建）
  → 返回统一 Tool[]
```

对模型而言，无论工具来自内建还是 MCP，都只是 `Tool` 接口的实例。命名格式 `mcp__<server>__<tool>` 确保可追溯。

---

## 11. Sandbox 安全沙箱

### 11.1 四层结构

```
① shouldUseSandbox()            — 路由决策：这条命令该不该进沙箱
② convertToSandboxRuntimeConfig() — 配置翻译：settings → runtime filesystem/network
③ bashPermissions.ts            — 权限联动：auto-allow 前先检查显式 deny/ask
④ cleanupAfterCommand()         — 清理：执行后宿主机级残留清理
```

### 11.2 路由决策（详细）

```text
if sandbox 本身不可用（平台/依赖/配置） → 不进沙箱
if 显式要求禁用 + 策略允许             → 不进沙箱
if 没有 command                        → 不进沙箱
if command 命中 excludedCommands       → 不进沙箱（便捷性豁免！非安全边界！）
否则                                   → 进沙箱

注释原文: excludedCommands is a user-facing convenience feature,
not a security boundary. It is not a security bug to be able to
bypass excludedCommands.
```

### 11.3 内建逃逸防护（具体攻击面）

**1. settings 文件强制 denyWrite**
settings.json / settings.local.json / managed settings drop-in 全在 denyWrite 列表里，沙箱内命令无法篡改。

**2. `.claude/skills` 强制 denyWrite**
注释："Skills have the same privilege level (auto-discovered, auto-loaded, full Claude capabilities)" — 如果允许写 skills 目录，等于允许注入未来会被自动加载的高权限能力。

**3. Git bare repo 逃逸防护**
攻击链：沙箱内种假 bare repo（HEAD + objects/ + refs/ + 恶意 core.fsmonitor）→ 宿主机无沙箱执行 git 命令 → Git 把当前目录当 repo → 恶意钩子执行。

防御：
- 构建 config 时：已存在的关键路径直接加 denyWrite
- 命令执行后：`scrubBareGitRepoFiles()` 清理被植入的 bare repo 文件
- 挂到 `cleanupAfterCommand()` → `Shell.ts` 命令结束时触发

### 11.4 与权限系统的关系

沙箱不替换权限——两套独立防线互相补位：
- **沙箱**：OS 级隔离（文件系统 + 网络）
- **权限**：应用层规则表达 + 用户确认

具体交互：
- auto-allow 前**先检查显式 deny/ask**
- 完整命令 → compound command 每个 subcommand → 都没有显式规则 → 才 auto-allow
- 沙箱的 allowlist 反向反馈给 `isPathAllowed()`，减少额外 permission prompt

### 11.5 配置热更新

```typescript
settingsChangeDetector.subscribe(() => {
  const newConfig = convertToSandboxRuntimeConfig(settings)
  BaseSandboxManager.updateConfig(newConfig)
})
```

改 settings 不需要重启，sandbox config 实时同步。

### 11.6 沙箱不可用时的透明度

`getSandboxUnavailableReason()` 返回具体原因（平台不支持 / WSL1 不是 WSL2 / 缺少依赖 / 不在 enabledPlatforms）。UI 侧 `SandboxDoctorSection` 展示错误和 warning。

**这不是"体验优化"——是防止用户以为开了沙箱实际没开的安全 footgun。**

---

## 12. 安全体系全景

### 12.1 同心圆防线

```
                    ┌──────────────────────────────────┐
                    │     遥测隐私隔离 + 密钥扫描       │ ← 数据出境防线
                 ┌──┼──────────────────────────────────┼──┐
                 │  │       权限模式分级管控             │  │ ← 策略防线
              ┌──┼──┼──────────────────────────────────┼──┼──┐
              │  │  │   Tool Permission 应用层拦截       │  │  │ ← 应用防线
           ┌──┼──┼──┼──────────────────────────────────┼──┼──┼──┐
           │  │  │  │       Sandbox 系统级隔离           │  │  │  │ ← 底层防线
           │  │  │  │  Unicode 清洗 / 路径校验           │  │  │  │
           └──┼──┼──┼──────────────────────────────────┼──┼──┼──┘
              └──┼──┼──────────────────────────────────┼──┼──┘
                 └──┼──────────────────────────────────┼──┘
                    └──────────────────────────────────┘
                                   │
                              宿主机（受保护）
```

### 12.2 权限模式分级

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| `default` | 每次操作弹出确认 | 日常使用 |
| `acceptEdits` | 自动批准文件编辑，命令仍需确认 | 轻度自动化 |
| `plan` | 只允许规划，不执行写操作 | 方案评审 |
| `auto` | 分类器自动判断，安全自动执行 | 高效开发 |
| `bypassPermissions` | 跳过权限检查（最危险） | CI/CD |

`bypassPermissions` 有两层防护：
- Statsig 远程开关（`tengu_disable_bypass_permissions_mode`）— 组织级管控
- 本地 `settings.permissions.disableBypassPermissionsMode`

### 12.3 危险命令检测（Auto Mode 防御）

```typescript
DANGEROUS_BASH_PATTERNS: [
  'python', 'python3', 'node', 'deno', 'ruby', 'perl', 'php',
  'npx', 'bunx', 'npm run', 'yarn run',
  'bash', 'sh', 'zsh', 'fish',
  'eval', 'exec', 'xargs', 'sudo',
  'ssh',
]
```

进入 Auto Mode 时：
- `stripDangerousPermissionsForAutoMode()` 剥离危险规则
- 退出时 `restoreDangerousPermissions()` 恢复
- 空规则或 `*` 通配符直接判定为极度危险

### 12.4 Unicode 隐写攻击防御

攻击模型：HackerOne #3086545 — MCP 工具返回内容中隐藏零宽字符 + Unicode Tag 字符，用户看不见但模型能识别。

防御：
```typescript
partiallySanitizeUnicode(prompt):
  迭代直到不再变化（最多 10 次）:
    ① NFKC 规范化（统一合成字符）
    ② 删除 /[\p{Cf}\p{Co}\p{Cn}]/gu  — Format/PrivateUse/Unassigned
    ③ 显式删除:
       - [​-‏] 零宽字符
       - [‪-‮] 方向格式化
       - [⁦-⁩] 方向隔离
       - [﻿]         BOM
       - [-]  私有使用区

recursivelySanitizeUnicode(value):
  → 递归处理 JSON 对象/数组，key 本身也脱敏
  → 应用于所有 MCP 工具调用的 input 字段
```

### 12.5 PII 隐私屏障

```typescript
type AnalyticsMetadata_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS = string
```

这个类型是 `string` 的别名，但强制开发者在上报遥测时显式做 `as` 断言——等于"我确认这不是代码或文件路径"。

效果：
- 普通 `string` 无法直接传给遥测函数（TypeScript 报错）
- 只有枚举值（`'auto'`、`'agent'` 等）可以安全传递
- PR 里出现这个类型名 → reviewer 知道要格外检查

### 12.6 Trust 建立时序

```
init() — Trust 前:
  → applySafeEnvironmentVariables()    // 只应用白名单 env var
  → initTelemetrySkeleton()            // 只注册 sink，不发事件
  // 不调用 attachAnalyticsSink()

main() — Trust 后:
  → initializeTelemetryAfterTrust()
  → applyFullEnvironmentVariables()    // 应用全部 env var
  → attachAnalyticsSink()              // 开始处理 telemetry 事件

设计原因：如果 CLAUDE.md 本身（或 @include 的外部文件）是攻击面，
Trust 建立之前的 env var 可能被恶意注入。推迟应用完整 env var
到 Trust 之后，极大缩小了"配置文件作为攻击面"的窗口期。
```

### 12.7 密钥扫描

30+ 种密钥模式：AWS/A3T/AKIA、GitHub PAT、OpenAI sk-、Anthropic sk-ant-api、Stripe、Shopify、Slack、npm、PyPI、私钥 PEM...

- `redactSecrets()` — 替换为 `[REDACTED]` 而非拒绝，保持上下文可读
- 扫描结果不返回命中文本 — 只返回"哪条规则命中"
- Anthropic 自家密钥前缀运行时拼接 `['sk','ant','api'].join('-')` — 避免字面量进入 bundle 被自动扫描误报

---

## 13. 产品信号机制

### 13.1 负面关键词检测

```typescript
matchesNegativeKeyword(input: string): boolean {
  const negativePattern = /\b(
    wtf|wth|ffs|omfg|shit(ty|tiest)?|dumbass|horrible|awful|
    piss(ed|ing)? off|piece of (shit|crap|junk)|
    what the (fuck|hell)|fucking? (broken|useless|terrible|awful|horrible)|
    fuck you|screw (this|you)|so frustrating|this sucks|damn it
  )\b/
  return negativePattern.test(lowerInput)
}
```

**这不是内容审查、不阻断输入、不改变模型行为。** 它的唯一调用点在 `processTextPrompt()`：

```typescript
const isNegative = matchesNegativeKeyword(userPromptText)
const isKeepGoing = matchesKeepGoingKeyword(userPromptText)
logEvent('tengu_input_prompt', {
  is_negative: isNegative,
  is_keep_going: isKeepGoing,
})
```

### 13.2 为什么是正则而非模型分类

1. 成本低（零额外 API）
2. 延迟低（本地同步计算）
3. 稳定（同样输入总是同样结果）
4. 只在输入路径（不需要模型推理）

这是一个**廉价的 frustration heuristic**。

### 13.3 完整产品链路

```
用户输入
  → matchesNegativeKeyword() → is_negative 标签
  → logEvent('tengu_input_prompt', { is_negative })
  → 正常进入 query

更高层 frustration detection
  → useFrustrationDetection(messages, isLoading, hasActivePrompt, ...)
  → 检测到用户明显 frustrated

REPL 渲染:
  {frustrationDetection.state !== 'closed' && <FeedbackSurvey />}
  → 弹出 TranscriptSharePrompt

用户同意后:
  submitTranscriptShare(messages, trigger='frustration', ...)
  → 上传 transcript + subagent transcripts + JSONL
```

**TranscriptShareTrigger 枚举**包含 `'frustration'` 作为正式触发类型——不是猜测，是正式枚举值。

### 13.4 这个信号在产品分析中的角色

`insights.ts` 中满意度标签：
```
frustrated / dissatisfied / likely_satisfied / satisfied / happy
```

facet 提取 prompt 举例："this is broken", "I give up" → frustrated。

"frustrated 用户"在 Claude Code 中是**正式的分析维度**，不是临时拼凑。`is_negative` 标签可作为漏斗、留存、失败会话、反馈转化率的分层维度。

---

## 14. 隐私治理

### 14.1 分级隐私控制

```typescript
type PrivacyLevel = 'default' | 'no-telemetry' | 'essential-traffic'

essential-traffic:
  → 关闭 Datadog、1P 事件日志、反馈调查、自动更新
  → 关闭 Grove、Release Notes、Model Capabilities 获取
  → "尽量少出网"的最严格选项
```

### 14.2 三层风险分类

```
第1类: 发给模型的内容（源码、命令输出、文件、Git状态、图片附件）
  → 最敏感，通常被忽视

第2类: 本地持久化的内容（transcript、memory、配置、OAuth缓存）
  → cleanupPeriodDays 默认保留 → 设为 0 才清理

第3类: 上传到外部的内容（遥测、team memory 同步、transcript sharing、
        Grove 训练、remote/bridge）
```

### 14.3 最有效的规避路线

```
1. 先关闭网络与遥测:
   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
2. 再关闭 transcript 与 memory:
   --no-session-persistence + CLAUDE_CODE_DISABLE_AUTO_MEMORY=1
3. 最后控制输入行为:
   不在 prompt 中粘贴密钥/token/env/生产配置/客户数据
```

**如果只做第 1 步而忽略第 2、3 步，隐私收益远低于预期。**

### 14.4 数据面叠加的长期影响

"进入模型的工作上下文 + 本地长期 transcript/memory + 外部同步/遥测"三者叠加后，形成**强于普通 CLI 助手的长期协作画像能力**。这不是单一日志事件的风险，而是能力叠加后的信息发散边界。

---

## 15. 前端组件体系

### 15.1 双中枢架构

```
App（Provider 装配: AppState + Stats + FpsMetrics）
  → REPL / FullscreenLayout
     → Messages                        ← 中枢1: "展示已经发生了什么"
     │    → VirtualMessageList         ← 虚拟滚动 + 搜索 + sticky prompt
     │         → MessageRow            ← 行级编排 + 状态预计算
     │              → Message          ← 消息类型分发
     │                   → messages/*  ← 30+ 种叶子组件
     │
     → PromptInput                     ← 中枢2: "组织下一步要做什么"
          → Footer / Suggestions / Notifications
          → QuickOpen / Search / Tasks / Teams / Bridge
```

Messages 不只是"渲染消息列表"——它是 transcript 语义整理器 + 视图转换器 + 性能热点总管：
- fullscreen / transcript / brief-only / 普通 prompt / remote 五种模式适配
- read/search 折叠、hook 摘要折叠、teammate shutdown 折叠、grouped tool use 合并
- VirtualMessageList 做虚拟滚动 + 搜索 index + 跳转 + sticky prompt 跟踪

PromptInput 不是传统 TextInput —— 它是一个**会话控制台**：
- 输入缓冲 + arrow key history
- prompt suggestion / typeahead / slash command
- model 选择、fast mode、permission mode 切换
- quick open、global search、bridge、teams、background tasks
- 普通输入与 vim 输入并存、快捷键冲突屏蔽

### 15.2 平台控制面

| 组件族 | 文件量 | 职责 |
|--------|--------|------|
| `permissions` | 51 | 工具类型 → 审批 UI 路由 + bash/file/web/skill 等专项审批 |
| `messages` | 41 | 30+ 种消息叶子渲染器 + tool result 子组件 |
| `agents` | 26 | agent 列表/详情/编辑/创建向导（步骤数组驱动） |
| `PromptInput` | 21 | 输入编排 + footer + suggestions + notifications |
| `mcp` | 13 | server 管理 + transport/auth/tool 三层视图（状态机驱动） |
| `tasks` | 12 | 多类型后台任务归一化（bash/remote/agent/teammate/workflow/dream） |
| `design-system` | 16 | Dialog/Tabs/Pane/ThemeProvider（自建终端设计系统） |

### 15.3 关键设计模式

**1. orchestrator / leaf 分层**
- orchestrator：管理状态、切换视图、调用 hooks
- leaf：只负责渲染某个具体子块
- 新增消息类型只需补一个 leaf component，不用改写总体结构

**2. hooks 驱动**
- 组件"显示结构"和"交互行为"明显拆开
- 新增能力优先新增 hook，不把单个组件写胖
- `useAppState(selector)` 强制切片订阅，避免无关组件重渲染

**3. 终端性能优化**
- VirtualMessageList 高度缓存与 terminal columns 联动
- OffscreenFreeze 离屏冻结
- `shouldRenderStatically()` 消息稳定渲染判定
- `areMessageRowPropsEqual()` / `areMessagePropsEqual()` 细粒度重渲染控制
- per-item 闭包最小化（`VirtualItem` 稳定包装层）

**4. 叶子组件的"最后一层判定"**
很多组件在叶子层才做语义判定：
- `AssistantTextMessage`：把模型输出/平台错误/用户中断/限流/余额/上下文超限 8 种异常统一路由
- `UserToolResultMessage`：把 tool_result 按取消/拒绝/错误/成功四种语义拆解
- `BackgroundTask`：把结构复杂的任务状态压缩成一行可扫描的摘要

### 15.4 状态管理：切片订阅

```typescript
useAppState(selector)  // 不是返回整棵树，强制切片
useSetAppState()       // 只写不读，不因全局变化重渲染
useAppStateStore()     // 直接拿 store（极少用）

// 组件里
useAppState(s => s.messages)
useAppState(s => s.tasks)
// 不会因为 tasks 更新导致 messages 组件重渲染
```

### 15.5 design-system 的 Dialog 设计

```typescript
Dialog:
  → isCancelActive（Ctrl+C 的状态管理）
  → exitState: 按过一次退出键 → "Press keyName again to exit"
  → 标准操作: Enter confirm / Esc cancel
  → hideBorder 为真只返回内容，否则包 Pane
  → 使用 useExitOnCtrlCDWithKeybindings 统一退出行为
```

把终端对话框的"取消、退出、边框、输入指南"全部统一了。

---

## 16. ChatBI v2 完整借鉴清单

### 16.1 核心架构机制

| Claude Code 机制 | ChatBI v2 对齐方案 | 优先级 |
|-----------------|-------------------|--------|
| **Query while(true) 主循环** | SQL 生成不是一次调用，是循环：生成→执行→自检→修正→再生成 | P0 |
| **Tool 安全默认（Fail-Closed）** | SQL 执行默认需确认、图表修改走 ask、危险操作必须 admin | P0 |
| **Prompt 分层 section Cache** | 语义层 + Skills（可缓存），用户问题 + 对话历史（不可缓存） | P0 |
| **Memory 文件化 + Relevant Recall** | Agent 记忆用 .md 文件，MEMORY.md 做索引，按需召回最多 5 条 | P1 |
| **压缩 + 状态补偿** | 对话压缩后补回语义层、当前 SQL、筛选条件 | P0 |
| **Multi-Agent: State Store 通信** | Leader + 子 Agent 走 PostgreSQL Store 读/写（参考海泰 ContextKey） | P0 |
| **append-only Checkpointer** | 写入简单、恢复时重建状态（参考 JSONL + loadTranscript 思路） | P1 |
| **Skills: 条件触发 + 热更新** | SKILL.md 外置，支持 paths 条件触发 + 变更即时生效 | P0 |
| **AskUserQuestion 即 Tool** | Agent 不确定时调用"询问用户"工具暂停等待（参考关键节点自动暂停） | P0 |

### 16.2 安全机制

| Claude Code 机制 | ChatBI v2 对齐方案 | 优先级 |
|-----------------|-------------------|--------|
| **安全默认（buildTool 保守值）** | 所有操作默认需确认，只有显式声明安全的才自动放行 | P0 |
| **权限模式分级** | default/auto/admin 三级，admin 才能改数据源/语义层配置 | P0 |
| **双防线（权限+沙箱）** | SQL AST 拦截 DML/DDL + 只读账号 + 行数限制（三层） | P0 |
| **Unicode 清洗** | 用户输入 + 数据库字段值进入 LLM 上下文前清洗隐写字符 | P1 |
| **Trust 建立时序** | 密钥启动时检测占位符 → 拒绝启动 + 明确提示 | P0 |
| **PII 类型屏障** | 审计日志中 SQL 内容脱敏，用户 PII 不进入日志 | P1 |
| **降级告警（WARNING/ERROR 分级）** | Redis 不可用→WARNING；限流/登录锁降级→ERROR | P0 |
| **危险命令检测（Auto 模式剥离）** | Agent 确认前自动检测：DROP/ALTER/INSERT/DELETE + LOAD_FILE/SLEEP | P0 |

### 16.3 工程与产品实践

| Claude Code 机制 | ChatBI v2 对齐方案 | 优先级 |
|-----------------|-------------------|--------|
| **配置热更新（subscribe 模式）** | 语义层/Skills 修改后实时同步，不重启服务 | P1 |
| **memoize 缓存** | 语义层解析、Skills 加载、RAG 检索结果缓存 | P1 |
| **熔断器（连续失败 N 次停止）** | 自愈连续失败 3 次 → 停止 + 返回明确错误 | P0 |
| **并发/串行分批（partitionToolCalls）** | SQL 生成和图表生成可并发，SQL 执行和自愈必须串行 | P1 |
| **可观测性（dump-prompts + /context）** | 导出实际发给 LLM 的 prompt，统计各 section token 消耗 | P1 |
| **负面信号检测** | 检测"没用/不对/错了/垃圾"→触发反馈收集或自动询问 | P2 |
| **Session Memory 直挂（省 API 调用）** | 长对话摘要直接读上次的摘要文件，不重新调 LLM 生成 | P2 |
| **fork child 复用父 prompt 字节** | 追问时复用上一轮的语义层 context 缓存（不讲新生成） | P1 |
| **双写策略（本地+远端通知）** | 配置变更先写本地（立即可见），再通知远端同步 | P2 |
| **orchestrator/leaf 分层** | 前端消息组件：外层编排 + 内层专职渲染 | P2 |

### 16.4 可观测与调试

| Claude Code 机制 | ChatBI v2 对齐方案 |
|-----------------|-------------------|
| `dump-prompts` 导出完整 API 请求 | 记录每次 LLM 调用的完整 prompt + response |
| `/context` 逐段统计 token | 展示语义层/Skills/历史对话各占多少 token |
| `pipeline_trace` 记录 Agent 调用链 | 展示 Intent → Schema → SQL → Chart 每步耗时+token |
| `checkResumeConsistency` 恢复一致性审计 | Checkpointer 恢复后校验 messageCount 与实际 chain 长度 |

---

## 17. 补充：MCP 集成深度细节

### 17.1 工具描述的截断保护

```
MAX_MCP_DESCRIPTION_LENGTH = 2048

注释原文: OpenAPI-generated MCP servers have been observed dumping
15-60KB of endpoint docs into tool.description; this caps the p95 tail
without losing the intent.
```

不是猜想，是真实线上经验——某些 OpenAPI 自动生成的 MCP 服务器会把整个端点文档（15-60KB）塞进一条工具描述。2048 字符的上限是工程数据驱动的。

### 17.2 认证雪崩防护的完整实现

问题：一个 Token 失效 → 100 个并发工具子调用同时发现 401 → 全部发起 Token 刷新 → 认证雪崩。

```typescript
// 缓存文件: ~/.claude/mcp-needs-auth-cache.json
type McpAuthCacheData = Record<string, { timestamp: number }>

getMcpAuthCache():
  → authCachePromise ??= readFile(path)
    .then(data => JSON.parse(data))
    .catch(() => ({}))     // 首次/文件损坏 → 空对象
  → Promise 结果 memoize（并发读不重复 fs.readFile）

setMcpAuthCacheEntry(serverId):
  → cache[serverId] = { timestamp: Date.now() }
  → 异步写入（不阻塞调用方）

isMcpAuthCached(serverId):
  → cache[serverId] 存在 && Date.now() - timestamp < 15*60*1000
  → 返回 true → 所有后续调用直接短路返回 needs-auth
  → MCP_AUTH_CACHE_TTL_MS = 15 * 60 * 1000  // 15 分钟
```

### 17.3 超时控制：为什么不用 AbortSignal.timeout()

```typescript
wrapFetchWithTimeout(baseFetch):
  → method === 'GET' → 不加超时（SSE 长连接不能被切断）
  → 其他方法 → setTimeout + AbortController
    → timer.unref()   // 不阻止进程退出

注释原文: AbortSignal.timeout() leaks ~2.4KB per request in Bun
           before GC collects it.
```

`timer.unref()` 也是关键——确保定时器不会阻止 Node.js 进程正常退出。这是跨运行时（Bun/Node）兼容性工程的典型案例。

### 17.4 Session 过期检测与自动恢复

```typescript
isMcpSessionExpiredError(error):
  → error.code !== 404 → false（只处理 404）
  → error.message.includes('"code":-32001')
    || error.message.includes('"code": -32001')
  → MCP 协议规范：Session 过期 = HTTP 404 + JSON-RPC -32001

检测到后:
  → connectToServer.cache.clear()     // 清除连接缓存
  → 重新 connectToServer()            // 建立新连接
```

### 17.5 IDE 工具白名单

```typescript
ALLOWED_IDE_TOOLS = [
  'mcp__ide__getDiagnostics',
  'mcp__ide__getOpenEditorFiles',
  // ... 仅少数高权限工具通过白名单
]

isIncludedMcpTool(tool):
  → !tool.name.startsWith('mcp__ide__') → true  // 非 IDE 工具全部允许
  → ALLOWED_IDE_TOOLS.includes(tool.name) → true // IDE 工具白名单
  → 其他 → false  // IDE 推送的其他工具被拦截
```

这意味着 IDE 可以向 Claude 推送工具，但不是所有推送都被接受——只有白名单内的能力才进入工具池。

### 17.6 并发连接控制

```typescript
getMcpServerConnectionBatchSize():
  → 本地: parseInt(env.MCP_SERVER_CONNECTION_BATCH_SIZE) || 3
  → 远程: parseInt(env.MCP_REMOTE_SERVER_CONNECTION_BATCH_SIZE) || 20

pMap(servers, connectToServer, { concurrency: batchSize })
```

本地只并发 3 个连接（stdio 进程启动有开销），远程 20 个（网络 IO 并发价值高）。

### 17.7 claude.ai 代理的 OAuth 自动刷新

```typescript
createClaudeAiProxyFetch(innerFetch):
  return async (url, init) => {
    const response = await innerFetch(url, init)
    if (response.status === 401):
      → await refreshOAuthToken()         // 静默刷新
      → return innerFetch(url, {           // 用新 token 重试
          ...init,
          headers: getUpdatedAuthHeaders()
        })
    return response
  }
```

用户完全无感知——401 了自动刷新 token 然后重试。

### 17.8 MCP 设置 UI 的状态机

```
MCPSettings 状态机:

list
  → server-menu
     → server-tools
        → tool-detail → 返回 server-tools
     → 返回 list

list
  → agent-server-menu
     → server-tools
        → tool-detail → 返回 server-tools
     → 返回 list

每个 server 在列表中展示的不仅仅是名称，而是:
  - transport 类型（sse/http/stdio/claudeai-proxy）
  - 认证状态（通过 ClaudeAuthProvider.tokens() 检测）
  - 工具数量
  - 连接状态（connected / pending / reconnecting / needs-auth / failed）
```

---

## 18. 补充：竞品对比（Claude Code vs Codex vs Gemini CLI vs Aider vs Cursor）

### 18.1 与 Codex (OpenAI) 的差异

- Codex：CLI + IDE + web + app + SDK + Slack，覆盖所有入口，强调统一产品线与企业治理
- Claude Code：更强调本地运行时、权限上下文、teammate/swarm 协作、memory 文件化

> Codex 更像"覆盖本地与云端的通用 coding agent 平台"
> Claude Code 更像"把长期会话、权限、memory 和多 agent 运行时压到本地内核里"

### 18.2 与 Gemini CLI (Google) 的差异

- Gemini CLI 已是成熟的 CLI agent：built-in tools、MCP、checkpointing、sandboxing、trusted folders、telemetry
- Claude Code 在此基础上继续深化：memory 分层更深、agent runtime 更重（teammate/snapshot/team memory/swarm backends）

> Gemini CLI 是高标准的通用 CLI agent 基线
> Claude Code 在此基础上继续向长期记忆和多 agent 协作深化

### 18.3 与 Aider 的差异

- Aider：终端 pair programming，轻量、repo map、git 集成、lint/test 闭环
- Claude Code：状态管理更复杂（终端工作台）、memory 分层、平台化能力（MCP/bridge/swarm）

> Aider 是强编辑代理
> Claude Code 是通用多角色 agent 平台

### 18.4 与 Cursor 的差异

- Cursor：IDE 主导、background agent 强、远程隔离执行环境
- Claude Code：本地终端内核主导、memory 分层、permission 主干化、swarm 后端

> Cursor 更像"IDE 驱动的远程代理平台"
> Claude Code 更像"本地 agent 操作系统，远程只是扩展层"

### 18.5 真正的差异化

Claude Code 与同类产品拉开差距的不是"功能多"，而是三点同时成立：
1. 统一的 query / agent / tool / permission 内核
2. 文件化、可审计、分层的 memory 系统
3. local-first，但能平滑扩展到 remote / bridge / swarm

很多产品能做到其中一两点，但很少三点同时成立。

---

## 19. 补充：Feature Flags 编译裁剪与内部/外部版本分流

### 19.1 全仓 89 个 feature(...) 开关

源码中全仓扫描到 89 个 `feature(...)` 开关，代表性分类：

| 类别 | 开关示例 | 说明 |
|------|---------|------|
| 交互与产品 | `VOICE_MODE`, `BUDDY`, `TERMINAL_PANEL`, `QUICK_SEARCH` | 产品级功能门控 |
| Agent/协作 | `FORK_SUBAGENT`, `COORDINATOR_MODE`, `TEAMMEM`, `AGENT_MEMORY_SNAPSHOT` | Multi-Agent 能力 |
| Memory/Compact | `EXTRACT_MEMORIES`, `REACTIVE_COMPACT`, `CACHED_MICROCOMPACT` | 上下文管理 |
| 平台扩展 | `WORKFLOW_SCRIPTS`, `MCP_RICH_OUTPUT`, `MCP_SKILLS`, `WEB_BROWSER_TOOL` | 外部能力接入 |
| 内部产品 | `KAIROS`, `KAIROS_BRIEF`, `KAIROS_DREAM`, `KAIROS_GITHUB_WEBHOOKS` | 内部产品线 |
| 实验与观测 | `PERFETTO_TRACING`, `ENHANCED_TELEMETRY_BETA`, `SLOW_OPERATION_LOGGING` | 性能与观测 |
| 高级能力 | `ULTRAPLAN`, `TORCH`, `LODESTONE`, `CHICAGO_MCP` | 更激进或内部代号 |

### 19.2 编译期分流机制

```
源码中的能力
    |
    +-- 编译期分流
    |      |
    |      +-- USER_TYPE === 'ant'
    |      |      → 注入 INTERNAL_ONLY_COMMANDS（20+ 个内部命令）
    |      |
    |      +-- feature('...')
    |             → bun:bundle 编译期开关
    |             → 外部构建 dead-code eliminate
    |
    +-- 运行期分流
           |
           +-- isEnabled()
           |      → 账号类型、平台、实验 gate、环境变量
           |
           +-- isHidden
                  → 即使存在，也不出现在 help/typeahead
```

### 19.3 内部命令列表

`INTERNAL_ONLY_COMMANDS` 包含但不限于：
`backfillSessions`, `breakCache`, `bughunter`, `commit`, `commitPushPr`, `ctx_viz`, `goodClaude`, `issue`, `initVerifiers`, `mockLimits`, `bridgeKick`, `resetLimits`, `teleport`, `antTrace`, `perfIssue`, `env`, `oauthRefresh`, `debugToolCall`, `agentsPlatform`, `autofixPr`

部分命令在当前快照中是 `stub`（`isEnabled: () => false, isHidden: true`），真实实现要么在内部仓库、要么被构建流程替换、要么已下线但接口位保留。

### 19.4 Beta Headers

```typescript
// src/constants/betas.ts
context-1m-2025-08-07
web-search-2025-03-05
fast-mode-2026-02-01
token-efficient-tools-2026-03-28
advisor-tool-2026-03-01
afk-mode-2026-01-31
cli-internal-2026-02-09
```

这些字符串透露出：项目和上游模型能力之间通过**显式 beta 协议头协商**，一些今天在产品表面不一定可见的能力其实已经在代码里占了稳定接口位。

### 19.5 undercover 模式

```typescript
// src/utils/undercover.ts

在公开/开源仓库里:
  → 内部版默认进入 undercover 模式
  → 自动加安全说明（避免 commit message 或 PR 泄露内部代号、版本号、项目名）
  → 甚至明确禁止写 "Claude Code" 或暴露自己是 AI
```

这说明项目在"公开协作场景下如何伪装内部 agent 身份"这件事上有明确设计。

---

## 20. 补充：Prompt 工程深层细节

### 20.1 max_tokens 的 Slot 预约优化

```typescript
// 真实源码注释
// Capped default for slot-reservation optimization. BQ p99 output = 4,911
// tokens, so 32k/64k defaults over-reserve 8-16× slot capacity. With the cap
// enabled, <1% of requests hit the limit; those get one clean retry at 64k

CAPPED_DEFAULT_MAX_TOKENS = 8_000
ESCALATED_MAX_TOKENS = 64_000
```

什么意思：虽然 Claude 模型支持 32K 或 64K 输出，但实际 P99 只有 4911 tokens。如果每次都申请 32K 的输出 Slot，8-16 倍的 Slot 容量被浪费。默认限制 8000 tokens 后，只有 <1% 的请求会触发截断，这些请求会走一次干净的 64K 重试。

**这是真正的 API 成本优化工程——不是简单的"设个上限"，而是基于生产数据的 Slot 预约策略。**

### 20.2 CLAUDE.md 的分级信任模型

```
Managed   /etc/claude-code/CLAUDE.md             → 最高（系统管理员）
User      ~/.claude/CLAUDE.md                    → 高（用户全局）
Project   {cwd}/CLAUDE.md, {cwd}/.claude/CLAUDE.md → 中（项目约定）
Local     .claude/rules/*.md                     → 最低（本地约定）
```

### 20.3 @include 的深度限制

```typescript
MAX_INCLUDE_DEPTH = 5  // 防御性设计

processMemoryFile(filePath, type, processedPaths, includeExternal, depth=0):
  → normalizedPath 已经在 processedPaths → 跳过（防循环引用）
  → depth >= 5 → 静默截断（不报错）
  → 解析 symlink 真实路径 → 双重去重（/tmp → /private/tmp）
```

### 20.4 CLAUDE.md 排除列表

```typescript
isClaudeMdExcluded(filePath, type):
  → settings.claudeMdExcludes 配置的路径 → 跳过
  → 让用户显式控制"哪些文件不要自动加载"
```

### 20.5 getSystemPrompt() 的 section 构造细节

```typescript
getSystemPrompt():
  return [
    getSimpleIntroSection(outputStyleConfig),        // "You are an interactive agent..."
    getSimpleSystemSection(),                         // 基础规则
    outputStyleConfig === null
      || outputStyleConfig.keepCodingInstructions === true
      ? getSimpleDoingTasksSection()                  // coding agent 工作规则
      : null,
    getActionsSection(),                              // 行为约定
    getUsingYourToolsSection(enabledTools),           // 工具使用方式
    getSimpleToneAndStyleSection(),                   // 语气风格
    getOutputEfficiencySection(),                     // 输出效率
    ...(shouldUseGlobalCacheScope()
      ? [SYSTEM_PROMPT_DYNAMIC_BOUNDARY] : []),      // 缓存分隔
    ...resolvedDynamicSections,                       // 动态段
  ].filter(s => s !== null)
```

---

## 21. 补充：组件性能优化的逐函数拆解

### 21.1 computeSliceStart — 锚点窗口算法

```typescript
computeSliceStart(collapsed, anchorRef, cap, step):
  → 根据 anchorRef.current.uuid 在 collapsed 中定位当前锚点
  → uuid 丢失 → 退回到历史 index
  → collapsed.length - start > cap + step → 推进窗口
  → 用当前 start 对应的 message 反向刷新 anchor

目的:
  → 消息分组重排时窗口不抖动
  → compaction 后不突然回到 0
  → 终端 scrollback 不因前部裁切不断重置
```

### 21.2 shouldRenderStatically — 消息冻结策略

```typescript
shouldRenderStatically(message, streamingToolUseIDs, inProgressToolUseIDs,
                       siblingToolUseIDs, screen, lookups):
  → transcript 模式 → 全部静态（true）
  → 普通 user/assistant/attachment:
      - 没有 toolUseID → 可静态
      - 在 streamingToolUseIDs 或 inProgressToolUseIDs → 保持动态
      - 有未解决 PostToolUse hook → 保持动态
      - 否则: sibling tool use 全部 resolved → 可静态
  → system api_error → 保持动态
  → grouped_tool_use → 组内全部 resolved → 可静态
  → collapsed_read_search → prompt 模式永远动态
```

**这个函数是消息稳定渲染策略的核心**：不是简单判断"是否 streaming"，而是区分了 7 种状态场景。冻结后不再重渲染，节省终端 BLIT 开销。

### 21.3 areMessagePropsEqual / areMessageRowPropsEqual — 细粒度重渲染控制

```typescript
areMessageRowPropsEqual(prev, next):
  → 只在真正影响当前行显示时才返回 false
  → 避免每次全局消息变化都导致整棵 transcript 行级重渲染

areMessagePropsEqual(prev, next):
  → 比较 message.uuid
  → 只有当前消息真有 thinking 内容时，才关心 lastThinkingBlockId
  → 仅当该消息是 latestBashOutputUUID 时关心 bash 更新
  → 对 transcript mode / containerWidth / verbose 做细粒度比较
```

### 21.4 ClassifierCheckingSubtitle — shimmer 性能隔离

源码注释写得很直白：如果 shimmer 时钟留在大体量的审批对话框里，会把整棵 `PermissionDialog + Select + children` 在 classifier 检查期间高频重渲染。所以把 shimmer 拆成独立组件，只让它自己以 20fps 重绘。

### 21.5 stickyPromptText — sticky prompt 文本提取

```typescript
stickyPromptText(msg):
  → WeakMap 缓存

computeStickyPromptText(msg):
  → 只识别两类"真实用户输入":
      - user message 中的 text block
      - attachment.type === 'queued_command' 的 mid-turn 用户输入
  → stripSystemReminders(raw) — 过滤系统 reminder
  → 文本以 < 开头或为空 → 不是用户真实输入
```

### 21.6 VirtualMessageList — 虚拟滚动的终端适配

```typescript
VirtualMessageList:
  → keysRef: 对 append-only 消息流做增量 key 追加
  → useVirtualScroll(scrollRef, keys, columns) 取得:
      - range（可见范围）
      - measureRef（高度测量）
      - offsets（偏移量）
      - getItemTop / getItemElement
      - scrollToIndex
  → useImperativeHandle 暴露光标导航接口:
      - enterCursor / navigatePrev / navigateNext
      - navigatePrevUser / navigateNextUser
      - navigateTop / navigateBottom
  → jumpState + scanRequestRef: 跳转、搜索、高亮
  → 高度缓存与 terminal columns 联动（终端换宽导致换行变化后重新计算）
```

### 21.7 PromptInputFooterSuggestions — 轻量窗口化裁剪

```typescript
PromptInputFooterSuggestions:
  → 根据 overlay 和终端 rows 计算 maxVisibleItems
  → maxColumnWidth = max(allPathWidths) + 5
  → 用 selectedSuggestion 算滚动窗口: startIndex / endIndex
  → 只渲染 suggestions.slice(startIndex, endIndex)

SuggestionItemRow:
  → unified suggestions: 单行布局 + truncatePathMiddle
  → 普通 suggestions: "主列 + tag + description" 三段式
  → 根据 columns 动态算 maxPathLength / availableWidth / descriptionWidth
```

### 21.8 BackgroundTask — 状态归约器

```typescript
BackgroundTask:
  → 按 task.type 分发:
      local_bash → ShellProgress
      remote_agent → RemoteSessionProgress
      local_agent → (agent 描述)
      in_process_teammate → describeTeammateActivity()
      local_workflow → TaskStatusText
      monitor_mcp → TaskStatusText
      dream → TaskStatusText
  → 所有路径统一: truncate(..., activityLimit, true)
  → 把结构复杂的任务状态压缩成一行
```

### 21.9 AssistantTextMessage — 异常语义路由器

```typescript
AssistantTextMessage:
  → isEmptyMessageText(text) → 空文本
  → isRateLimitErrorMessage(text) → RateLimitMessage
  → 硬编码分流 8 种特殊常量:
      NO_RESPONSE_REQUESTED
      PROMPT_TOO_LONG_ERROR_MESSAGE
      CREDIT_BALANCE_TOO_LOW_ERROR_MESSAGE
      INVALID_API_KEY_ERROR_MESSAGE
      TOKEN_REVOKED_ERROR_MESSAGE
      API_TIMEOUT_ERROR_MESSAGE
      CUSTOM_OFF_SWITCH_MESSAGE
      ERROR_MESSAGE_USER_ABORT
  → startsWithApiErrorPrefix(text) → 通用 API 错误
  → 不命中任何特殊分支 → 普通 Markdown 文本
```

---

## 22. 补充：用户信息面全景分析

### 22.1 六层信息面

```
┌─────────────────────────────────────────────────────────────────────┐
│  层级          │  信息类型              │  敏感程度                  │
├─────────────────────────────────────────────────────────────────────┤
│  模型上下文     │  源码、命令、文件内容   │  极高（最容易忽视）        │
│                │  对话历史、工具结果     │                           │
│                │  Git 状态、CLAUDE.md    │                           │
│                │  图片、MCP 返回内容     │                           │
├─────────────────────────────────────────────────────────────────────┤
│  本地持久化     │  transcript JSONL       │  高（可恢复、可检索）      │
│                │  session metadata       │                           │
│                │  OAuth 缓存             │                           │
│                │  memory 文件            │                           │
│                │  agent transcript       │                           │
├─────────────────────────────────────────────────────────────────────┤
│  Memory 积累    │  用户偏好、角色背景     │  高（跨 session 延续）     │
│                │  项目事实、参考信息     │                           │
│                │  会话摘要               │                           │
│                │  agent 角色记忆          │                           │
│                │  团队共享记忆           │                           │
├─────────────────────────────────────────────────────────────────────┤
│  Telemetry      │  deviceId, sessionId    │  中（元数据而非内容）      │
│                │  accountUuid, orgUuid   │                           │
│                │  repo remote hash        │                           │
│                │  工具使用事件            │                           │
│                │  文件路径 hash/内容 hash  │                           │
├─────────────────────────────────────────────────────────────────────┤
│  云同步         │  团队 memory pull/push   │  中~高（取决于内容）       │
│                │  组织知识条目            │                           │
├─────────────────────────────────────────────────────────────────────┤
│  主动上传       │  transcript 分享        │  高（包含完整会话）        │
│                │  Grove 训练数据          │  极高（影响模型训练）      │
└─────────────────────────────────────────────────────────────────────┘
```

### 22.2 不是某一个日志事件的风险

**真正的风险**：进入模型的工作上下文 + 本地长期 transcript/memory + 外部同步/遥测/分享，三者叠加后形成"长期、可恢复、可检索、可同步"的用户工作画像。这不是单点数据打点的风险。

### 22.3 PII 路由保护

```typescript
// _PROTO_* 前缀的字段 → PII 标记，只进入有权限的 1P 导出渠道
// 发送到 Datadog 前 → stripProtoFields() 删除所有 _PROTO_ 字段

stripProtoFields(metadata):
  → 删除所有 _PROTO_ 开头的字段
  → 再发给 Datadog（通用日志流不能有 PII）
```

### 22.4 MCP 工具名脱敏

```typescript
sanitizeToolNameForAnalytics(toolName):
  if (toolName.startsWith('mcp__')):
    return 'mcp_tool'     // 不暴露用户的 MCP 服务器名称
  return toolName
```

---

## 23. 补充：隐藏特性与彩蛋

### 23.1 Buddy 系统 — 完整的人格化子系统

这不是"一个吉祥物贴图"，而是一套完整设计：

**companion 的两层拆分**：
- `bones`：确定性骨架 — `rarity`, `species`, `eye`, `hat`, `shiny`, `stats`（通过 seeded PRNG 由 userId hash 决定，不持久化）
- `soul`：模型生成 — `name`, `personality`（持久化存储）

**稀有度权重**：`common 60 / uncommon 25 / rare 10 / epic 4 / legendary 1`
**物种**：18 种（duck/goose/blob/cat/dragon/octopus/owl/penguin/turtle/snail/ghost/axolotl/capybara/cactus/robot/rabbit/mushroom/chonk）
**属性**：DEBUGGING / PATIENCE / CHAOS / WISDOM / SNARK（一项峰值、一项短板）

**运行时交互**：
- 宽终端：完整 ASCII sprite + 500ms tick idle/fidget/blink 动画
- 窄终端：退化成单行 face + name/quip
- 被 pet：2.5 秒爱心上浮动画
- 说话：10 秒 speech bubble + 最后 3 秒 fade
- 用户滚动 transcript：自动关掉气泡

**上线节奏**：2026 年 4 月 1-7 日 teaser window → 4 月开始 `isBuddyLive()` 正式上线

### 23.2 内部命令 stub 模式

部分命令目录只有一个文件：
```js
export default { isEnabled: () => false, isHidden: true, name: 'stub' };
```

含义：外部快照不提供真实实现，但命令名和接线位保留。真正实现要么在内部仓库、要么被构建流程替换、要么已下线但接口还在。

### 23.3 周边与品牌

- `/stickers` → 打开 https://www.stickermule.com/claudecode（实体周边购买）
- `guest passes` → 3 张 guest pass / 推荐 upsell
- 这些细节对主能力不必要，但对产品气质很重要

---

## 24. 逐行补充：架构层代码真相

### 24.1 cli.tsx 的早期分流逻辑

```typescript
// src/entrypoints/cli.tsx 结构伪代码
async function main() {
  const argv = parseArgs(process.argv)

  // 快路径分流 —— 命中则执行并退出，不进入 main.tsx
  if (argv['--version'])    { console.log(version); process.exit(0) }
  if (argv['--dump-system-prompt']) { await dumpSystemPrompt(); process.exit(0) }
  if (argv['remote-control'])      { return runRemoteControl(argv) }
  if (argv['daemon'] || argv['bg'] || argv['runner']) { return runDaemonOrBackground(argv) }

  // 兜底：进入完整主启动器（React、Ink、MCP 等全部加载）
  await import('./main.tsx').then(m => m.main(argv))
}
```

设计意图：普通快速命令（--version、--dump-prompt）不需要加载 React、Ink、MCP 等全部依赖。启动速度快且副作用少。

### 24.2 main.tsx 的总控能力装配

```typescript
// src/main.tsx —— 关键 import 反映职责范围
import { init, initializeTelemetryAfterTrust } from './entrypoints/init.js'
import { launchRepl }              from './replLauncher.js'
import { fetchBootstrapData }      from './services/api/bootstrap.js'
import { getMcpToolsCommandsAndResources } from './services/mcp/client.js'
import { getTools }                from './tools.js'
import { getAgentDefinitionsWithOverrides } from './tools/AgentTool/loadAgentsDir.js'
import { initBundledSkills }       from './skills/bundled/index.js'
import { showSetupScreens, exitWithError } from './interactiveHelpers.js'
import { settingsChangeDetector }  from './utils/settings/changeDetector.js'
// ... 还有约 80 个 import
```

**6 个初始化步骤都是异步 Promise.all 并行执行的**——这是一个关键的启动性能优化。

### 24.3 init.ts 与 setup.ts 的 Trust 时序

```typescript
// src/entrypoints/init.ts
export async function init(argv) {
  applySafeEnvironmentVariables()     // 只应用安全的 env var（trust 前）
  initializeCertificates()            // 证书与 HTTPS 代理
  initializeHttpAgent()               // HTTP agent 配置
  initTelemetrySkeleton()             // 注册 telemetry sink，但不发事件
  // Note: initializeTelemetryAfterTrust() 在 trust 建立后由 main.tsx 调用
}

export async function initializeTelemetryAfterTrust() {
  applyFullEnvironmentVariables()     // trust 通过后才应用全部 env var
  attachAnalyticsSink()               // 开始处理 telemetry 事件队列
}
```

```typescript
// src/setup.ts
export async function setup(argv, permissionContext) {
  setCwd(resolvedWorkingDir)          // 设置工作目录
  startHooksWatcher()                 // 监听 hooks 配置变化
  initWorktreeSnapshot()              // tmux/worktree 快照
  initSessionMemory()                 // 初始化 session memory 系统
  startTeamMemoryWatcher()            // 启动 team memory 文件监听
}
```

### 24.4 AppState 的真实类型结构

```typescript
// src/state/AppState.ts 类型结构（简化）
type AppState = {
  messages:              Message[]
  toolPermissionContext: ToolPermissionContext
  mainLoopModel:         string
  mcpClients:            McpClient[]
  plugins:               Plugin[]
  agentRegistry:         AgentDefinition[]
  notifications:         NotificationQueue
  remoteBridgeState:     BridgeState | null
  tasks:                 Task[]
  foregroundedTaskId:    string | null
  teamContext:           TeamContext | null
  expandedView:          string | null
  // ...还有约 10 个字段
}
```

`AppState` 是系统的**共享状态总线**，不是简单的 UI 状态——它包含工具权限上下文、MCP 客户端、Agent 注册表、通知队列、远程桥接状态、后台任务等。所有组件通过 `useAppState(selector)` 切片订阅。

### 24.5 源码文件规模全景

`src/` 目录共 1902 个源码文件。以下是核心目录的职责和文件数：

| 目录 | 职责 | 说明 |
|------|------|------|
| `src/components/` | Ink/React 终端 UI 组件体系 | 最大目录，含 30+ 子目录 |
| `src/services/` | 按主题划分的业务服务层 | api/compact/extractMemories/mcp/analytics 等 |
| `src/tools/` | 模型可调用工具与工具 UI | AgentTool/BashTool/FileEditTool 等 30+ 工具 |
| `src/commands/` | Slash/CLI 命令实现 | 100+ 命令目录 |
| `src/hooks/` | 跨组件复用的 React Hook | 80+ 个 hooks |
| `src/utils/` | 通用工具函数 | permissions/sessionStorage/sanitization 等 |
| `src/ink/` | 终端渲染基础设施 | 自建 Ink 渲染引擎 |
| `src/state/` | 全局状态仓库与选择器 | AppState/AppStateStore/selectors |
| `src/skills/` | skills 能力与 bundled 技能入口 | loadSkillsDir/bundledSkills |
| `src/memdir/` | memory 目录检索、召回与持久化 | memdir/paths/findRelevantMemories |

---

## 25. 逐行补充：getSystemPrompt() 完整构造

### 25.1 主体结构（真实源码结构）

```typescript
// src/constants/prompts.ts — getSystemPrompt() 返回值
export async function getSystemPrompt(
  tools: Tools,
  model: string,
  additionalWorkingDirectories?: string[],
  mcpClients?: MCPServerConnection[],
): Promise<string[]> {
  return [
    getSimpleIntroSection(outputStyleConfig),            // 身份声明段
    getSimpleSystemSection(),                              // 基础规则段
    outputStyleConfig === null ||
    outputStyleConfig.keepCodingInstructions === true
      ? getSimpleDoingTasksSection()                      // coding agent 工作规则
      : null,
    getActionsSection(),                                   // 行为约定
    getUsingYourToolsSection(enabledTools),               // 工具使用方式
    getSimpleToneAndStyleSection(),                        // 语气与风格
    getOutputEfficiencySection(),                          // 输出效率规则
    ...(shouldUseGlobalCacheScope()
      ? [SYSTEM_PROMPT_DYNAMIC_BOUNDARY] : []),           // 缓存分隔标记
    ...resolvedDynamicSections,                            // 动态段（从 systemPromptSections 解析）
  ].filter(s => s !== null)
}
```

### 25.2 动态段枚举

```typescript
// 来自 src/constants/systemPromptSections.ts
const dynamicSections = [
  systemPromptSection('session_guidance', ...),     // 会话指引
  systemPromptSection('memory', ...),              // MEMORY.md 内容
  systemPromptSection('ant_model_override', ...),   // 模型覆盖
  systemPromptSection('env_info_simple', ...),      // 环境信息
  systemPromptSection('language', ...),            // 语言偏好
  systemPromptSection('output_style', ...),         // 输出风格
  DANGEROUS_uncachedSystemPromptSection('mcp_instructions', ...), // MCP 指令（显式不可缓存）
  systemPromptSection('scratchpad', ...),           // 草稿区
  systemPromptSection('frc', ...),                 // 功能推荐配置
  systemPromptSection('summarize_tool_results', ...), // 工具结果摘要规则
]
```

### 25.3 section 缓存机制的完整实现

```typescript
// src/constants/systemPromptSections.ts

// 可缓存 section
export function systemPromptSection(
  name: string,
  compute: ComputeFn,
): SystemPromptSection {
  return { name, compute, cacheBreak: false }
}

// 显式声明不可缓存（名字故意的——DANGEROUS）
export function DANGEROUS_uncachedSystemPromptSection(
  name: string,
  compute: ComputeFn,
  _reason: string,
): SystemPromptSection {
  return { name, compute, cacheBreak: true }
}

// 解析时：先查缓存，命中的直接返回
export async function resolveSystemPromptSections(
  sections: SystemPromptSection[],
): Promise<(string | null)[]> {
  const cache = getSystemPromptSectionCache()
  return Promise.all(
    sections.map(async s => {
      if (!s.cacheBreak && cache.has(s.name)) {
        return cache.get(s.name) ?? null  // 缓存命中
      }
      const value = await s.compute()      // 重新计算
      setSystemPromptSectionCacheEntry(s.name, value)  // 更新缓存
      return value
    }),
  )
}
```

### 25.4 缓存清除触发

`clearSystemPromptSections()` 在以下路径被调用：
- `/clear` — 清空对话时
- `/compact` — 压缩时
- `EnterWorktree` / `ExitWorktree` — worktree 切换时
- Resume / Restore session — 恢复会话时

---

## 26. 逐行补充：Session Storage 读写实现

### 26.1 transcript 消息类型判断

```typescript
// src/utils/sessionStorage.ts
export function isTranscriptMessage(entry: Entry): entry is TranscriptMessage {
  return (
    entry.type === 'user' ||
    entry.type === 'assistant' ||
    entry.type === 'attachment' ||
    entry.type === 'system'
  )
}
// progress 不是 transcript message — 不能进入 parentUuid 主链
// 旧版本把 progress 混进 transcript 后恢复时会把真实对话链截断
```

### 26.2 写入去重逻辑（原始源码结构）

```typescript
// src/utils/sessionStorage.ts:1212
const isAgentSidechain = entry.isSidechain && entry.agentId !== undefined
const targetFile = isAgentSidechain
  ? getAgentTranscriptPath(asAgentId(entry.agentId!))
  : sessionFile

const isNewUuid = !messageSet.has(entry.uuid)
if (isAgentSidechain || isNewUuid) {
  void this.enqueueWrite(targetFile, entry)

  if (!isAgentSidechain) {
    messageSet.add(entry.uuid)
    if (isTranscriptMessage(entry)) {
      await this.persistToRemote(sessionId, entry)
    }
  }
}
```

### 26.3 lite reader 的实现

```typescript
// src/utils/sessionStoragePortable.ts
export const LITE_READ_BUF_SIZE = 65536  // 只读头尾 64KB

// 读取逻辑：
// if 文件 > SKIP_PRECOMPACT_THRESHOLD:
//   buf = readTranscriptForLoad() 仅读取 boundary 之后仍有效的部分
//   metadataLines = scanPreBoundaryMetadata() 从 boundary 前补扫 metadata
// else:
//   buf = readFile(filePath)  全量读

extractFirstPromptFromHead():
  → 跳过 tool_result
  → 跳过 isMeta 标记的消息
  → 跳过 compact summary
  → 跳过 <command-name> 包装
  → 跳过系统自动注入片段
```

### 26.4 buildConversationChain 的并行工具结果恢复

```typescript
// src/utils/sessionStorage.ts:2069
export function buildConversationChain(
  messages: Map<string, Message>,
  leafMessage: Message | undefined,
): Message[] {
  // ...沿 parentUuid 回溯...
  transcript.reverse()
  // 关键：恢复并行 tool 调用中丢失的兄弟结果
  return recoverOrphanedParallelToolResults(messages, transcript, seen)
}

recoverOrphanedParallelToolResults():
  处理场景:
    assistant 一次输出多个并行 tool_use
    streaming 过程中这些块被拆成多个 assistant message
    tool_result 分别挂到不同 assistant block 上
    单纯按单 parent 链逆推只会保留其中一支
  → 必须补全兄弟节点 + 补全孤立 tool_result
```

---

## 27. 逐行补充：compactConversation 的四种策略细节

### 27.1 手动 compact 入口

```typescript
// src/commands/compact/compact.ts
// 重新计算 cache-safe prompt：
const defaultSysPrompt = await getSystemPrompt(...)
const systemPrompt = buildEffectiveSystemPrompt({
  mainThreadAgentDefinition: undefined,   // compact 不绑 agent
  toolUseContext: context,
  customSystemPrompt: context.options.customSystemPrompt,
  defaultSystemPrompt: defaultSysPrompt,
  appendSystemPrompt: context.options.appendSystemPrompt,
})
// → compact 本身也依赖 prompt 系统，且要拿共享 cache key 的前缀
```

### 27.2 compact prompt 的正文内容

```typescript
// src/services/compact/prompt.ts
const NO_TOOLS_PREAMBLE = `CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.`
```

### 27.3 Session Memory compact 的配置

```typescript
// src/services/compact/sessionMemoryCompact.ts
getSessionMemoryCompactConfig():
  → 默认保留 10K-40K tokens 原文
  → calculateMessagesToKeepIndex() — 精确裁切保留位置
  → adjustIndexToPreserveAPIInvariants() — 修正裁切位置
```

### 27.4 状态补偿函数

```typescript
// src/services/compact/compact.ts
export function buildPostCompactMessages(result: CompactionResult): Message[] {
  return [
    ...createPostCompactFileAttachments(result),   // 恢复文件上下文
    getDeferredToolsDeltaAttachment(result),         // 重新声明所有工具能力
  ]
}

createPostCompactFileAttachments():
  → 获取并重新添加通过 FileReadTool 查看且还没丢掉缓存的文件（带截断上限）
  → 不添加已在 compact 前显式关闭的文件

getDeferredToolsDeltaAttachment():
  → 重新全量声明当前装载好的外部能力
  → 追加回贴入新的消息队列
```

---

## 28. 逐行补充：Memory 体系的文件级实现

### 28.1 buildMemoryLines() 给模型注入的规则

```typescript
// src/memdir/memdir.ts:199
export function buildMemoryLines(
  displayName: string,
  memoryDir: string,
  extraGuidelines?: string[],
  skipIndex = false,
): string[] {
  const lines: string[] = [
    `# ${displayName}`,
    '',
    // DIR_EXISTS_GUIDANCE = "This directory already exists - write to it directly..."
    // 避免模型浪费一轮对话去 ls/mkdir 确认目录
    `You have a persistent, file-based memory system at ${'`'}${memoryDir}${'`'}. ${DIR_EXISTS_GUIDANCE}`,
    '',
    ...TYPES_SECTION_INDIVIDUAL,     // 记忆类型分类（user/feedback/project/reference）
    ...WHAT_NOT_TO_SAVE_SECTION,     // 禁止保存的内容（可从代码推导的、重复的）
    '',
    ...howToSave,                    // 双步法：先写 topic 文件，再在 MEMORY.md 添加索引行
    '',
    ...(extraGuidelines ?? []),      // agent 专属额外规则（scope 说明等）
  ]
  lines.push(...buildSearchingPastContextSection(memoryDir))
  return lines
}
```

### 28.2 Memory 文件的前端 Matter 格式

```
文件的 frontmatter 包含：
  ---
  name: <short-kebab-case-slug>
  description: <one-line summary — used to decide relevance during recall>
  metadata:
    type: user | feedback | project | reference
  ---

  <the fact: for feedback/project, follow with **Why:** and **How to apply:** lines>
```

### 28.3 isAutoMemoryEnabled() 的完整开关优先级

```typescript
// src/memdir/paths.ts
export function isAutoMemoryEnabled(): boolean {
  // 优先级从高到低:
  // 1. CLAUDE_CODE_DISABLE_AUTO_MEMORY 环境变量
  // 2. CLAUDE_CODE_SIMPLE (--bare 模式) → 关闭
  // 3. 远程模式无持久存储时 → 关闭
  // 4. settings.json 中的 autoMemoryEnabled 字段
  // 5. 默认：开启

  const envVal = process.env.CLAUDE_CODE_DISABLE_AUTO_MEMORY
  if (isEnvTruthy(envVal))          return false   // 显式关闭
  if (isEnvDefinedFalsy(envVal))    return true    // 显式开启
  if (isEnvTruthy(process.env.CLAUDE_CODE_SIMPLE)) return false
  if (isEnvTruthy(process.env.CLAUDE_CODE_REMOTE) &&
      !process.env.CLAUDE_CODE_REMOTE_MEMORY_DIR)   return false
  const settings = getInitialSettings()
  if (settings.autoMemoryEnabled !== undefined) return settings.autoMemoryEnabled
  return true  // 默认开启
}
```

### 28.4 Agent Memory scope 的路径解析

```typescript
getAgentMemoryDir(agentType, scope):
  user:    <memoryBase>/agent-memory/<agentType>/
  project: <cwd>/.claude/agent-memory/<agentType>/
  local:
    默认:    <cwd>/.claude/agent-memory-local/<agentType>/
    若设置 CLAUDE_CODE_REMOTE_MEMORY_DIR:
      <remoteMemoryDir>/projects/<sanitized-git-root>/agent-memory-local/<agentType>/

注意:
  agentType 中 ':' 会被替换为 '-'（插件命名空间 my-plugin:my-agent → my-plugin-my-agent）
  local scope 在 remote 环境下重定位到远端 memory mount 的 project namespace
```

### 28.5 shouldExtractMemory() 的触发阈值

```typescript
// src/services/SessionMemory/sessionMemory.ts:134
export function shouldExtractMemory(messages: Message[]): boolean {
  const currentTokenCount = tokenCountWithEstimation(messages)

  if (!isSessionMemoryInitialized()) {
    // 首次：会话 token 需超过初始化阈值（10K）才开启
    if (!hasMetInitializationThreshold(currentTokenCount)) return false
    markSessionMemoryInitialized()
  }

  const hasMetTokenThreshold = hasMetUpdateThreshold(currentTokenCount)
    // 距上次更新需增长 5000 tokens

  const hasMetToolCallThreshold =
    countToolCallsSince(messages, lastMemoryMessageUuid) >= getToolCallsBetweenUpdates()
    // 距上次更新需完成至少 3 次工具调用

  const hasToolCallsInLastTurn = hasToolCallsInLastAssistantTurn(messages)

  // token 阈值始终必要；在自然断点（无 tool_use）或双阈值都满足时才触发
  const shouldExtract =
    (hasMetTokenThreshold && hasMetToolCallThreshold) ||
    (hasMetTokenThreshold && !hasToolCallsInLastTurn)

  if (shouldExtract) {
    lastMemoryMessageUuid = messages[messages.length - 1]?.uuid
    return true
  }
  return false
}
```

---

## 29. 逐行补充：Multi-Agent 的权限桥接实现

### 29.1 leaderPermissionBridge

```typescript
// src/utils/swarm/leaderPermissionBridge.ts
let registeredSetter: SetToolUseConfirmQueueFn | null = null
let registeredPermissionContextSetter: SetToolPermissionContextFn | null = null

// REPL 把 leader 的权限 UI setter 暴露出来供 teammate 使用
```

### 29.2 in-process teammate 的 canUseTool 权限实现

```typescript
// src/utils/swarm/inProcessRunner.ts
const setToolUseConfirmQueue = getLeaderToolUseConfirmQueue()

if (setToolUseConfirmQueue) {
  // in-process 路径：权限请求入 leader 的 ToolUseConfirmQueue
  return new Promise<PermissionDecision>(resolve => {
    setToolUseConfirmQueue(queue => [
      ...queue,
      {
        ...toolRequest,
        workerBadge: identity.color
          ? { name: identity.agentName, color: identity.color }
          : undefined,  // UI 上带颜色标识区分是哪个 teammate 在请求
      },
    ])
  })
} else {
  // bridge 不可用：退回 mailbox 路径
  // → 发 permission request 给 leader inbox
  // → 等 leader response
  // → 应用回 teammate 上下文
}
```

### 29.3 强制注入的协作工具

```typescript
// src/utils/swarm/inProcessRunner.ts
// teammate agent 的工具池会被强制注入 swarm-essential tools：
tools: agentDefinition?.tools
  ? [
      ...new Set([
        ...agentDefinition.tools,
        SEND_MESSAGE_TOOL_NAME,
        TEAM_CREATE_TOOL_NAME,
        TEAM_DELETE_TOOL_NAME,
        TASK_CREATE_TOOL_NAME,
        TASK_GET_TOOL_NAME,
        TASK_LIST_TOOL_NAME,
        TASK_UPDATE_TOOL_NAME,
      ]),
    ]
  : ['*']  // 自定义 agent 若 tools 为空，给全部工具

// 这说明 swarm 协作能力属于 runtime contract，不完全由 agent frontmatter 决定
```

### 29.4 team 创建时的 task list 初始化

```typescript
// src/tools/TeamCreateTool/TeamCreateTool.ts
const taskListId = sanitizeName(finalTeamName)
await resetTaskList(taskListId)
await ensureTasksDir(taskListId)
setLeaderTeamName(sanitizeName(finalTeamName))
// team 一建立就自动绑定了一套共享 task list
```

---

## 30. 逐行补充：runAgent() — subagent 的真实执行链路

### 30.1 完整调用链路

```typescript
// src/tools/AgentTool/runAgent.ts
// runAgent() 是真正的 agent 执行器，不只是 "调 query()"
// 它做了以下几步：

// 1. 初始化 agent-specific MCP servers
for await (const hookResult of executeSubagentStartHooks(...)) { ... }

// 2. 构造子 agent 的 ToolUseContext（独立 abortController + 受限权限）
const agentToolUseContext = createSubagentContext(toolUseContext, { ... })

// 3. 写入 sidechain transcript
void recordSidechainTranscript(initialMessages, agentId)
void writeAgentMetadata(agentId, { ... })

// 4. 调用核心执行引擎
for await (const message of query({ ... })) { ... }

// agent 的 system prompt 在此时由 getSystemPrompt() 组装
// 如果 agent 定义了 memory → 自动追加 Agent Memory prompt
// 如果 agent 定义了 tools → 工具池在此确定
```

### 30.2 forkSubagent 的特殊处理

```typescript
// src/tools/AgentTool/forkSubagent.ts
// 关键规则：
// - subagent_type 省略时触发 implicit fork
// - child 继承 parent 的完整 conversation context
// - child 继承 parent 的 rendered system prompt（原始字节，不重新生成）
// - 所有 fork child 默认后台运行

// 为什么用原始字节？
// FORK_AGENT 注释：
// "Reconstructing by re-calling getSystemPrompt() can diverge
//  from the cached prefix the API used, and bust the prompt cache"
```

---

## 31. 逐行补充：Swarm Teammate 的完整生命周期

### 31.1 backend 检测与选择

```typescript
// src/utils/swarm/backends/registry.ts
export async function detectAndGetBackend(): Promise<BackendDetectionResult> {
  await ensureBackendsRegistered()
  if (cachedDetectionResult) return cachedDetectionResult  // 缓存结果

  const insideTmux = await isInsideTmux()
  const inITerm2 = isInITerm2()

  // 优先级：tmux > iTerm2 native pane > in-process
  if (insideTmux) {
    return { backend: createTmuxBackend(), isNative: true }
  }
  if (inITerm2) {
    if (!check_it2_installed()) {
      return { backend: null, isNative: false, needsIt2Setup: true }
    }
    return { backend: createITermBackend(), isNative: true }
  }
  // 回退到 in-process（不需要额外依赖）
  return { backend: createInProcessBackend(), isNative: false }
}
```

### 31.2 in-process teammate 的 spawn 完整流程

```typescript
// src/utils/swarm/spawnInProcess.ts
// 文件头注释: Creates and registers an in-process teammate task.
// Unlike process-based teammates (tmux/iTerm2), in-process teammates
// run in the same Node.js process using AsyncLocalStorage for context isolation.

spawnInProcessTeammate():
  → 生成 agentId = formatAgentId(name, teamName)
  → 生成 taskId  = generateTaskId('in_process_teammate')
  → 创建 abortController
  → 创建 teammate identity（含 agentName, color, teamName, agentType, model）
  → 创建 teammateContext（AsyncLocalStorage 上下文隔离）
  → 构造 InProcessTeammateTaskState
  → registerTask(taskState, setAppState)  // 注册到全局调度系统
```

### 31.3 mailbox 的完整 API

```typescript
// src/utils/teammateMailbox.ts
// 文件头注释: Teammate Mailbox - File-based messaging system for agent swarms
// Each teammate has an inbox file at .claude/teams/{team_name}/inboxes/{agent_name}.json

readMailbox(agentName, teamName)     → 读取所有消息
readUnreadMessages(agentName, teamName) → 只读未读消息
writeToMailbox(recipientName, message, teamName):
  → release = await lockfile.lock(inboxPath, {
      lockfilePath: lockFilePath,
      ...LOCK_OPTIONS,
    })  // 文件锁防并发写入
  → 读取现有消息 → 追加新消息 → 写回文件
markMessageAsReadByIndex(agentName, teamName, index) → 标记已读
```

### 31.4 useInboxPoller 的消息处理

```typescript
// src/hooks/useInboxPoller.ts
// 周期性执行:
const unread = await readUnreadMessages(agentName, teamName)

// 按消息类型拆分处理:
for (const msg of unread) {
  switch (msg.type):
    case 'permission_request'  → 入 leader 的权限队列
    case 'permission_response' → 应用到 teammate 上下文（恢复执行）
    case 'shutdown_request'    → 触发 shutdown 流程
    case 'shutdown_approval'   → 确认 shutdown
    case 'plan_approval_request'  → 入 leader 的 Plan 审批
    case 'plan_approval_response' → 应用到 teammate
    default → 作为普通 teammate 消息处理
}
// mailbox 传的不是纯文本，而是 agent 协作协议消息
```

---

## 32. 逐行补充：代码级工程细节

### 32.1 prompt caching 的默认 max_tokens 限制

```typescript
// src/utils/context.ts
// Capped default for slot-reservation optimization.
// BQ p99 output = 4,911 tokens, so 32k/64k defaults over-reserve 8-16× slot capacity.
// With the cap enabled, <1% of requests hit the limit;
// those get one clean retry at 64k.
export const CAPPED_DEFAULT_MAX_TOKENS = 8_000
export const ESCALATED_MAX_TOKENS = 64_000

// 有效上下文窗口计算
export function getEffectiveContextWindowSize(model: string): number {
  const reservedTokensForSummary = Math.min(
    getMaxOutputTokensForModel(model),
    MAX_OUTPUT_TOKENS_FOR_SUMMARY,  // 20_000
  )
  let contextWindow = getContextWindowForModel(model)
  // 支持环境变量硬覆盖
  const autoCompactWindow = process.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW
  // ...
  return contextWindow - reservedTokensForSummary
}
```

### 32.2 MODEL_CONTEXT_WINDOW_DEFAULT

```typescript
// src/utils/context.ts:18
export const MODEL_CONTEXT_WINDOW_DEFAULT = 200_000

// 百万级上下文检查
export function has1mContext(model: string): boolean {
  return /\[1m\]/i.test(model)  // 模型名含 [1m] 标记 → 1M 上下文
}
```

### 32.3 工具池组装的真源码实现

```typescript
// src/tools.ts:345
export function assembleToolPool(
  permissionContext: ToolPermissionContext,
  mcpTools: Tools,
): Tools {
  const builtInTools = getTools(permissionContext)
  const allowedMcpTools = filterToolsByDenyRules(mcpTools, permissionContext)

  const byName = (a: Tool, b: Tool) => a.name.localeCompare(b.name)
  // 合并、排序、名字冲突时内建优先
  return uniqBy(
    [...builtInTools].sort(byName).concat(allowedMcpTools.sort(byName)),
    'name',
  )
}
```

### 32.4 getAllBaseTools() — 内建工具总表

```typescript
// src/tools.ts:193
export function getAllBaseTools(): Tools {
  return [
    AgentTool,
    TaskOutputTool,
    BashTool,
    FileEditTool,
    FileReadTool,
    FileWriteTool,
    GlobTool,
    GrepTool,
    WebFetchTool,
    NotebookEditTool,
    AskUserQuestionTool,
    // Feature Flag 控制:
    ...(isEnvTruthy(process.env.ENABLE_LSP_TOOL) ? [LSPTool] : []),
    ...(isWorktreeModeEnabled() ? [EnterWorktreeTool, ExitWorktreeTool] : []),
    ...(isToolSearchEnabledOptimistic() ? [ToolSearchTool] : []),
    // Ant 内部:
    ...(process.env.USER_TYPE === 'ant' ? [ConfigTool] : []),
  ]
}
```

### 32.5 Session Memory 更新的安全约束实现

```typescript
// src/services/SessionMemory/sessionMemory.ts:460
export function createMemoryFileCanUseTool(memoryPath: string): CanUseToolFn {
  return async (tool: Tool, input: unknown) => {
    if (
      tool.name === FILE_EDIT_TOOL_NAME &&
      typeof input === 'object' && input !== null &&
      'file_path' in input &&
      typeof input.file_path === 'string' &&
      input.file_path === memoryPath    // 精确路径匹配，不允许路径穿越
    ) {
      return { behavior: 'allow' as const, updatedInput: input }
    }
    return {
      behavior: 'deny' as const,
      message: `only ${FILE_EDIT_TOOL_NAME} on ${memoryPath} is allowed`,
      decisionReason: { type: 'other',
        reason: `only ${FILE_EDIT_TOOL_NAME} on ${memoryPath} is allowed` },
    }
  }
}
```

### 32.6 connectedToServer 的 memoize 缓存键

```typescript
// src/services/mcp/client.ts:595
export const connectToServer = memoize(
  async (name, serverRef, serverStats) => { ... },
  getServerCacheKey  // 缓存键 = name + JSON(serverRef)
)
```

### 32.7 危险 Bash 权限检测的实现

```typescript
// src/utils/permissions/permissionSetup.ts
export function isDangerousBashPermission(
  toolName: string,
  ruleContent: string | undefined,
): boolean {
  if (toolName !== BASH_TOOL_NAME) return false
  // 空规则或 * 通配符 = 允许所有命令 = 极度危险
  if (ruleContent === undefined || ruleContent === '' || ruleContent === '*') {
    return true
  }
  for (const pattern of DANGEROUS_BASH_PATTERNS) {
    if (content === `${pattern}:*`) return true   // "python:*" 匹配任意 python 命令
    if (content === `${pattern}*`) return true    // "python*" 匹配 python, python3 等
    if (content === `${pattern} *`) return true   // "python *" 匹配 python + 任意参数
  }
  return false
}
```

### 32.8 密钥扫描规则示例

```typescript
// src/services/teamMemorySync/secretScanner.ts
const SECRET_RULES: SecretRule[] = [
  // AWS 访问密钥
  { id: 'aws-access-token',
    source: '\\b((?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16})\\b' },
  // Anthropic 自家 API Key（编译期拼接，避免密文出现在代码里）
  { id: 'anthropic-api-key',
    source: `\\b(${ANT_KEY_PFX}03-[a-zA-Z0-9_\\-]{93}AA)(?:...)` },
  // GitHub Personal Access Token
  { id: 'github-pat', source: 'ghp_[0-9a-zA-Z]{36}' },
  // OpenAI API Key
  { id: 'openai-api-key', source: '\\b(sk-(?:proj|svcacct|admin)-...' },
  // 私钥文件（PEM 格式）
  { id: 'private-key', source: '-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY...' },
]
```

---

## 33. 补充：沙箱路径语义详解

### 33.1 两套完全不同的路径解析规则

```typescript
// src/utils/sandbox/sandbox-adapter.ts

// 1. Permission rule 里的路径
resolvePathPatternForSandbox(pattern, source):
  // `//path` → absolute from filesystem root
  // `/path`  → relative to settings file directory
  // 这是 Claude Code 特有的权限规则语法

// 2. sandbox.filesystem.* 里的路径
resolveSandboxFilesystemPath(pattern, source):
  // `/path`    → absolute path（标准 Unix 路径）
  // `~/path`   → expanded to home directory
  // `./path` 或 `path` → relative to settings file directory
  // 这是标准路径语义
```

**如果不理解这两套语义的区别，就会把整个 sandbox 行为理解错。**

### 33.2 convertToSandboxRuntimeConfig() 的内置保护

```typescript
convertToSandboxRuntimeConfig(settings):
  初始化:
    allowWrite = ['.', ClaudeTempDir]        // 默认允许写当前目录和临时目录
    denyWrite = []
    denyRead = []
    allowRead = []

  内置保护:
    → 永远拒绝写: settings.json / settings.local.json / managed settings drop-in
    → 永远拒绝写: .claude/skills（Skills have same privilege level）
    → 对 cwd / originalCwd 都做保护

  Git worktree 兼容:
    → 若当前是 worktree: 把 main repo path 加入 allowWrite

  add-dir 兼容:
    → 把 additionalDirectories 和 session add-dir 注入 allowWrite

  遍历所有 setting source:
    → 从 permissions.allow/deny 提取 Edit/Read 规则
    → 从 sandbox.filesystem.allowWrite/denyWrite 提取规则
    → 按 source 解析路径语义（不同来源路径语义不同！）

  Bare Git repo 防御:
    → 扫描 ['HEAD','objects','refs','hooks','config'] 是否已存在
    → 已存在 → denyWrite
    → 不存在 → 加入 scrubPaths（执行后清理）
```

### 33.3 沙箱是否启用的完整判定链

```typescript
// src/utils/sandbox/sandbox-adapter.ts
function isSandboxingEnabled(): boolean {
  if (!isSupportedPlatform())           return false  // 平台不支持
  if (checkDependencies().errors.length > 0) return false  // 依赖缺失
  if (!isPlatformInEnabledList())       return false  // 不在 enabledPlatforms
  return getSandboxEnabledSetting()                   // 用户配置
}

// "settings 写了 sandbox.enabled: true" 和 "实际正在沙箱模式运行" 不是同一概念
// 中间隔着三关：平台、依赖、白名单

function isSandboxRequired(): boolean {
  // sandbox.enabled + sandbox.failIfUnavailable → 沙箱从"增强安全"升级成"必须条件"
  return getSandboxEnabledSetting() &&
    (settings?.sandbox?.failIfUnavailable ?? false)
}
```

### 33.4 allowManagedDomainsOnly 的网络钳制

```typescript
shouldAllowManagedSandboxDomainsOnly():
  → policySettings.sandbox.network.allowManagedDomainsOnly === true

// 一旦 policy 开启:
//   1. 沙箱的网络放行只能来自 managed/policy source
//   2. 运行时 ask callback 被包装 → 直接拒绝临时放行
//   3. UI 上的 "don't ask again for <host>" 选项被拿掉

const wrappedCallback: SandboxAskCallback | undefined = sandboxAskCallback
  ? async (hostPattern: NetworkHostPattern) => {
      if (shouldAllowManagedSandboxDomainsOnly()) {
        return false  // 直接拒绝
      }
      return sandboxAskCallback(hostPattern)
    }
  : undefined
```

---

## 34. 逐行补充：前端组件 AppStateProvider 的完整初始化

### 34.1 AppStateProvider

```typescript
// src/state/AppState.tsx
AppStateProvider:
  → 先检查 HasAppStateContext — 禁止嵌套 provider
  → createStore(initialState ?? getDefaultAppState(), onChangeAppState)  — 懒初始化
  → useEffect:
      → 检查 toolPermissionContext.isBypassPermissionsModeAvailable
      → 若远端设置要求禁用 bypass:
        → 调用内部 _temp(prev) 把 toolPermissionContext 替换成禁用版
  → useSettingsChange(onSettingsChange)  — 把 settings 变更同步回 store
  → 最终挂载:
      → MailboxProvider + VoiceProvider + AppStoreContext.Provider
```

### 34.2 useAppState 的切片订阅实现

```typescript
// src/state/AppState.tsx
useAppState(selector):
  → useAppStore() 取到 store
  → 构造 get():
      → 从 store.getState() 取新状态
      → 执行 selector
  → useSyncExternalStore(store.subscribe, get, get)
  // 不是返回整棵树 — 强制调用者只取切片
  // 配合 Object.is 语义 — 避免无关组件重渲染
```

---

*基于 claude-code-analysis 项目全部 30 个 Markdown 分析文件系统整理*
*整理日期: 2026-06-04*
*总计约 50,000 字*
