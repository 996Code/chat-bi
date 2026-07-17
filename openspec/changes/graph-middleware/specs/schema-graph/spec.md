## ADDED Requirements

### Requirement: SchemaGraph construction from SemanticModelContent
The system SHALL provide a `SchemaGraph` class that constructs a NetworkX DiGraph from a `SemanticModelContent` object. Each model SHALL become a node (with attributes: display_name, column_count, source). Each relationship SHALL become two directed edges (forward + reverse) with attributes: on, join_type, type, source, confidence, weight (weight = 1 - confidence).

#### Scenario: Construct graph from semantic layer with relationships
- **WHEN** a SchemaGraph is constructed with a SemanticModelContent containing 3 models (biz_orders, biz_users, biz_products) where biz_orders has relationships to biz_users and biz_products
- **THEN** the graph SHALL contain 3 nodes and 4 directed edges (2 relationships × 2 directions), with edge weights calculated as 1 - confidence

#### Scenario: Construct graph from semantic layer with no relationships
- **WHEN** a SchemaGraph is constructed with a SemanticModelContent containing models but no relationships
- **THEN** the graph SHALL contain nodes for each model but zero edges

#### Scenario: Construct graph from empty semantic layer
- **WHEN** a SchemaGraph is constructed with a SemanticModelContent that has no models
- **THEN** the graph SHALL be empty (0 nodes, 0 edges) and all query methods SHALL return empty results without errors

### Requirement: Shortest JOIN path via Dijkstra
The system SHALL provide a `find_join_paths(source, target)` method that uses Dijkstra's algorithm on the weighted DiGraph to find the shortest JOIN path between two tables. The method SHALL return a list of path segments, each containing the table sequence and the ON conditions along the path.

#### Scenario: Direct relationship between two tables
- **WHEN** find_join_paths is called with source="biz_orders" and target="biz_users" where a direct FK relationship exists (confidence=1.0, weight=0)
- **THEN** the method SHALL return a single path ["biz_orders", "biz_users"] with the ON condition from the relationship

#### Scenario: Indirect path through intermediate table
- **WHEN** find_join_paths is called with source="biz_order_items" and target="biz_users" where no direct relationship exists but a path exists through biz_orders
- **THEN** the method SHALL return a path ["biz_order_items", "biz_orders", "biz_users"] with ON conditions for each hop

#### Scenario: No path exists between tables
- **WHEN** find_join_paths is called with two tables that are in disconnected components
- **THEN** the method SHALL return an empty list (no path found)

#### Scenario: Multiple paths with different confidence levels
- **WHEN** two paths exist between tables — one via FK (confidence=1.0, weight=0) and one via name_pattern (confidence=0.6, weight=0.4)
- **THEN** the method SHALL return the FK path as the shortest (lowest total weight)

### Requirement: Smart table expansion with shortest path and community
The system SHALL provide an `expand_tables(seeds, max_depth, max_total)` method that replaces the current BFS expansion. The expansion strategy SHALL be: (1) find shortest paths between all seed table pairs to include intermediate JOIN tables, (2) add tables from the same community as seed tables, (3) add 1-hop neighbors as fallback, (4) stop when max_total is reached. Seed tables SHALL always be included regardless of max_total.

#### Scenario: Seeds with direct relationship
- **WHEN** expand_tables is called with seeds=["biz_orders", "biz_users"] that have a direct relationship
- **THEN** the result SHALL include both seeds and no additional intermediate tables (path is direct)

#### Scenario: Seeds requiring intermediate table
- **WHEN** expand_tables is called with seeds=["biz_order_items", "biz_users"] that require biz_orders as intermediate
- **THEN** the result SHALL include biz_orders in addition to the seeds

#### Scenario: Community expansion adds related tables
- **WHEN** expand_tables is called with seeds=["biz_orders"] and biz_orders belongs to a community containing biz_order_items and biz_payments
- **THEN** the result SHALL include community members in addition to seeds and path tables

#### Scenario: Max total limits expansion
- **WHEN** expand_tables is called with max_total=5 and expansion would produce 10 tables
- **THEN** the result SHALL contain at most 5 tables, with seed tables always included

#### Scenario: Disconnected seed table
- **WHEN** expand_tables is called with a seed table that has no relationships
- **THEN** the result SHALL include only that seed table (no expansion possible)

### Requirement: JOIN context generation for SQL prompt
The system SHALL provide a `get_join_context(tables)` method that, given a list of table names, finds the minimal set of JOIN paths connecting them and returns structured JoinPath objects containing: the table sequence, ON conditions, join types, and confidence levels.

#### Scenario: Two tables with direct relationship
- **WHEN** get_join_context is called with tables=["biz_orders", "biz_users"]
- **THEN** the method SHALL return one JoinPath with the direct ON condition and join type

#### Scenario: Three tables requiring two JOINs
- **WHEN** get_join_context is called with tables=["biz_order_items", "biz_orders", "biz_users"]
- **THEN** the method SHALL return JoinPaths covering the full connection: order_items→orders and orders→users

#### Scenario: Tables with no connecting path
- **WHEN** get_join_context is called with tables from disconnected graph components
- **THEN** the method SHALL return partial paths for connected subsets and log a WARNING for disconnected tables

### Requirement: Community detection
The system SHALL provide a `get_communities()` method that detects table communities using NetworkX label_propagation_communities (default) or greedy_modularity_communities (configurable via `graph_community_algorithm`). Each community SHALL be returned as a list of table names.

#### Scenario: Tables cluster into business domains
- **WHEN** get_communities is called on a graph where order-related tables are densely connected and user-related tables form a separate cluster
- **THEN** the method SHALL return at least 2 communities grouping tables by business domain

#### Scenario: Fully connected graph
- **WHEN** get_communities is called on a graph where all tables are interconnected
- **THEN** the method SHALL return a single community containing all tables

#### Scenario: Empty graph
- **WHEN** get_communities is called on an empty graph
- **THEN** the method SHALL return an empty list

### Requirement: Hub table identification via centrality
The system SHALL provide a `get_hub_tables(top_k)` method that computes degree centrality and returns the top-k tables with highest centrality scores. Each result SHALL include the table name and its centrality score.

#### Scenario: Identify hub tables
- **WHEN** get_hub_tables is called with top_k=3 on a graph where biz_users has 10 relationships and other tables have 2-3
- **THEN** the method SHALL return biz_users as the top hub with the highest centrality score

#### Scenario: Top_k exceeds available tables
- **WHEN** get_hub_tables is called with top_k=50 on a graph with 10 tables
- **THEN** the method SHALL return all 10 tables sorted by centrality

### Requirement: Impact analysis
The system SHALL provide a `get_impact(table)` method that returns all tables reachable from the given table (downstream dependents), useful for understanding which tables are affected by changes to a given table.

#### Scenario: Hub table has broad impact
- **WHEN** get_impact is called for biz_users which is referenced by 5 other tables
- **THEN** the method SHALL return all 5 dependent tables

#### Scenario: Leaf table has minimal impact
- **WHEN** get_impact is called for a table with no outgoing relationships
- **THEN** the method SHALL return an empty list

### Requirement: Graph modification — add and remove relationships
The system SHALL provide `add_relationship(from_table, rel)` and `remove_relationship(from_table, target)` methods for modifying the graph structure. These methods SHALL update both forward and reverse edges.

#### Scenario: Add new relationship
- **WHEN** add_relationship is called with from_table="biz_orders" and a Relationship targeting "biz_categories"
- **THEN** the graph SHALL contain both forward edge (orders→categories) and reverse edge (categories→orders)

#### Scenario: Remove existing relationship
- **WHEN** remove_relationship is called with from_table="biz_orders" and target="biz_users"
- **THEN** the graph SHALL remove both forward and reverse edges between these tables

#### Scenario: Remove non-existent relationship
- **WHEN** remove_relationship is called for a table pair that has no relationship
- **THEN** the method SHALL log a WARNING and make no changes (no error raised)

### Requirement: Visualization data export
The system SHALL provide `to_vis_data()` and `to_vis_subgraph(center, depth)` methods that export graph data in a format suitable for AntV G6 rendering. The export SHALL include nodes (with id, label, community, centrality attributes) and edges (with source, target, on, confidence, relSource, joinType, cardinality attributes). Note: edge `source` field is renamed to `relSource` to avoid conflict with G6's built-in `source` field (node ID).

#### Scenario: Full graph export
- **WHEN** to_vis_data is called on a graph with 5 tables and 8 relationships
- **THEN** the result SHALL contain 5 node objects and 8 edge objects (forward edges only, no reverse duplicates for visualization)

#### Scenario: Subgraph export with depth
- **WHEN** to_vis_subgraph is called with center="biz_orders" and depth=2
- **THEN** the result SHALL contain only nodes within 2 hops of biz_orders and edges between those nodes

#### Scenario: Subgraph for non-existent table
- **WHEN** to_vis_subgraph is called with a center table that does not exist in the graph
- **THEN** the method SHALL return empty nodes and edges lists
