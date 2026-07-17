## ADDED Requirements

### Requirement: Graph API endpoints for visualization and analysis
The system SHALL expose REST API endpoints under `/graph` prefix for graph data retrieval. All endpoints SHALL require `data_source_id` parameter and use the current semantic model for that data source to construct the SchemaGraph.

#### Scenario: Get full graph data
- **WHEN** a GET request is made to `/graph?data_source_id=<id>`
- **THEN** the system SHALL return the full graph visualization data (nodes + edges) from `SchemaGraph.to_vis_data()`

#### Scenario: Get subgraph centered on a table
- **WHEN** a GET request is made to `/graph/subgraph?data_source_id=<id>&center=biz_orders&depth=2`
- **THEN** the system SHALL return the subgraph visualization data within 2 hops of biz_orders

#### Scenario: Get community list
- **WHEN** a GET request is made to `/graph/communities?data_source_id=<id>`
- **THEN** the system SHALL return a list of communities, each containing table names

#### Scenario: Get hub tables
- **WHEN** a GET request is made to `/graph/hubs?data_source_id=<id>&top_k=10`
- **THEN** the system SHALL return the top 10 hub tables with centrality scores

#### Scenario: Get impact analysis
- **WHEN** a GET request is made to `/graph/impact?data_source_id=<id>&table=biz_users`
- **THEN** the system SHALL return all tables impacted by changes to biz_users

#### Scenario: Get JOIN path between two tables
- **WHEN** a GET request is made to `/graph/join-path?data_source_id=<id>&source=biz_orders&target=biz_users`
- **THEN** the system SHALL return the shortest JOIN path with ON conditions

#### Scenario: Data source not found
- **WHEN** a GET request is made to `/graph?data_source_id=<nonexistent>`
- **THEN** the system SHALL return 404 with an error message

#### Scenario: No semantic model for data source
- **WHEN** a GET request is made to `/graph?data_source_id=<id>` but no semantic model exists for that data source
- **THEN** the system SHALL return 200 with empty graph data (nodes=[], edges=[])

### Requirement: Graph API endpoints for relationship modification
The system SHALL expose POST and DELETE endpoints for adding and removing relationships on the graph. These modifications SHALL be persisted by updating the semantic model (creating a new version via the existing patch mechanism).

#### Scenario: Add relationship via API
- **WHEN** a POST request is made to `/graph/relationship?data_source_id=<id>` with body containing from_table and Relationship data
- **THEN** the system SHALL add the relationship to the semantic model (creating a new version) and return the updated graph data

#### Scenario: Delete relationship via API
- **WHEN** a DELETE request is made to `/graph/relationship?data_source_id=<id>&from=biz_orders&target=biz_users`
- **THEN** the system SHALL remove the relationship from the semantic model (creating a new version) and return success

#### Scenario: Add relationship with invalid target
- **WHEN** a POST request is made to add a relationship targeting a non-existent table
- **THEN** the system SHALL return 400 with an error message indicating the target table does not exist

### Requirement: Frontend graph API client
The frontend SHALL provide a `graph` API object in `src/api/index.ts` with methods matching all backend graph endpoints.

#### Scenario: Fetch full graph data from frontend
- **WHEN** `graph.full(dataSourceId)` is called
- **THEN** it SHALL make a GET request to `/graph?data_source_id=<id>` and return the graph data

#### Scenario: Fetch subgraph from frontend
- **WHEN** `graph.subgraph(dataSourceId, center, depth)` is called
- **THEN** it SHALL make a GET request to `/graph/subgraph` with the appropriate parameters

### Requirement: ER graph visualization component
The system SHALL provide a `SchemaGraph.vue` component using AntV G6 that renders an ER diagram with force-directed layout. Nodes SHALL represent tables, edges SHALL represent relationships. The component SHALL support: drag, zoom, hover tooltips, click-to-focus, minimap, and community-based node coloring.

#### Scenario: Render full ER graph
- **WHEN** the SchemaGraph component receives graph data with 20 nodes and 30 edges
- **THEN** it SHALL render a force-directed layout with all nodes and edges visible, nodes colored by community

#### Scenario: Hover tooltip shows relationship details
- **WHEN** the user hovers over an edge in the ER graph
- **THEN** a tooltip SHALL appear showing the ON condition, join type, source, and confidence of that relationship

#### Scenario: Click node to focus subgraph
- **WHEN** the user clicks a node in the ER graph
- **THEN** the view SHALL highlight the clicked node and its 1-hop neighbors, dimming other nodes

#### Scenario: Drag node to reposition
- **WHEN** the user drags a node in the ER graph
- **THEN** the node SHALL move to the new position and connected edges SHALL follow

#### Scenario: Zoom and pan
- **WHEN** the user scrolls the mouse wheel or drags the canvas background
- **THEN** the graph SHALL zoom in/out or pan accordingly

#### Scenario: Minimap navigation
- **WHEN** the ER graph is rendered
- **THEN** a minimap SHALL be displayed in the corner showing the full graph overview with a viewport indicator

#### Scenario: Empty graph state
- **WHEN** the SchemaGraph component receives empty graph data (no nodes)
- **THEN** it SHALL display an empty state message instead of a blank canvas

### Requirement: SemanticView graph tab integration
The SemanticView SHALL add a "图谱" tab alongside the existing detail view. When selected, it SHALL display the SchemaGraph component for the current data source's semantic model.

#### Scenario: Switch to graph tab
- **WHEN** the user clicks the "图谱" tab in SemanticView
- **THEN** the SchemaGraph component SHALL load and render the ER graph for the current data source

#### Scenario: Switch back to detail view
- **WHEN** the user switches from the "图谱" tab back to the detail tab
- **THEN** the graph component SHALL be hidden (not destroyed, to preserve state)

#### Scenario: Graph tab loads data source graph
- **WHEN** the SemanticView has a data source selected and the user opens the graph tab
- **THEN** the graph SHALL be fetched via `graph.full(dataSourceId)` and rendered

### Requirement: Graph relationship editing
The SchemaGraph component SHALL support adding and deleting relationships directly on the graph visualization. Adding a relationship SHALL open a form to specify the target table, ON condition, join type, and cardinality. Deleting SHALL require confirmation.

#### Scenario: Add relationship from graph
- **WHEN** the user clicks an "add relationship" button and fills in the form with target table, ON condition, join type, and cardinality
- **THEN** the system SHALL call the POST `/graph/relationship` endpoint and update the graph visualization

#### Scenario: Delete relationship from graph
- **WHEN** the user right-clicks an edge and selects "delete relationship" and confirms
- **THEN** the system SHALL call the DELETE `/graph/relationship` endpoint and remove the edge from the visualization

#### Scenario: Add relationship with missing required fields
- **WHEN** the user submits the add relationship form without filling the ON condition
- **THEN** the form SHALL show a validation error and not submit

### Requirement: G6 lazy loading
The AntV G6 library SHALL be loaded lazily (dynamic import) only when the SchemaGraph component is mounted. This prevents the ~500KB library from affecting initial page load.

#### Scenario: G6 not loaded on initial page load
- **WHEN** the user first loads the application and navigates to SemanticView
- **THEN** the G6 library SHALL NOT be included in the initial JavaScript bundle

#### Scenario: G6 loaded when graph tab is activated
- **WHEN** the user clicks the "图谱" tab for the first time
- **THEN** the G6 library SHALL be dynamically imported and a loading indicator shown until ready
