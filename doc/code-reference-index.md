# ChatBI v2 代码引用索引

> 完整代码索引，覆盖 backend/app/ + frontend/src/ 全部关键定义。
> 格式：`file:line` — 定义名: 描述

---

## backend/app/ai/ — Agent 执行引擎（14 个文件）

### `ai/agent.py` — 状态机编排
| 行号 | 定义 | 描述 |
|------|------|------|
| 31 | `AgentStage` | 状态机阶段枚举 (INTENT/SCHEMA_SEARCH/GENERATE_SQL/EXECUTE_SQL/SELF_CHECK/VISUALIZE/FINAL) |
| 43 | `AgentState` | Agent 执行状态 dataclass（含所有中间产物、计数器、降级标记） |
| 103 | `AgentDeps` | Agent 依赖注入 dataclass（各节点服务，测试可 mock） |
| 123 | `run_agent()` | **主循环**：状态机编排，对标 Claude Code while(true) |

### `ai/intent.py` — 意图识别
| 行号 | 定义 | 描述 |
|------|------|------|
| 34 | `CONFIDENCE_DEGRADE_THRESHOLD` | 置信度降级阈值 (0.6) |
| 36 | `MAX_RETRIES` | 重试最大次数 (2) |
| 39 | `IntentOutput` | 意图识别输出 Pydantic 模型 |
| 69 | `strip_visualization()` | 剥离可视化措辞 ("用柱状图展示" → "柱状图" hint) |
| 97 | `_INTENT_PROMPT` | LLM 意图分类 prompt |
| 123 | `classify_intent()` | **意图分类**：5 意图 + Pydantic 强约束 + 降级 |
| 211 | `safe_normalized_question()` | 安全提取 normalized_question |

### `ai/thinking.py` — 预思考
| 行号 | 定义 | 描述 |
|------|------|------|
| 29 | `ThinkingResult` | 预思考结果 dataclass |
| 39 | `format_thinking_hint()` | 格式化预思考为 SQL 生成提示 |
| 59 | `_THINKING_PROMPT` | LLM 预思考 prompt |
| 78 | `think()` | **预思考**：选表理由 + 聚合方式 + 陷阱警告 |

### `ai/sql_agent.py` — SQL 生成
| 行号 | 定义 | 描述 |
|------|------|------|
| 29 | `GenerateResult` | SQL 生成结果 dataclass |
| 46 | `_SYSTEM_PROMPT` | SQL 生成系统 prompt（分层设计） |
| 59 | `_build_allowed_columns_section()` | 构建白名单列约束段 |
| 65 | `_build_datatype_constraint_section()` | 构建数据类型约束段 |
| 76 | `_build_dynamic_context()` | 构建动态上下文（few-shot/记忆/Skills） |
| 101 | `_extract_sql()` | 从 LLM 响应提取 SQL |
| 113 | `generate_sql()` | **SQL 生成**：含 Prompt 分层 + Few-shot 注入 |

### `ai/sql_healer.py` — SQL 自愈
| 行号 | 定义 | 描述 |
|------|------|------|
| 34 | `_ERROR_CODE_MAP` | 错误码前缀 → 分类映射 |
| 47 | `ErrorCategory` | 错误分类枚举 |
| 63 | `extract_error_code()` | 提取错误码 |
| 80 | `SelfHealCircuitBreaker` | 自愈熔断器 |
| 135 | `get_circuit_breaker()` | 熔断器单例 |
| 152 | `HealResult` | 自愈结果 dataclass |
| 162 | `_SECURITY_RULES` | 安全规则（自愈 prompt 保留） |
| 171 | `_CATEGORY_HINTS` | 按错误分类的纠正 hint |
| 180 | `heal_sql()` | **SQL 自愈**：错误分类 → 纠正 prompt → 重新生成 |

### `ai/chart_agent.py` — 图表生成
| 行号 | 定义 | 描述 |
|------|------|------|
| 30 | `ChartResult` | 图表生成结果 dataclass |
| 41 | `heal_json()` | JSON 自愈（统计括号差异 → 补全） |
| 106 | `_TIME_KEYWORDS` | 时间相关列关键词冻结集 |
| 115 | `_PIE_MAX_SLICES` | 饼图最大切片数 (10) |
| 117 | `_NUMERIC_COL_THRESHOLD` | 数值列检测阈值 (0.8) |
| 119 | `_DATAZOOM_THRESHOLD` | 数据缩放阈值 (20) |
| 139 | `analyze_data_shape()` | 分析数据形状 |
| 266 | `_build_kpi_option()` | 构建 KPI 指标卡 ECharts option |
| 302 | `infer_chart_by_rule()` | **规则推断**图表类型（LLM 降级兜底） |
| 415 | `_CHART_PROMPT` | LLM 图表选择 prompt |
| 467 | `inject_data()` | 注入数据到 ECharts option |
| 584 | `generate_chart()` | **图表生成**：LLM → JSON 自愈 → 规则兜底 |

### `ai/result_checker.py` — 结果自检
| 行号 | 定义 | 描述 |
|------|------|------|
| 26 | `ResultIssue` | 结果问题枚举 |
| 35 | `CheckResult` | 自检结果 dataclass |
| 47 | `_count_all_null_columns()` | 检查全 NULL 列 |
| 65 | `_is_count_zero()` | 检查 COUNT=0 |
| 76 | `check_result()` | **结果自检**：纯规则（零行/全NULL/笛卡尔积） |

### `ai/replier.py` — 自然语言回复
| 行号 | 定义 | 描述 |
|------|------|------|
| 24 | `_FALLBACK_REPLY` | 兜底回复文案 |
| 26 | `_REPLY_PROMPT` | LLM 回复生成 prompt |
| 41 | `generate_reply()` | **回复生成**：GENERAL/EXPLANATION 意图 |

### `ai/ask_user.py` — 用户澄清
| 行号 | 定义 | 描述 |
|------|------|------|
| 25 | `AskUserReason` | 澄清原因枚举 |
| 39 | `should_ask_for_schema()` | Schema 不确定 → ask_user |
| 71 | `should_ask_for_result()` | 结果不明确 → ask_user |

### `ai/recall.py` — 记忆召回
| 行号 | 定义 | 描述 |
|------|------|------|
| 27 | `recall_memories()` | **记忆召回**：关键词相关（不调 LLM） |
| 148 | `format_memories_for_prompt()` | 格式化记忆为 Prompt 文本 |
| 174 | `extract_memory_from_turn()` | 从对话轮提取记忆 |
| 294 | `consolidate_memories()` | 记忆整理/聚合 |
| 438 | `save_query_memory()` | 保存查询记忆 |
| 508 | `_extract_join_pairs()` | 提取 JOIN 对 |
| 523 | `_extract_join_on_conditions()` | 提取 ON 条件 |
| 583 | `_build_linkage_content()` | 构建链路记忆内容 |
| 683 | `persist_linkage_memory()` | **持久化链路记忆** |
| 859 | `persist_metric_feedback()` | **持久化指标反馈** |

### `ai/compressor.py` — 上下文压缩
| 行号 | 定义 | 描述 |
|------|------|------|
| 25 | `estimate_tokens()` | Token 估算 |
| 71 | `should_compress()` | 是否触发压缩（阈值 70%） |
| 93 | `CompactResult` | 压缩结果 dataclass |
| 111 | `_COMPACT_PROMPT` | 压缩 prompt |
| 119 | `compact_history()` | **上下文压缩**：LLM 摘要 + 状态补偿 |
| 190 | `CompressionCircuitBreaker` | 压缩熔断器 |
| 230 | `get_compression_circuit_breaker()` | 熔断器单例 |

### `ai/state_store.py` — 对话状态管理
| 行号 | 定义 | 描述 |
|------|------|------|
| 26 | `DecisionPoint` | 关键决策点 dataclass |
| 48 | `ConversationState` | 对话状态 dataclass（结构化状态） |
| 156 | `StateStore` | **状态存储**：JSONL 持久化 save/load |
| 248 | `_turns_to_messages()` | 轮次 → 消息列表转换 |
| 273 | `_format_state_compensation()` | 状态补偿格式化 |
| 294 | `format_history_text()` | **历史文本格式化**：含压缩 + 状态补偿 |
| 371 | `_messages_to_text()` | 消息列表 → 纯文本 |

### `ai/schema_utils.py` — Schema 工具
| 行号 | 定义 | 描述 |
|------|------|------|
| 32 | `extract_allowed_columns()` | 提取白名单列（语义层 = 安全边界） |
| 61 | `get_schema_graph()` | 构建 SchemaGraph（NetworkX） |
| 83 | `expand_with_relationships()` | **图谱扩展**：沿关系图补全关联表 |
| 150 | `_expand_with_bfs()` | BFS 扩展实现 |
| 201 | `build_schema_context()` | 构建 Schema 上下文文本 |
| 258 | `build_metrics_hint()` | 构建业务指标提示段 |
| 298 | `build_join_path_section()` | **JOIN 路径预计算**：Dijkstra 最短路径 |

### `ai/chat_utils.py` — 共享工具
| 行号 | 定义 | 描述 |
|------|------|------|
| 17 | `normalize_value()` | 值归一化 |
| 42 | `serialize_thinking()` | 预思考序列化 |
| 61 | `build_schema_context_fallback()` | 无语义层时的兜底 Schema 构建 |
| 73 | `_extract_model_name()` | 提取模型名 |
| 80 | `inherit_prev_tables()` | **追问表继承**：检索结果 ∪ 上轮表 |

### `ai/question_generator.py` — 示例问题生成
| 行号 | 定义 | 描述 |
|------|------|------|
| 24 | `_QUESTION_PROMPT` | LLM 示例问题生成 prompt |
| 42 | `generate_sample_questions()` | 生成示例问题 |
| 81 | `_build_schema_summary()` | Schema 摘要 |
| 107 | `_parse_questions()` | 解析 LLM 输出 |
| 120 | `_fallback_questions()` | 规则兜底生成（LLM 失败时） |

---

## backend/app/services/ — 业务服务层（16 个文件）

### `services/embedder.py` — 向量嵌入
| 行号 | 定义 | 描述 |
|------|------|------|
| 40 | `Embedder` | 嵌入器抽象 Protocol |
| 62 | `LocalEmbedder` | **本地 BGE 实现**：惰性加载 + LRU 缓存 |
| 158 | `get_embedder()` | 单例工厂 |
| 183 | `reset_embedder()` | 重置单例（测试用） |
| 189 | `embed_texts()` | 便捷嵌入函数 |

### `services/retriever.py` — 两阶段检索
| 行号 | 定义 | 描述 |
|------|------|------|
| 31 | `RetrievalResult` | 检索结果 dataclass |
| 39 | `retrieve()` | **两阶段检索**：向量召回 → LLM 精筛 |
| 92 | `_candidates_to_models()` | 候选 → 模型 dict 转换 |
| 106 | `_llm_refine()` | **LLM 精筛**：宁缺毋滥 |

### `services/vector_store.py` — 向量存储抽象
| 行号 | 定义 | 描述 |
|------|------|------|
| 30 | `VectorRecord` | 向量记录 dataclass |
| 43 | `SearchResult` | 搜索结果 dataclass |
| 52 | `VectorStore` | 向量存储抽象 Protocol |
| 99 | `_cosine_similarity()` | 余弦相似度计算 |
| 109 | `_matches_filter()` | 标量过滤匹配 |
| 114 | `MockVectorStore` | **Mock 实现**：纯内存，开发/测试用 |
| 185 | `get_vector_store()` | 工厂：Milvus/Mock 切换 |
| 283 | `reset_vector_store()` | 重置单例（测试用） |

### `services/milvus_vector_store.py` — Milvus 实现
| 行号 | 定义 | 描述 |
|------|------|------|
| 34 | `_build_filter_expr()` | 构建 Milvus filter 表达式 |
| 60 | `_build_id_filter()` | 构建 ID filter |
| 66 | `MilvusVectorStore` | **Milvus 实现**：HNSW 索引 + IP 度量 |

### `services/sql_executor.py` — SQL 执行
| 行号 | 定义 | 描述 |
|------|------|------|
| 29 | `ExecuteResult` | 执行结果 dataclass |
| 47 | `_inject_limit()` | **自动 LIMIT**：无 LIMIT 的 SELECT 自动加 |
| 71 | `_execute_sync()` | 同步执行（to_thread 内跑） |
| 130 | `execute_sql()` | **SQL 执行**：连接池复用 + 超时双保险 + READ ONLY |

### `services/datasource_engine.py` — 数据源连接池
| 行号 | 定义 | 描述 |
|------|------|------|
| 30 | `build_engine_url()` | 构建 SQLAlchemy engine URL |
| 54 | `datasource_to_url()` | DataSource → URL（含 Fernet 解密） |
| 73 | `DataSourceEnginePool` | **动态连接池**：懒加载 + 引用计数 |
| 227 | `get_engine_pool()` | 单例工厂 |

### `services/datasource_health.py` — 数据源健康检查
| 行号 | 定义 | 描述 |
|------|------|------|
| 43 | `_PING_TIMEOUT` | Ping 超时 (5s) |
| 47 | `PingResult` | Ping 结果 dataclass |
| 59 | `_ping_sync()` | 同步 ping |
| 96 | `ping_datasource()` | 异步 ping 单个数据源 |
| 121 | `check_all_datasources_health()` | 批量健康检查（并发） |
| 222 | `_audit_health()` | 健康检查审计日志 |

### `services/graph_service.py` — 知识图谱服务
| 行号 | 定义 | 描述 |
|------|------|------|
| 34 | `JoinPath` | JOIN 路径 dataclass |
| 45 | `_EDGE_ON` / `_EDGE_*` | 边属性键常量 |
| 54 | `_NODE_*` | 节点属性键常量 |
| 60 | `SchemaGraph` | **图谱服务**：NetworkX 构建 + Dijkstra + 社区发现 + 中心度分析 |

### `services/semantic_scanner.py` — 数据源扫描
| 行号 | 定义 | 描述 |
|------|------|------|
| 30 | `InspectorLike` | DB Inspector 抽象 Protocol |
| 41 | `_column_comment()` | 提取列注释 |
| 58 | `_infer_cardinality()` | 推断关系基数 |
| 64 | `scan_data_source()` | **扫描数据源** → SemanticModelContent |
| 93 | `_scan_table()` | 扫描单表 |
| 163 | `_infer_semantic_type()` | 推断语义类型 (measure/dimension) |
| 180 | `_scan_relationships()` | 扫描外键关系 |
| 209 | `_AMOUNT_KEYWORDS` / `_COUNT_KEYWORDS` | 金额/计数关键词 |
| 218 | `_type_matches()` | 类型匹配检查 |
| 245 | `_infer_simple_metrics()` | 规则推断简单指标 |
| 352 | `enrich_metrics()` | LLM 丰富指标描述 |
| 611 | `_make_semaphore()` | 并发控制信号量 |

### `services/knowledge_graph.py` — 图谱推断与演化
| 行号 | 定义 | 描述 |
|------|------|------|
| 38 | `NAME_PATTERN_CONFIDENCE` | name_pattern 推断置信度 (0.6) |
| 39 | `AI_INFERRED_CONFIDENCE` | LLM 推断置信度 (0.7) |
| 44 | `_ON_BLOCKED_CHARS` | ON 条件字符黑名单 |
| 47 | `_ON_WORD_RE` | ON 条件关键词黑名单 |
| 50 | `FREQUENT_JOIN_THRESHOLD` | 频繁 JOIN 阈值 (3) |
| 51 | `FREQUENT_JOIN_BOOST` | 频繁 JOIN 置信度增量 (0.1) |
| 54 | `CORRECTION_PENALTY` | 纠正惩罚 (-0.15) |
| 55 | `PRAISE_BOOST` | 点赞增量 (+0.05) |
| 56 | `MIN_CONFIDENCE` / `MAX_CONFIDENCE` | 置信度范围 [0.1, 0.95] |
| 62 | `_strip_table_prefix()` | 去除 Schema 前缀 |
| 70 | `_infer_relationships_by_name()` | 按名称模式推断关系 |
| 129 | `_infer_relationships_batch_by_llm()` | LLM 批量推断关系 |
| 245 | `infer_knowledge_graph()` | **知识图谱推断**：name_pattern + LLM |
| 316 | `mine_implicit_relationships()` | 挖掘隐式关系 |
| 374 | `apply_feedback_signals()` | 应用反馈信号调整 confidence |
| 426 | `VersionConflictError` | 版本冲突异常 |
| 442 | `linkage_memories_to_cooccurrence()` | 链路记忆 → 共现计数 |
| 468 | `_compute_confidence_updates()` | 置信度更新计算 |
| 511 | `_discover_new_pairs()` | 发现新表对 |
| 549 | `apply_confidence_updates()` | **应用置信度更新** |
| 721 | `sync_linkage_to_graph()` | **同步链路记忆到图谱** |

### `services/fewshot.py` — Few-shot 检索
| 行号 | 定义 | 描述 |
|------|------|------|
| 26 | `FEWSHOT_SCORE_THRESHOLD` | 相似度阈值 (0.5) |
| 30 | `FewShotExample` | Few-shot 示例 dataclass |
| 37 | `find_fewshot_examples()` | **检索相似 SQL 示例** |
| 98 | `format_fewshot_prompt()` | 格式化 few-shot 提示 |
| 112 | `index_fewshot_example()` | **索引新示例**（回流） |

### `services/skills_loader.py` — Skills 加载器
| 行号 | 定义 | 描述 |
|------|------|------|
| 33 | `Skill` | Skill 定义 dataclass |
| 84 | `SkillsLoader` | **Skills 加载器**：SKILL.md 解析 + 热更新 + 多租户 |
| 207 | `get_skills_loader()` | 单例工厂 |
| 217 | `reset_skills_loader()` | 重置单例（测试用） |

### `services/indexer.py` — 向量索引构建
| 行号 | 定义 | 描述 |
|------|------|------|
| 33 | `model_to_text()` | Model → 文本 |
| 72 | `metric_to_text()` | Metric → 文本 |
| 94 | `IndexResult` | 索引构建结果 dataclass |
| 100 | `build_index()` | **构建索引**：语义层 → 向量索引 |

### `services/indexer_update.py` — 索引增量更新
| 行号 | 定义 | 描述 |
|------|------|------|
| 34 | `RebuildResult` | 重建结果 dataclass |
| 41 | `rebuild_index()` | **重建索引**：delete + re-insert |

### `services/semantic_diff.py` — 语义层差异比较
| 行号 | 定义 | 描述 |
|------|------|------|
| 26 | `_COL_DIFF_ATTRS` | 列差异比较属性 |
| 30 | `diff_semantic_contents()` | 语义层内容 diff |
| 77 | `_diff_columns()` | 列级别 diff |
| 105 | `is_empty_diff()` | 空 diff 检查 |

### `services/metadata_refresher.py` — 元数据自动刷新
| 行号 | 定义 | 描述 |
|------|------|------|
| 41 | `detect_and_refresh_metadata()` | **检测并刷新元数据** |
| 93 | `_refresh_single_datasource()` | 刷新单个数据源 |
| 225 | `_merge_content()` | 新旧内容合并 |
| 296 | `_audit_refresh()` | 刷新审计日志 |

---

## backend/app/core/ — 基础设施层（18 个文件）

### `core/config.py` — 集中配置
| 行号 | 定义 | 描述 |
|------|------|------|
| 20 | `Settings` | **应用配置**：Pydantic BaseSettings，所有魔法数字集中管理 |
| 339 | `get_settings()` | 缓存单例 |
| 344 | `validate_settings_on_startup()` | 启动配置校验：CHANGE_ME 拒绝启动 |

### `core/auth.py` — 认证鉴权
| 行号 | 定义 | 描述 |
|------|------|------|
| 63 | `AuthUser` | 认证用户 Pydantic 模型 |
| 82 | `get_current_user()` | **JWT 鉴权依赖**：提取当前用户 |
| 174 | `require_role()` | **RBAC 依赖工厂**：admin/user/read_only |
| 215 | `GLOBAL_TABLES` | 全局表名集合（免租户过滤） |
| 231 | `set_current_tenant()` | 设置当前租户（contextvar） |
| 240 | `get_current_tenant()` | 获取当前租户 |
| 258 | `write_audit_log()` | **审计日志写入**：success/fail/denied 三态 |

### `core/security.py` — 密码与加密
| 行号 | 定义 | 描述 |
|------|------|------|
| 20 | `hash_password()` | bcrypt 密码哈希 |
| 34 | `verify_password()` | 密码验证 |
| 55 | `_get_fernet()` | Fernet 密码器单例 |
| 69 | `encrypt_password()` | 数据源密码加密 |
| 78 | `decrypt_password()` | 数据源密码解密 |
| 89 | `create_access_token()` | JWT Access Token 生成 |
| 105 | `create_refresh_token()` | JWT Refresh Token 生成 |
| 122 | `decode_token()` | JWT Token 解码 |

### `core/llm_client.py` — LLM 客户端
| 行号 | 定义 | 描述 |
|------|------|------|
| 37 | `get_llm_client()` | LLM 客户端单例 (AsyncOpenAI) |
| 59 | `get_embedding_client()` | Embedding 客户端单例 |
| 75 | `extract_content()` | 安全提取 LLM 响应内容 |
| 90 | `llm_chat()` | **LLM 调用**：重试/退避 + Token 追踪 + Prompt 捕获 |
| 200 | `reset_clients()` | 重置客户端（测试用） |
| 207 | `infer_column_chinese()` | LLM 推断列中文名 |

### `core/llm_json.py` — JSON 解析
| 行号 | 定义 | 描述 |
|------|------|------|
| 25 | `parse_json_response()` | **LLM JSON 解析**：Markdown 去除 + 括号匹配 + 容错 |

### `core/sql_validator.py` — SQL 三层校验
| 行号 | 定义 | 描述 |
|------|------|------|
| 31 | `_DANGEROUS_FUNCTIONS` | 危险函数黑名单 (LOAD_FILE/SLEEP/BENCHMARK 等) |
| 60 | `_collect_derivable_names()` | 收集 SQL 中可推导的表/列名 |
| 99 | `ValidationResult` | 校验结果 dataclass |
| 109 | `validate_sql()` | **三层校验**：Layer1 AST + Layer2 危险函数 + Layer3 白名单列 |

### `core/redis_client.py` — Redis 客户端
| 行号 | 定义 | 描述 |
|------|------|------|
| 36 | `get_redis()` | Redis 连接（降级模式） |
| 99 | `check_redis_health()` | 健康检查 |
| 130 | `_sanitize_url()` | URL 脱敏（日志安全） |
| 140 | `close_redis()` | 关闭连接 |

### `core/milvus_client.py` — Milvus 客户端
| 行号 | 定义 | 描述 |
|------|------|------|
| 34 | `get_milvus_client()` | Milvus 客户端单例 |
| 64 | `is_milvus_healthy()` | 健康检查 |
| 96 | `reset_milvus_client()` | 重置（测试用） |
| 115 | `close_milvus()` | 关闭连接 |

### `core/rate_limit.py` — 限流
| 行号 | 定义 | 描述 |
|------|------|------|
| 35 | `get_limiter()` | slowapi Limiter 单例 |

### `core/logging.py` — 日志
| 行号 | 定义 | 描述 |
|------|------|------|
| 29 | `setup_logging()` | 日志配置：控制台 + 每日轮转文件 |

### `core/checkpointer.py` — 检查点
| 行号 | 定义 | 描述 |
|------|------|------|
| 35 | `Checkpointer` | **JSONL 检查点**：追加写入 |
| 255 | `get_checkpointer()` | 单例工厂 |

### `core/startup_probe.py` — 启动探测
| 行号 | 定义 | 描述 |
|------|------|------|
| 41 | `ProbeResult` | 探测结果 dataclass |
| 52 | `_redact()` | URL 脱敏 |
| 66 | `probe_postgres()` | PostgreSQL 探测 |
| 88 | `probe_redis()` | Redis 探测 |
| 113 | `probe_milvus()` | Milvus 探测 |
| 141 | `_PROBES` | 探测函数映射 |
| 148 | `_probe()` | 单次探测 |
| 158 | `check_required_services()` | 必须服务探测（fail-fast） |
| 192 | `check_optional_services()` | 可选服务探测（降级） |
| 213 | `run_startup_probes()` | **运行全部启动探测** |

### `core/scheduler.py` — 定时任务
| 行号 | 定义 | 描述 |
|------|------|------|
| 33 | `get_scheduler()` | APScheduler 单例 |
| 42 | `start_scheduler()` | 启动调度器 |
| 81 | `_register_jobs()` | 注册所有定时任务 |
| 137 | `shutdown_scheduler()` | 关闭调度器 |
| 154 | `add_interval_job()` | 添加间隔任务 |
| 187 | `_run_datasource_health_check()` | 数据源健康检查定时任务 |
| 205 | `_run_metadata_refresh()` | 元数据刷新定时任务 |
| 223 | `_run_redis_health_check()` | Redis 健康检查定时任务 |

### `core/token_tracker.py` — Token 追踪
| 行号 | 定义 | 描述 |
|------|------|------|
| 24 | `NodeUsage` | 节点用量 dataclass |
| 49 | `TokenTracker` | **Token 追踪器**：contextvar 请求级 |
| 102 | `start_token_tracking()` | 开始追踪 |
| 107 | `stop_token_tracking()` | 停止追踪 |
| 116 | `track_usage()` | 记录节点用量 |
| 130 | `get_node_usage()` | 查询节点用量 |

### `core/prompt_capture.py` — Prompt 捕获
| 行号 | 定义 | 描述 |
|------|------|------|
| 27 | `PromptRecord` | Prompt 记录 dataclass |
| 48 | `PromptCapture` | **Prompt 捕获器**：contextvar 请求级 |
| 76 | `start_prompt_capture()` | 开始捕获 |
| 81 | `stop_prompt_capture()` | 停止捕获 |
| 90 | `record_prompt()` | 记录 Prompt |

### `core/prompt_cache.py` — Prompt 缓存
| 行号 | 定义 | 描述 |
|------|------|------|
| 20 | `PromptSection` | Prompt 段 dataclass |
| 51 | `PromptCache` | **TTL 缓存**：语义层/Skills 可缓存段 |
| 156 | `get_prompt_cache()` | 单例工厂 |

### `core/text_sanitize.py` — Unicode 清洗
| 行号 | 定义 | 描述 |
|------|------|------|
| 22 | `_ZERO_WIDTH` | 零宽字符正则 |
| 24 | `_DIRECTIONAL` | 方向字符正则 |
| 26 | `_PRIVATE_USE` | 私用区字符正则 |
| 28 | `_FORMAT_CONTROL` | 格式控制字符正则 |
| 31 | `sanitize_text()` | **Unicode 清洗**：NFKC 归一化 + 危险字符移除 |

### `core/agent_memory.py` — 文件记忆存储
| 行号 | 定义 | 描述 |
|------|------|------|
| 19 | `FIRST_SECTION_PATTERN` | Markdown 首段标题正则 |
| 24 | `AgentMemoryStore` | **文件记忆存储**：.md 文件 + YAML frontmatter |
| 496 | `get_agent_memory_store()` | 单例工厂 |

---

## backend/app/db/ — 数据持久化层（2 个文件）

### `db/models.py` — ORM 模型
| 行号 | 定义 | 描述 |
|------|------|------|
| 28 | `new_uuid()` | UUID 生成 |
| 32 | `utcnow()` | 当前 UTC 时间 |
| 43 | `TenantMixin` | **多租户 Mixin**：tenant_id + tenant_filter() |
| 56 | `Tenant` | 租户模型 |
| 72 | `User` | 用户模型（含 role: admin/user/read_only） |
| 99 | `DataSource` | 数据源模型（含 scan_status 状态机） |
| 135 | `SemanticModel` | 语义模型（版本化 JSON） |
| 158 | `Conversation` | 对话模型 |
| 179 | `SavedQuery` | 保存查询模型 |
| 208 | `Dashboard` | 看板模型 |
| 224 | `DashboardWidget` | 看板组件模型 |
| 259 | `AuditLog` | 审计日志模型（success/fail/denied） |

### `db/session.py` — 数据库会话
| 行号 | 定义 | 描述 |
|------|------|------|
| 26 | `Base` | SQLAlchemy DeclarativeBase |
| 44 | `_get_engine_kwargs()` | 引擎参数构建 |
| 71 | `get_engine()` | 异步引擎（懒加载） |
| 87 | `get_async_session_factory()` | 异步会话工厂 |
| 99 | `get_engine_sync()` | 同步引擎（迁移用） |
| 111 | `get_db()` | **FastAPI DB 依赖** |
| 122 | `auto_create_tables()` | **自动建表**：create_all + 增量补列 |
| 151 | `_ensure_enum_types()` | 确保枚举类型存在 |
| 172 | `_add_missing_columns()` | 增量补列 |
| 207 | `_add_missing_constraints()` | 增量补约束 |

---

## backend/app/schemas/ — Pydantic 模型（1 个文件）

### `schemas/semantic_layer.py` — 语义层 JSON Schema
| 行号 | 定义 | 描述 |
|------|------|------|
| 30 | `_Inferred` | 推断来源追踪基类 |
| 51 | `Column` | 列定义 |
| 66 | `Relationship` | 关系定义 |
| 84 | `_METRIC_DANGEROUS` | 指标危险 SQL 正则 |
| 88 | `_DDL_DML_KEYWORDS` | DDL/DML 关键词正则 |
| 95 | `Metric` | 指标定义（含复合指标展开） |
| 149 | `CalculatedField` | 计算字段定义 |
| 160 | `Model` | 表模型定义 |
| 173 | `SemanticModelContent` | 完整语义层模型 |

---

## backend/app/api/ — HTTP API 路由层（13 个文件）

### `api/__init__.py` — 路由聚合
| 行号 | 定义 | 描述 |
|------|------|------|
| 31 | `ping()` | 健康 ping |

### `api/auth.py` — 认证
| 行号 | 定义 | 描述 |
|------|------|------|
| 56 | `_validate_email()` | 邮箱格式校验 |
| 70 | `RegisterRequest` | 注册请求体 |
| 76 | `LoginRequest` | 登录请求体 |
| 81 | `RefreshRequest` | 刷新请求体 |
| 85 | `TokenResponse` | Token 响应体 |
| 97 | `register()` | **注册**：创建租户 + 用户 |
| 158 | `_check_login_lock()` | 登录锁定检查 |
| 181 | `_record_login_failure()` | 记录登录失败 |
| 192 | `_clear_login_lock()` | 清除登录锁定 |
| 199 | `login()` | **登录**：JWT 签发 |
| 281 | `refresh_token()` | **刷新 Token** |

### `api/chat.py` — 同步问答
| 行号 | 定义 | 描述 |
|------|------|------|
| 40 | `ChatRequest` | 问答请求体 |
| 47 | `AskUserPayload` | ask_user 响应体 |
| 54 | `TokenUsagePayload` | Token 用量响应体 |
| 63 | `ChatResponse` | 问答响应体 |
| 96 | `_persist_co_occurrence()` | 持久化共现计数 |
| 148 | `build_agent_deps()` | **装配 Agent 依赖** |
| 298 | `chat()` | **同步问答**端点 |

### `api/chat_stream.py` — 流式问答
| 行号 | 定义 | 描述 |
|------|------|------|
| 65 | `_sse()` | SSE 事件格式化 |
| 100 | `chat_stream()` | **流式问答**端点 (SSE) |
| 726 | `_persist_skeleton()` | 持久化对话骨架 |
| 760 | `_persist()` | 持久化完整轮次 |

### `api/data_sources.py` — 数据源管理
| 行号 | 定义 | 描述 |
|------|------|------|
| 91 | `create_data_source()` | 创建数据源 |
| 274 | `scan_data_source_endpoint()` | 触发扫描 |
| 315 | `_run_scan_background()` | 后台扫描任务 |
| 481 | `_update_scan()` / `_finish_scan()` / `_fail_scan()` | 扫描状态更新 |
| 516 | `_apply_inferred_relationships()` | 应用推断关系 |
| 538 | `_enrich_with_llm()` | LLM 丰富 |

### `api/semantic_models.py` — 语义层管理
| 行号 | 定义 | 描述 |
|------|------|------|
| 49 | `get_current_semantic_model()` | 获取当前语义层 |
| 77 | `list_versions()` | 版本列表 |
| 115 | `rollback_to_version()` | 版本回滚 |
| 241 | `patch_semantic_model()` | 语义层编辑 |
| 390 | `patch_metric()` | 指标编辑 |
| 572 | `diff_versions()` | 版本差异比较 |

### `api/dashboard.py` — 看板管理
| 行号 | 定义 | 描述 |
|------|------|------|
| 191 | `list_dashboards()` | 看板列表 |
| 217 | `create_dashboard()` | 创建看板 |
| 238 | `update_dashboard()` | 更新看板 |
| 265 | `delete_dashboard()` | 删除看板 |
| 295 | `get_dashboard()` | 看板详情 |
| 331 | `add_widget()` | 添加组件 |
| 434 | `delete_widget()` | 删除组件 |
| 458 | `update_widget_layout()` | 更新布局 |
| 500 | `refresh_widget()` | 刷新组件数据 |

### `api/graph.py` — 知识图谱 API
| 行号 | 定义 | 描述 |
|------|------|------|
| 97 | `get_full_graph()` | 全量图谱 |
| 108 | `get_subgraph()` | 子图 |
| 121 | `get_communities()` | 社区发现 |
| 133 | `get_hub_tables()` | 枢纽表 |
| 146 | `get_impact()` | 影响分析 |
| 176 | `get_join_path()` | JOIN 路径 |
| 207 | `add_relationship()` | 新增关系 |
| 275 | `delete_relationship()` | 删除关系 |
| 332 | `get_table_columns()` | 表列信息 |

### `api/observability.py` — 可观测性
| 行号 | 定义 | 描述 |
|------|------|------|
| 52 | `list_audit_logs()` | 审计日志列表 |
| 86 | `list_slow_queries()` | 慢查询列表 |
| 121 | `list_conversations()` | 对话列表 |
| 176 | `get_conversation_detail()` | 对话详情 |
| 211 | `get_conversation_trace()` | Prompt 追踪 |
| 261 | `health_detail()` | 健康详情 |
| 314 | `get_datasource_metrics()` | 数据源指标 |

### `api/saved_queries.py` — 保存查询
| 行号 | 定义 | 描述 |
|------|------|------|
| 44 | `list_saved_queries()` | 保存查询列表 |
| 72 | `get_saved_query()` | 查询详情 |
| 98 | `export_saved_query_csv()` | CSV 导出 |

### `api/skills.py` — 业务规则
| 行号 | 定义 | 描述 |
|------|------|------|
| 99 | `list_skills()` | Skills 列表 |
| 131 | `save_skill()` | 保存 Skill |
| 175 | `delete_skill()` | 删除 Skill |
| 220 | `preview_skill()` | 预览 Skill（SQL 生成） |

### `api/memory.py` — Agent 记忆
| 行号 | 定义 | 描述 |
|------|------|------|
| 162 | `list_memories()` | 记忆列表 |
| 200 | `save_memory()` | 保存记忆 |
| 230 | `delete_memory()` | 删除记忆 |
| 245 | `consolidate_memories()` | 整理记忆 |
| 272 | `get_consolidate_status()` | 整理状态 |
| 281 | `retry_consolidate_graph_sync()` | 重试图谱同步 |
| 389 | `_run_consolidate_background()` | 后台整理任务 |

---

## frontend/src/ — 前端代码

### `api/client.ts` — HTTP 客户端
| 行号 | 定义 | 描述 |
|------|------|------|
| 40 | `apiClient` | Axios 实例 + JWT 拦截器 |
| 97 | `_tryRefresh()` | Token 自动刷新 |
| 133 | `_goToLogin()` | 跳转登录 |

### `api/index.ts` — API 服务函数
| 行号 | 定义 | 描述 |
|------|------|------|
| 79 | `auth` | 认证 API |
| 157 | `datasource` | 数据源 API |
| 340 | `semantic` | 语义层 API |
| 502 | `chat` | 问答 API |
| 531 | `observability` | 可观测性 API |
| 675 | `skills` | Skills API |
| 768 | `memory` | 记忆 API |
| 850 | `dashboard` | 看板 API |
| 968 | `graph` | 图谱 API |
| 637 | `STREAM_URL` | SSE 流式端点 |

### `router/index.ts` — 路由
| 行号 | 定义 | 描述 |
|------|------|------|
| 28 | `router` | Vue Router 实例 + 导航守卫 |

### `composables/useAuth.ts` — 认证状态
| 行号 | 定义 | 描述 |
|------|------|------|
| 29 | `token` | 访问令牌 reactive ref |
| 38 | `isLoggedIn` | 登录状态 computed |
| 49 | `setToken()` | 设置 Token |
| 67 | `clearToken()` | 清除 Token |

### `utils/error.ts` — 错误处理
| 行号 | 定义 | 描述 |
|------|------|------|
| 29 | `extractErrorDetail()` | 错误详情提取 |

### `utils/exportExcel.ts` — Excel 导出
| 行号 | 定义 | 描述 |
|------|------|------|
| 56 | `exportQueryToExcel()` | 导出查询结果到 Excel（数据 + 图表） |
| 144 | `sanitizeCell()` | CSV 注入防护 |
| 163 | `base64ToBuffer()` | base64 → ArrayBuffer |

### `utils/g6-config.ts` — 图谱配置
| 行号 | 定义 | 描述 |
|------|------|------|
| 27 | `COMMUNITY_COLORS` | 社区颜色数组 |
| 48 | `getCommunityColor()` | 社区颜色获取 |
| 72 | `NODE_SIZE_MIN` / `NODE_SIZE_MAX` | 节点大小范围 [36, 72] |
| 76 | `NODE_GLOW_THRESHOLD` | 节点发光阈值 (0.3) |
| 88 | `getNodeSize()` | 节点大小计算 |
| 104 | `EDGE_CONFIDENCE_COLORS` | 边置信度颜色映射 |
| 123 | `getEdgeColor()` | 边颜色计算 |
| 133 | `EDGE_FLOW_ANIMATION` | 边流动动画配置 |
| 139 | `EDGE_FLOW_THRESHOLD` | 流动动画阈值 (0.8) |
| 159 | `D3_FORCE_LAYOUT` | D3 力导向布局配置 |
| 182 | `TOOLTIP_STYLE` | 工具提示样式 |

### `components/ConversationDetailDrawer.vue` — 对话详情抽屉
| 行号 | 定义 | 描述 |
|------|------|------|
| 308 | `loadDetail()` | 加载对话详情（轮次、意图、SQL、图表） |

### `components/SchemaGraph.vue` — 知识图谱可视化
| 行号 | 定义 | 描述 |
|------|------|------|
| 376 | `initG6()` | 初始化 G6 图实例 |
| 577 | `buildBehaviors()` | 构建交互行为（拖拽/连线） |
| 659 | `setMode()` | 切换交互模式 |
| 721 | `transformData()` | 数据转换（G6 格式） |
| 809 | `loadGraphData()` | 加载图谱数据 |
| 858 | `showAddDialog()` | 显示新增关系对话框 |
| 924 | `handleAddRelationship()` | 新增关系 |
| 963 | `handleDeleteRelationship()` | 删除关系 |

### `views/ChatView.vue` — 对话问答页
| 行号 | 定义 | 描述 |
|------|------|------|
| 880 | `send()` | **发送消息**：SSE 流式 + 管线可视化 |
| 1002 | `handleSSEEvent()` | SSE 事件处理 |
| 1219 | `sendFallback()` | 非流式兜底 |
| 1284 | `renderChart()` | ECharts 渲染 |
| 1360 | `navigateHistory()` | 输入历史导航 |
| 1372 | `onInputChange()` | 斜杠命令检测 |
| 1379 | `runSlashCommand()` | 斜杠命令执行 |
| 1449 | `answerClarify()` | 回答澄清问题 |

### `views/DataSourceView.vue` — 数据源管理页
| 行号 | 定义 | 描述 |
|------|------|------|
| 154 | `fetchList()` | 加载数据源列表 |
| 166 | `create()` | 创建数据源 |
| 185 | `scan()` | 触发扫描 |
| 199 | `startPollingScan()` | 轮询扫描进度 |
| 249 | `toggle()` | 启停数据源 |
| 270 | `checkHealth()` | 健康检查 |
| 287 | `checkAllHealth()` | 批量健康检查 |

### `views/SemanticView.vue` — 语义层编辑页
| 行号 | 定义 | 描述 |
|------|------|------|
| 382 | `fetchData()` | 加载语义层数据 |
| 419 | `openVersions()` | 版本历史 |
| 433 | `doRollback()` | 版本回滚 |
| 457 | `showDiff()` | 版本差异 |
| 477 | `startTableEdit()` | 编辑表属性 |
| 517 | `saveColEdit()` | 编辑列属性 |
| 592 | `addMetric()` | 新增指标 |
| 601 | `editMetric()` | 编辑指标 |
| 615 | `saveMetric()` | 保存指标 |
| 649 | `deleteMetric()` | 删除指标 |

### `views/DashboardView.vue` — 看板页
| 行号 | 定义 | 描述 |
|------|------|------|
| 125 | `loadDashList()` | 看板列表 |
| 138 | `switchDashboard()` | 切换看板 |
| 143 | `loadCurrentDashboard()` | 加载看板详情 |
| 185 | `initGrid()` | 初始化 GridStack 布局 |
| 271 | `saveLayout()` | 保存布局 |
| 280 | `toggleEditMode()` | 编辑模式切换 |
| 292 | `refreshWidget()` | 刷新组件 |
| 389 | `refreshAllWidgets()` | 刷新所有组件 |
| 467 | `deleteWidget()` | 删除组件 |

### `views/HistoryView.vue` — 历史记录页
| 行号 | 定义 | 描述 |
|------|------|------|
| 133 | `loadAudit()` | 加载审计日志 |
| 143 | `loadSlow()` | 加载慢查询 |
| 153 | `loadConversations()` | 加载对话列表 |
| 163 | `openConversationDetail()` | 打开对话详情 |

### `views/ObservabilityView.vue` — 系统监控页
| 行号 | 定义 | 描述 |
|------|------|------|
| 198 | `loadMetrics()` | 加载数据源指标 |
| 211 | `openTraceDialog()` | 打开 Prompt 追踪 |

### `views/MemoryView.vue` — 记忆管理页
| 行号 | 定义 | 描述 |
|------|------|------|
| 424 | `fetchData()` | 加载记忆列表 |
| 441 | `openEditor()` | 打开记忆编辑 |
| 450 | `doSave()` | 保存记忆 |
| 474 | `doDelete()` | 删除记忆 |
| 488 | `doConsolidate()` | 整理记忆 |

### `views/SkillsView.vue` — 业务规则页
| 行号 | 定义 | 描述 |
|------|------|------|
| 206 | `fetchData()` | 加载 Skills |
| 218 | `openEditor()` | 打开编辑 |
| 229 | `doPreview()` | 预览 Skill |
| 247 | `doSave()` | 保存 Skill |
| 270 | `doDelete()` | 删除 Skill |

---

*更新于 2026-08-03*