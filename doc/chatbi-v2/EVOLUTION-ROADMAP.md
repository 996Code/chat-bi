# ChatBI v2 演进路线图

> 本文是 v2 核心交付完成后（68 任务 / 538 测试通过）的**面向未来**演进规划。
> 与 `ROADMAP.md`（已交付的历史里程碑）区分：那个记录"做完了什么"，这个记录"接下来值得做什么"。

---

## 如何使用本文档

1. **这不是施工图，是决策依据**。每个方向给出现状（带 file:line）、方案、难度、收益，帮你判断"值不值得做、先做哪个"。
2. **一个一个做**。不要把多个方向打包推进。选定一个方向后，单独开 spec/plan，想透做透验证透，再下一个。
3. **方案不是承诺**。标注"待研究"的方向，难点未解，落地前需重新评估。
4. **已否决的方向**记录在附录，避免重复讨论。

---

## 落地原则（与项目硬约束一致）

- **宁缺毋滥**：检索/匹配/识别失败返回空 + 明确提示，禁止"不要返回空"类指令（诱导幻觉）。
- **Fail-Closed**：任何降级都要 WARNING + 告警，安全相关出问题拒绝而非放行。
- **配置不硬编码**：魔法数字/阈值进 `config.py`，走环境变量。
- **先验证再回流**：反哺类功能（图谱/fewshot）必须确认信号可靠，否则会污染系统越用越偏。

---

## LLM 依赖现状（决策基础）

系统对 LLM 的依赖**高度健康**——强依赖只有 1 处，其余全部 fail-closed 降级。

### 强依赖（LLM 挂 = 主流程拿不到结果）— 1 处

| 调用点 | 位置 | 说明 |
|--------|------|------|
| SQL 生成 | `sql_agent.py:172` | NL→SQL 的核心能力，失败直接 `final(failed)`，无规则兜底（合理，本质是 LLM 能力） |

### 弱依赖（有降级路径）— 13 处

| 阶段 | 调用点 | LLM 失败时降级到 |
|------|--------|----------------|
| 意图识别 | `intent.py:159` | CLARIFICATION（反问用户） |
| 闲聊回复 | `replier.py:66` | 默认固定文案 |
| 预思考 | `thinking.py:117` | 空结果（SQL 跳过该 hint） |
| SQL 自愈 | `sql_healer.py:235` | 失败 + 熔断器（连续3次熔断） |
| 图表生成 | `chart_agent.py:622` | 规则推断（柱状/表格兜底） |
| 检索精排 | `retriever.py:144` | 原始向量召回 |
| 标题生成 | `chat_stream.py:700` | 问题截断（后台，不阻塞） |
| 记忆提炼 | `recall.py:191` | 跳过（后台） |
| 历史压缩 | `compressor.py:124` | 简单截断 + 熔断器 |
| 扫描·列中文名 | `data_sources.py:554` | 原始列名 |
| 扫描·表关系 | `knowledge_graph.py:188` | name_pattern 推断 |
| 扫描·示例问题 | `question_generator.py:62` | 规则生成兜底 |
| Skill 预览 | `skills.py:245` | HTTP 500（仅管理后台） |

### 不依赖 LLM（纯代码/embedding）

- **图谱构建** `graph_service.py`（749 行，0 次 LLM）
- **向量检索召回** `retriever.py`（用 embedding，独立服务）
- **fewshot 召回/回流** `fewshot.py`（全程 embedding）
- **记忆召回** `recall.py`（关键词正则，显式注释"省 LLM 调用"）
- **结果自检** `result_checker.py`（纯规则）
- **SQL 执行/校验**（SQLGlot AST + 白名单）

### LLM 完全不可用时的影响

| 功能 | 表现 |
|------|------|
| 新的 NL→SQL 提问 | ❌ 完全不可用（强依赖） |
| 闲聊 | ⚠️ 固定文案 |
| 已保存查询/看板 | ✅ 完全可用 |
| 历史回放 | ✅ 完全可用 |
| 图谱可视化/向量检索 | ✅ 可用（embedding 独立） |

### 性能特征

一次完整 TEXT_TO_SQL 对话**串行**调 LLM（不计重试）：

```
intent(1) → retrieve精排(1) → think(1) → generate_sql(1) → [heal × 0~2] → generate_chart(1)
```

- 正常（无自愈）：5 次，本地小模型 30s ~ 2min
- 最坏（2 轮自愈 + 结果修正）：8~9 次，1 ~ 4min
- 后台段（标题/记忆/fewshot 回流）已放 `finally`，不阻塞用户

---

## 主方向（5 个）

### 方向 1：查询反哺知识图谱（纯隐式信号）🔄 进行中

> **执行状态**：E1 Wave 1 完成（2026-07-18）。详见 `openspec/changes/graph-feedback-loop/`。
> ✅ **已完成 (E1 graph-feedback-loop)**。5 Wave 全部交付:
> - Wave 1: linkage 记忆基建 + persist_linkage_memory + config 配置项
> - Wave 2: persist_warning SSE 事件 (反哺失败前端告知) + C1/W1/W2 修复
> - Wave 3: 图谱 confidence 更新 (乐观锁 + 新表对发现 + sync_linkage_to_graph)
> - Wave 4: 前端冲突弹框 (三选项: 重试/放弃/取消)
> - Wave 5: 双路召回确认 + 端到端验收测试 + 文档
>
> 最终设计: 记忆承载链路经验 (linkage 类型), 整理时轻聚合 → 按阈值 boost confidence / 发现新表对 → 乐观锁写入语义层。版本冲突不静默吞错, 前端弹框让用户决策。

**一句话**：成功的查询应该让图谱越来越准——表共现频繁的关系自动提升 confidence。

#### 现状
- `apply_feedback_signals`（`knowledge_graph.py:373-420`）和 `mine_implicit_relationships`（`knowledge_graph.py:315-370`）算法**已实现且有 13 个单测**，但 `knowledge_graph.py:313` 注释明确写"目前无生产调用方"——**是死代码**。
- confidence 字段只在**扫描期**写入（外键=1.0 / name_pattern=0.6 / LLM 推断=0.7），运行时**从未更新**。
- 主管线 `_persist`（`chat_stream.py:686-845`）已有 3 个反哺点（SavedQuery/fewshot/记忆），图谱反哺应并列插入 `chat_stream.py:842` 后。

#### 方案
1. **只用隐式信号**（成功查询自动挖掘），不做显式纠错按钮。信号源都已就绪：
   - `state.current_tables`（用到的表，含扩展后的）
   - `state.sql` 里的 JOIN（复用 `mine_implicit_relationships` 的正则）
2. 新增 `apply_confidence_updates(content, updates)` helper（仿 `_apply_inferred_relationships` 写法），只改已存在 Relationship 的 confidence 数值并升 version。
3. **节流防版本爆炸**：SemanticModel 是 append-only 版本化，每次成功查询都写版本会爆炸。用独立 `relationship_feedback` 表累计，达阈值或定时任务批量合并回语义层。
4. 扩展 `mine_implicit_relationships` 支持发现**全新表对**（当前 line 346-348 只 boost 已知关系，共现再多也忽略新对）。

#### 难度 / 收益
- 难度：**中**（算法就绪，主要工作是接入 + 节流 + 写回）
- 收益：**高**（JOIN 路径越来越准，检索精排受益）

#### 依赖
- 独立，可先做。建议和方向 5（纠偏经验）区分：本方向是**表关系**层面经验，方向 5 是**问题→SQL 映射**层面经验。

---

### 方向 2：一次对话生成多个图表

**一句话**：用户说"各渠道销售额和订单数对比"，应该拆成两个子查询、并排两张图。

#### 现状
全链路是**单查询绑定**：
- `IntentOutput.normalized_question` 是单字符串（`intent.py:45`），`chart_type_hint` 单数（`intent.py:48`）
- `AgentState` 的 `sql`/`execute_result`/`chart_option` 全是单值字段（`agent.py:59-61`）
- SSE 事件单线：`intent → schema → sql → data → chart`（`chat_stream.py:9-18`），`chart` 事件无 `subquery_id`
- 前端 `Message.chart` 单数（`ChatView.vue:436`），`renderChart` 按消息索引单实例（`ChatView.vue:1201`）

#### 方案（4 phase，单点逐步推进）
1. **意图拆解**：`IntentOutput` 加 `sub_questions: list[str] | None`，prompt 识别并列/对比结构（"A 和 B"、"对比 X 与 Y"）。拆不出就 `None` 走原单查询。
2. **管线 fan-out**：`event_stream` 在 Stage 1 后判断 `sub_questions`，循环跑 schema→sql→data→chart 子管线，emit 带 `subquery_id` 的事件。`AgentState` 字段保持单值，每次循环用新 state。
3. **前端布局**：`Message.charts: Chart[]`，模板用 CSS Grid 并排（`grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))`），最多 2 列。**不直接用 GridStack**（那适合看板编辑，对话流用 CSS Grid 更轻）。`charts.length === 1` 时回退原布局。
4. **持久化兼容**：`ConversationState` 加 `charts: list`，读取时空则回退旧 `chart_option` 单值。

#### 难度 / 收益
- 难度：**中高**（触及意图 schema + 管线结构 + 前端布局 + 持久化四层）
- 收益：**高**（匹配真实提问习惯）

#### 依赖
- 独立。建议在方向 4（指标识别）之后做——指标识别准了，多图表的每个子查询质量才高。

---

### 方向 3：图谱驱动意图/表选择（减少 LLM 依赖）

**一句话**：用图谱结构信号（社区/路径距离）替代部分 LLM 调用，降延迟 + 提精度。

#### 现状
- **意图识别的追问消解/指代展开无法被图谱替代**（`intent.py:100-110` 是语义判断）。
- 但**检索精排**（`retriever.py:106-188` `_llm_refine`）的**降级路径完全无图谱参与**——LLM 失败时直接返回原始向量召回（`retriever.py:151-155`），无序无结构。
- SchemaGraph 信号已就绪：`expand_tables`（最短路径）、`get_communities`（社区归属）、`get_hub_tables`（中心度）——但只在 Stage 3 表扩展用了，没用于检索精排。

#### 方案（4 phase 渐进，风险递增）
1. **图谱增强降级路径**（低风险高收益，优先）：`_llm_refine` 失败时，调 `SchemaGraph.expand_tables(raw_candidates)` 做距离排序，把同社区/距离近的候选排前。改动 ~30 行。
2. **图谱信号注入 LLM 精排 prompt**：在 `_llm_refine` prompt 里加"候选 X 与高分表 Y 同社区"、"Z 是枢纽表(可能 Fan-Trap)"，辅助 LLM 判断。
3. **图谱规则预筛**：先用社区+邻居缩小候选集，再让 LLM 在更小集合精排（省 token）。需先评估关系覆盖率。
4. **图谱前置过滤**（探索性，高风险）：用图谱判断问题名词是否对应节点，全无对应则跳过完整意图分类。风险：追问消解无能为力。

#### 难度 / 收益
- 难度：Phase1 **低** / Phase2-3 **中** / Phase4 **高**
- 收益：Phase1 明确（修复降级路径精度差）；Phase2-3 渐进提升 + 省 token

#### 依赖
- Phase 1 独立可做。建议在做完 P0-5（图谱缓存）后推进——图谱频繁用，缓存先行。

---

### 方向 4：指标识别优化

**一句话**：让系统认识 GMV、复购率、客单价这些业务指标，而不是每次让 LLM 瞎猜。

#### 现状
- **Metric schema 定义完备**：`Metric(type="single"|"composite")`（`semantic_layer.py:78-117`），composite 支持子指标组合。**但扫描不产出**——`semantic_scanner.py:154` 写死 `metrics=[]`，生产语义层 `model.metrics` 永远空。
- **SQL prompt 看不到指标**：`build_schema_context`（`schema_utils.py:201-241`）只输出列+关系，跳过 metrics；`sql_agent.py:46-56` 的系统 prompt 6 条规则里**没有指标概念**。
- **复合指标全靠 LLM 自由推理 + memory 兜底**：GMV/复购率/客单价没有结构化定义，靠 `seed_memories.py` 的关键词召回（`recall.py:27-117` 纯关键词，"复购率"召不回"GMV"）。
- **⚠️ 隐患**：`skills/_template/sql-rules/SKILL.md:14-15` 写 `GMV = SUM(total_amount)`，但 `seed_memories.py:48-60` 写 `GMV = SUM(actual_amount)` 且明确反对 total_amount——**两者矛盾，同时注入 prompt 会误导 LLM**。

#### 方案
1. **Metric 自动推断**：新增 `infer_metrics_from_models(content)`（仿 `infer_knowledge_graph`），扫描 Stage 3 调用——找 measure 列 + 常见命名（`*_amount/*_price/*_count`）→ single Metric；跨表金额/数量 → composite Metric。
2. **schema_context 输出指标**：`build_schema_context` 每张表后追加 `[指标: GMV=SUM(actual_amount) WHERE ...]`。
3. **thinking/sql_agent 加指标槽**：ThinkingResult 加 `metrics_used`；`_build_dynamic_context` 在 skills 后加 `【指标定义】` 块，只注入命中的相关 metric。
4. **memory 召回升级 embedding**：复用 fewshot 的 embedder+vector_store，解决"复购率召不回 GMV"。
5. **统一 seed 数据源**：删除 `skills/_template` 里冲突的 GMV 公式，或让 skills 从 semantic_layer.metrics 动态生成。

#### 难度 / 收益
- 难度：**中**（schema 就绪，主要补推断 + 注入 + 召回升级）
- 收益：**高**（直接提升 NL2SQL 准确率，复合指标不再瞎猜）

#### 依赖
- 独立可做。是准确率攻坚的核心方向。

---

### 方向 5：对话纠偏经验择优沉淀 ⚠️ 待研究

**一句话**：多轮对话里的纠偏过程（自愈、追问修正）是高质量监督信号，应该择优沉淀成经验。

> **⚠️ 本方向有硬难点未解，方案未定，落地前必须重新评估。**

#### 现状（问题）
- fewshot 回流只看"成功"（`chat_stream.py:806` `if state.success and state.sql`），**不区分怎么成功的**：
  - LLM 一次就对 → 回流
  - LLM 错了 → 自愈修复 → 回流（但 `heal_before_sql` 对照经验没沉淀）
  - 用户追问修正 → 回流（但纠偏过程丢失）
- 且"成功"**不校验业务正确**——能跑通但语义错的 SQL 也会回流，越用越偏。

#### 候选方案：verified / normal 分级
- fewshot 加 `quality: "verified"|"normal"` 字段
- **verified**（优先召回）：自愈后成功、多轮追问修正成功
- **normal**：首次即成功
- 召回时 verified 排序靠前

#### ⚠️ 硬难点（未解，勿急着做）
1. **追问修正极难判定**：用户问 A → 换说法重问 B → 成功。这是"修正"还是"新查询"？没有可靠信号区分"不满意重问" vs "自然连续追问"。
2. **verified 的 SQL 未必对**：自愈修复后能跑通，但不一定业务正确。把自愈 SQL 标 verified 优先召回，可能传播"能跑通但语义错"的 SQL，比现状（normal）风险更高。
3. **fewshot 主键冲突**：`index_fewshot_example` 的 example_id = `md5(ds:question)`（`fewshot.py:34`）。但追问修正会改 question（"销售额"→"成交额"），生成**不同** fewshot 而非更新同一条，"纠错闭环降权旧 fewshot"做不到。
4. **召回权重改造**：verified 排序靠前要改 `find_fewshot_examples`（`fewshot.py:38`）的召回逻辑，或向量库支持 quality 权重。

#### 可先做的子集（信号明确）
- **只做自愈对照沉淀**：`self_heal_rounds > 0` + `heal_before_sql` 是客观字段，把"错误 SQL → 正确 SQL"的对照经验结构化存储（独立于 fewshot），供调试/分析/未来 Fewshot 质量评估用。这部分信号明确，无判定难题。
- **模糊部分（追问修正判定）标为待研究**，不强行做。

#### 难度 / 收益
- 难度：**中**（自愈对照子集）/ **高**（完整方案，含追问修正判定）
- 收益：**高**（直接提升 NL2SQL 准确率 + 错误经验自淘汰）——前提是难点解决

#### 依赖
- 和方向 1（反哺图谱）互补：方向 1 是表关系经验，本方向是问题→SQL 映射经验。
- 建议在 P0-7（检索/fewshot 质量评估闭环）之后做——没有评估机制，无法验证 verified 经验是否真的更准。

---

## 补充方向（8 个）

> 精简表格。每个方向展开单点做时，再补完整 spec。

### P0-5：SchemaGraph + 语义层跨请求缓存（低难/高收益）

- **现状**：`get_schema_graph`（`schema_utils.py:61-80`）每请求重建 NetworkX DiGraph；`get_communities`（`graph_service.py:391-429`）每次重跑 label_propagation；语义层 JSON 每次重新解析（`chat.py:147-156`、`chat_stream.py:359`）。100 张表量级单次构建+社区发现占 100-300ms。
- **方案**：以 `(tenant_id, data_source_id, sm.version)` 为 key LRU/TTL 缓存；语义层变更 bump version 自动失效；`get_communities` 结果挂 SchemaGraph 实例惰性算一次。
- **难度**：低｜**收益**：高（每次 Q&A 省百毫秒，所有租户共享）

### P0-6：跨阶段并行 + 短路（中难/高收益）

- **现状**：intent→retrieve→think→sql→chart 5 次串行 LLM（`chat_stream.py`），think 输出仅作 hint 不阻塞 schema_context（`thinking.py:39`）；fewshot/memory/skills 在 `build_agent_deps` 串行 await（`chat.py:181-211`）。
- **方案**：① `asyncio.gather` 并发 fewshot 检索 + memory recall + skills 加载（三者无依赖）；② 高置信意图短路 think（加配置阈值跳过预思考）；③ chart 选型与全量 fetch 并行（先 fetch 前 N 行喂 chart agent）。
- **难度**：中｜**收益**：高（端到端延迟降 30-50%）

### P0-7：检索/fewshot 质量评估闭环（中难/高收益）

- **现状**：召回率/精确率零监控（`retriever.py:106-188` 只记日志）；fewshot 无条件回流且"成功"不校验业务正确（`chat_stream.py:806`）；无 TTL/失效，schema 变更后旧 SQL 不 stale。
- **方案**：① 离线评估集（每数据源 50-100 条人工标注 Q+期望表集合），定期跑 recall@k/precision@k 写库；② fewshot 带 review 状态（pending/approved/rejected），默认 pending 不召回；③ fewshot 附加 schema_version，语义层版本变更标记 stale。
- **难度**：中｜**收益**：高（检索是准确率根因，没评估就是盲调）

### P0-8：敏感数据脱敏 / PII 标记（中难/高收益·合规阻塞）

- **现状**：`Column` 无 `is_pii` 字段（`semantic_layer.py:50-56`）；`sql_executor.py:100-113` 原样 fetchall；`SELECT phone FROM users` 可全文导出，CSV 导出（`saved_queries.py:180-192`）只防公式注入不防 PII。
- **方案**：① Column 加 `is_pii`+`mask_strategy`（none/partial/hash/suppress）；扫描阶段正则+LLM 自动识别（手机/邮箱/身份证）；② executor 返回前按标记脱敏；③ RBAC 分级（admin 明文/user 脱敏）；④ 审计记录敏感列查询。
- **难度**：中｜**收益**：高（合规底线，缺失阻挡企业级落地）

### P1-9：核心 SLO 监控 + 告警（中难/高收益）

- **现状**：只有按数据源 24h 聚合（`observability.py:288-343`），admin 主动拉取；无定时聚合/趋势/成本维度；LLM 成本只累计 token 不折算金额；告警只是 logger.error 文本，无外部通道。
- **方案**：① AuditLog 加 prompt_tokens/completion_tokens/model 列（目前只在 StateStore JSONL）；② `/slo` 端点（成功率/P50P95延迟/自愈率/降级率/单查询平均 token）；③ `config.cost_per_1k_tokens` 折算金额；④ webhook 告警（飞书/钉钉）：错误率/熔断/慢查询超阈值。
- **难度**：中｜**收益**：高（生产化硬门槛）

### P1-12：SchemaGraph 算法测试覆盖（低难/中收益）

- **现状**：`graph_service.py`（Dijkstra/社区/expand_tables/get_join_context）**零测试覆盖**（`grep SchemaGraph` 在 tests/ 零命中）；超级枢纽（degree=90）、多社区种子、无连通路径等 edge case 都没测。
- **方案**：补 `test_graph_service.py`：① Dijkstra 多跳 + max_hops 截断；② expand_tables 防扩散（种子保留+远亲按距离）；③ 社区补全兜底；④ get_join_context 去重；⑤ 双向边 forward/reverse 语义。
- **难度**：低｜**收益**：中（一次性投入长期防回归）

### P2-13：E2E 测试（中难/中收益）

- **现状**：`test_integration.py:147-159` 有注释掉的 `TestFullQueryLifecycle` 骨架从未补；现有全是单元级（test_agent mock deps，test_chat_stream 只测 heal 分支）。
- **方案**：pytest+httpx 跑完整 7 步状态机（mock LLM 固定响应），断言 StateStore 落库/AuditLog/SavedQuery/fewshot 回流；前端 Playwright 跑"提问→看 SSE→追问"。
- **难度**：中｜**收益**：中（上线/重构安全网）

### P2-14：多轮压缩质量评估 + 关键信息防丢（中难/中收益）

- **现状**：摘要 prompt（`compressor.py:77-82`）只保留"查了什么表/指标/筛选/结果数字"，**丢失**聚合粒度（按天 vs 按月）、时间窗口（本月 vs 上月）、排序、limit；token 估算粗（中文1.5/英文4，`compressor.py:25-37`）；压缩质量零监控。
- **方案**：① 摘要输出结构化 JSON（强制保留时间窗口/聚合维度/筛选/排序）；② 压缩质量评估集（长对话+追问，对比压缩前后回答一致率）；③ compress node 单独统计触发率/失败率上 observability。
- **难度**：中｜**收益**：中（多轮是 BI 高频场景，压缩质量决定追问体验）

---

## 落地路线（按价值/难度/依赖分组，但严格单点做）

> ⚠️ **Wave 只是分组参考，不是打包施工令。** 选定一个方向 → 开 spec/plan → 做透验证透 → 再下一个。绝不一次推进多个。

### Wave 1 — 高价值 / 低风险 / 信号就绪（优先从这里挑第一个做）

| 方向 | 为什么先做 |
|------|-----------|
| **P0-5 图谱/语义层缓存** | 低难度高收益，立竿见影省延迟；是方向 3（图谱驱动意图）的前置 |
| **方向 1 查询反哺图谱** | 算法已就绪（13 单测），只缺接入；隐式信号无需新交互 |
| **P1-12 SchemaGraph 算法测试** | 低难度，补回归保护；和方向 1/3 都涉及图谱，先建安全网 |

### Wave 2 — 准确率攻坚（NL2SQL 的核心价值）

| 方向 | 为什么 |
|------|--------|
| **方向 4 指标识别优化** | schema 就绪，直接提升复合指标准确率；先消除 seed 冲突隐患 |
| **P0-7 检索/fewshot 质量评估** | 没评估就是盲调，是方向 5 的前置 |
| **方向 3-Phase1 图谱增强降级** | 低风险修复降级路径精度差 |

### Wave 3 — 性能 + 能力扩展

| 方向 | 为什么 |
|------|--------|
| **P0-6 跨阶段并行+短路** | 延迟降 30-50%，用户体验质变 |
| **方向 2 多图表** | 匹配真实提问习惯；建议在方向 4 之后（指标准了子查询质量才高） |

### Wave 4 — 生产化 + 合规（上线/商业化前必做）

| 方向 | 为什么 |
|------|--------|
| **P0-8 PII 脱敏** | 合规底线，缺失阻挡企业落地 |
| **P1-9 SLO 监控+告警** | 生产化硬门槛 |
| **P2-13 E2E 测试** | 重构/上线安全网 |
| **P2-14 压缩质量评估** | 长对话场景成熟后 |
| **方向 5 纠偏经验沉淀** | 难点多，需先解决"追问修正判定"再做完整方案；自愈对照子集可提前 |

### 依赖关系速查

```
P0-5缓存 ──→ 方向3(图谱驱动意图)
方向4(指标) ──→ 方向2(多图表)
P0-7(评估) ──→ 方向5(纠偏经验)
方向1(反哺图谱) ←─ 独立
P1-12(图谱测试) ←─ 建议在做方向1/3前补
```

---

## 附录 A：已否决方向及理由（避免重复讨论）

| 方向 | 否决理由 |
|------|---------|
| ~~查询结果分享/收藏~~ | ChatBI 是内部数据查询工具，不是 C 端内容产品。"分享单张图"是产品经理式功能堆砌，BI 场景真正有价值的是看板（已有）。加 star/share_token 偏离核心价值。 |
| ~~用户纠错按钮（点赞/表选错了）~~ | 用户发现表选错，第一反应是换说法重问，而非点纠错按钮。且已有更自然的纠错通道——**主动确认机制（ask_user）**：表不确定时系统列候选让用户选。再加显式按钮是冗余交互负担。方向 1/5 改为纯隐式信号反哺。 |

> 如未来场景变化（如要做 C 端、或 ask_user 不够用），可重新评估这两个方向。

---

## 附录 B：风险登记册

| 风险 | 关联方向 | 缓解 |
|------|---------|------|
| 反哺信号污染（错误经验回流） | 方向1/5 | 先做 P0-7 评估闭环；节流 + 阈值；宁缺毋滥 |
| SemanticModel 版本爆炸（频繁写 confidence） | 方向1 | 独立 feedback 表累计 + 定时批量合并 |
| 图谱关系覆盖率不足（推断关系缺失） | 方向3 | Phase3 前先评估 `knowledge_graph.py` 关系覆盖率 |
| verified SQL 未必业务正确 | 方向5 | 难点未解前不做完整方案，只做自愈对照子集 |
| 多图表管线并发 LLM 触发限流 | 方向2 | 复用 `_limiter`，单请求内子查询串行或受限并发 |
| PII 识别漏判（新型敏感字段） | P0-8 | 正则+LLM 双保险 + 审计记录 + 人工可标 |

---

## 附录 C：关键 file:line 索引

### 反哺/经验类
- `knowledge_graph.py:315-420` — mine_implicit_relationships / apply_feedback_signals（死代码，待接入）
- `chat_stream.py:774-845` — _persist 现有反哺点（SavedQuery/fewshot/记忆），方向1接入点在 842 后
- `fewshot.py:34` — example_id = md5(ds:question)，方向5主键冲突点
- `recall.py:27-117` — memory 关键词召回，方向4升级 embedding 点

### 指标/SQL 生成
- `semantic_layer.py:78-117` — Metric schema（完备）
- `semantic_scanner.py:154` — metrics=[] 写死，方向4推断点
- `schema_utils.py:201-241` — build_schema_context 不输出 metrics
- `sql_agent.py:46-98` — SQL prompt 无指标槽
- `skills/_template/sql-rules/SKILL.md:14-15` vs `seed_memories.py:48-60` — GMV 公式冲突

### 图谱/检索
- `graph_service.py:99-498` — SchemaGraph 全部方法（零测试）
- `retriever.py:106-188` — _llm_refine 精排 + 降级路径（方向3改造点）
- `schema_utils.py:61-80` — get_schema_graph 每请求重建（P0-5缓存点）

### 管线/性能
- `chat_stream.py:201-589` — 7 步管线单线（方向2 fan-out / P0-6 并行改造点）
- `chat.py:181-217` — _generate_sql_with_fewshot 闭包（skills+memory 注入处）
- `compressor.py:77-82` — 摘要 prompt 丢关键字段（P2-14改造点）

### 安全/可观测
- `sql_executor.py:100-113` — 原样 fetchall（P0-8 脱敏点）
- `semantic_layer.py:50-56` — Column 无 is_pii（P0-8 加字段点）
- `observability.py:288-343` — 现有按数据源聚合（P1-9 扩展点）

---

*最后更新: 2026-07-18*
*本文档随方向落地持续更新——做完一个方向，更新其状态并归档到 ROADMAP.md。*



