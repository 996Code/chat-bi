# Phase 1: 基础设施 — Context

**Gathered:** 2026-05-02
**Status:** Ready for planning
**Source:** Discuss-phase decisions + user adjustments

<domain>
## Phase Boundary

Phase 1 delivers the minimum viable "ask → get result" pipeline:
- User registers/logs in → connects MySQL → asks in Chinese → gets SQL + data table

**Infrastructure is lightweight, features are NOT reduced:**

- **Implementation changes only** (swap before production):
  - SQLite → PostgreSQL (same SQLAlchemy models, same Alembic migrations)
  - In-memory cache → Redis (same interface, login lock service swaps backend)
  - Chroma embedded → Chroma server (same Python API, just URL change)
- **All 27 Phase 1 requirements delivered in full** — no feature, security, or architectural reduction
- **Phase 1 does NOT need Docker to run** — `pip install` + `python main.py` starts everything
</domain>

<decisions>
## Implementation Decisions

### AUTH — Authentication
- AUTH-01: Email/password registration + login with bcrypt hash (cost 12)
- AUTH-02: JWT token auth, access 15min + refresh 7d + rotation
- AUTH-03: CORS configured (dev localhost, prod whitelist later)
- AUTH-06: Password reset via email link (30min token)
- AUTH-07: Login failure lock (5 failures → 15min lock, in-memory for v1)
- AUTH-08: Email verification (send verification link after registration)

### TENANT — Multi-tenancy
- TENANT-02: Tenant-scoped data sources (each user has independent datasource configs)
- Full multi-tenant isolation (TENANT-01, TENANT-03) deferred to Phase 3

### DS — Data Source
- DS-01: MySQL connection only (Phase 1 — other DBs deferred)
- DS-05: Connection test endpoint
- DS-06: Auto-scan table structure via INFORMATION_SCHEMA
- DS-07: Datasource edit and disconnect

### DSO — Data Source Operations
- DSO-01: Connection pool management per datasource
- DSO-02: Health check (connectivity test)
- DSO-03: Manual metadata sync (re-scan table structure)
- DSO-09: Connection parameters encrypted storage

### META — Semantic Layer
- META-01: Metadata JSON Schema storage (empty schema, full UI deferred to Phase 2)

### SQL — Text-to-SQL
- SQL-01: Natural language → SQL generation (basic LLM prompt, no RAG yet)
- SQL-02: Intent recognition (DataQuery vs Other, simple classification)
- SQL-07: Query timeout protection (30s)
- SQL-08: Result row limit (1000)

### CHART — Visualization
- CHART-02: Data table display only (full ECharts deferred to Phase 2)

### SEC — Security
- SEC-02: Database read-only account (connection configured as read-only)

### UX — User Experience
- UX-11: Empty state design (no datasource / no query / no result guidance)

### API — Interface
- API-01: RESTful API basic routes (auth, datasources, query)
- API-03: Standard error response format ({ code, message, details })

### Claude's Discretion
- Implementation details: file structure, exact function signatures, component layouts
- UI styling: use Element Plus defaults for now
- LLM provider: support OpenAI-compatible API (user configures their own key)
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Planning documents
- `.planning/ROADMAP.md` — Phase goals, tasks, dependencies
- `.planning/REQUIREMENTS.md` — All 88 requirements with phase mapping
- `.planning/PROJECT.md` — Product context, tech stack, key decisions
- `.planning/STATE.md` — Current project state

### Phase 1 requirements (27 IDs)
AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, AUTH-06, AUTH-07, AUTH-08,
TENANT-02,
DS-01, DS-05, DS-06, DS-07,
META-01,
SQL-01, SQL-02, SQL-07, SQL-08,
CHART-02,
SEC-02,
UX-11,
API-01, API-03,
DSO-01, DSO-02, DSO-03, DSO-09

### Tech stack decisions
- Backend: Python FastAPI + SQLAlchemy (async) + aiomysql
- Frontend: Vue3 + TypeScript + Element Plus + Vue Router + Pinia + Axios
- Database: SQLite (v1 dev) → PostgreSQL (prod)
- Cache: In-memory (v1 dev) → Redis (prod)
- Vector DB: Chroma embedded (v1 dev) → Chroma server (prod)
- LLM: OpenAI-compatible API (user-provided key)
- Migration: Alembic (SQLite compatible)
- AI Framework: LangGraph (deterministic workflow, not LangChain Agents)
</canonical_refs>

<specifics>
## Specific Ideas

### Phase 1 execution principle
- Minimum viable pipeline: register → login → add MySQL datasource → ask in Chinese → get SQL + JSON data + table display
- No RAG, no semantic layer UI, no ECharts, no multi-turn, no Oracle yet
- Every component should be swappable — use abstractions that allow SQLite→Postgres, memory→Redis, embedded→server transitions in later phases

### Key constraints
- All user-facing text in Chinese (error messages, prompts, UI labels)
- User provides their own LLM API key (no embedded keys)
- Phase 1 should work without Docker (pip install + python main.py)
</specifics>

<deferred>
## Deferred Ideas

- PostgreSQL support (DS-02) → Phase 2
- Full semantic layer UI (META-02~06, META-08) → Phase 2
- RAG indexes (SQL-03, SQL-04) → Phase 2
- AST validation (SQL-05) → Phase 2
- Self-healing (SQL-06) → Phase 2
- ECharts (CHART-03~09) → Phase 2
- Redis cache (PERF-01) → Phase 2
- Full multi-tenant (TENANT-01, TENANT-03) → Phase 3
- Audit logs (SEC-01) → Phase 3
</deferred>

---

*Phase: 01-infrastructure*
*Context gathered: 2026-05-02*
