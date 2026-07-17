## ADDED Requirements

### Requirement: SchemaGraph-based table expansion replaces BFS
The `expand_with_relationships()` function in `schema_utils.py` SHALL delegate to `SchemaGraph.expand_tables()` instead of performing BFS traversal. The function signature and return type SHALL remain unchanged for backward compatibility.

#### Scenario: Expansion uses shortest path between seeds
- **WHEN** expand_with_relationships is called with seeds=["biz_order_items", "biz_users"]
- **THEN** the expansion SHALL include biz_orders (the intermediate table on the shortest path) rather than all 2-hop BFS neighbors

#### Scenario: Expansion includes community tables
- **WHEN** expand_with_relationships is called with graph_expand_use_community=True and seeds belong to a community
- **THEN** the expansion SHALL include community member tables in addition to path tables

#### Scenario: Expansion falls back to 1-hop neighbors
- **WHEN** no shortest path or community expansion produces results for a seed table
- **THEN** the expansion SHALL include 1-hop neighbors of that seed table

#### Scenario: Backward compatible return type
- **WHEN** expand_with_relationships is called with the same arguments as before
- **THEN** the return type SHALL still be `list[str]` of table names, with seeds always included

#### Scenario: Fallback when SchemaGraph construction fails
- **WHEN** SchemaGraph construction fails for any reason (e.g., invalid semantic model data)
- **THEN** expand_with_relationships SHALL log a WARNING and fall back to the original BFS algorithm

### Requirement: JOIN path prompt block in SQL generation
The SQL agent prompt SHALL include an independent `【JOIN 路径】` section after the `【schema 与类型约束】` section. This section SHALL contain pre-computed JOIN paths with ON conditions for the expanded table set, so the LLM does not need to infer JOIN logic from schema context alone.

#### Scenario: JOIN path block for connected tables
- **WHEN** the SQL agent generates a prompt for tables ["biz_orders", "biz_users"] that have a direct relationship
- **THEN** the prompt SHALL include a 【JOIN 路径】 section containing: "biz_orders JOIN biz_users ON biz_orders.user_id = biz_users.id (LEFT, confidence=1.0)"

#### Scenario: JOIN path block for multi-hop connection
- **WHEN** the SQL agent generates a prompt for tables ["biz_order_items", "biz_users"] connected via biz_orders
- **THEN** the 【JOIN 路径】 section SHALL show the full path: "biz_order_items → biz_orders → biz_users" with ON conditions for each hop

#### Scenario: JOIN path block for disconnected tables
- **WHEN** the expanded table set contains tables from disconnected graph components
- **THEN** the 【JOIN 路径】 section SHALL show available paths and note "以下表无直接关联路径: <table_list>" for disconnected tables

#### Scenario: JOIN path block disabled via config
- **WHEN** graph_join_path_in_prompt is set to False
- **THEN** the SQL agent prompt SHALL NOT include the 【JOIN 路径】 section (backward compatible behavior)

### Requirement: Graph-driven expansion configuration
The system SHALL add the following configuration items to `app/core/config.py`, all controllable via environment variables:

- `graph_max_join_path_hops: int = 4` — Maximum hops for JOIN path search
- `graph_community_algorithm: str = "label_propagation"` — Community detection algorithm (label_propagation | greedy_modularity)
- `graph_expand_use_community: bool = True` — Whether to include community tables in expansion
- `graph_join_path_in_prompt: bool = True` — Whether to inject JOIN path block in SQL prompt

#### Scenario: Default configuration values
- **WHEN** no environment variables are set for graph configuration
- **THEN** the defaults SHALL be: max_join_path_hops=4, community_algorithm="label_propagation", expand_use_community=True, join_path_in_prompt=True

#### Scenario: Override via environment variable
- **WHEN** GRAPH_EXPAND_USE_COMMUNITY=False is set in the environment
- **THEN** graph_expand_use_community SHALL be False and community expansion SHALL be skipped

#### Scenario: Invalid community algorithm value
- **WHEN** GRAPH_COMMUNITY_ALGORITHM is set to an unsupported value
- **THEN** the system SHALL log a WARNING and fall back to "label_propagation"

### Requirement: Agent pipeline integration
The agent pipeline in `agent.py` and `chat_stream.py` SHALL use SchemaGraph for table expansion and JOIN context generation, replacing the direct calls to `expand_with_relationships()` and `build_schema_context()`.

#### Scenario: Agent uses SchemaGraph for expansion
- **WHEN** the agent pipeline processes a query with retrieved tables
- **THEN** it SHALL construct a SchemaGraph from the semantic model and call expand_tables() instead of expand_with_relationships()

#### Scenario: Agent injects JOIN path context
- **WHEN** the agent pipeline builds the SQL prompt
- **THEN** it SHALL call SchemaGraph.get_join_context() and inject the result as the 【JOIN 路径】 block

#### Scenario: SchemaGraph construction failure is non-fatal
- **WHEN** SchemaGraph construction or query fails during the agent pipeline
- **THEN** the pipeline SHALL log a WARNING, fall back to the original BFS + schema_context approach, and continue processing (fail-open for query quality, not fail-closed)
