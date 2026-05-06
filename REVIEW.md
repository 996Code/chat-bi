---
phase: 02-code-review-command
reviewed: 2026-05-06T00:00:00Z
depth: deep
files_reviewed: 7
files_reviewed_list:
  - backend/app/api/query.py
  - backend/app/services/evaluation_service.py
  - backend/app/api/evaluation.py
  - frontend/src/views/ChatView.vue
  - frontend/src/views/MonitoringView.vue
  - frontend/src/views/EvaluationView.vue
  - frontend/src/components/PipelineTraceDialog.vue
findings:
  critical: 2
  warning: 2
  info: 1
  total: 5
status: issues_found
---

# Phase 02: Code Review Report

**Reviewed:** 2026-05-06
**Depth:** deep
**Files Reviewed:** 7
**Status:** issues_found

## Summary

A deep cross-file review was performed on 7 files covering the stream query endpoint, evaluation service/API, three frontend views, and the PipelineTraceDialog component. Two BLOCKER-level bugs were found that will cause immediate runtime failures in the evaluation pipeline, plus two WARNING-level issues affecting metrics accuracy and user experience.

## Critical Issues

### CR-01: evaluation.py passes wrong arguments to run_evaluation — runtime failure

**File:** `backend/app/api/evaluation.py:46`

**Issue:** The function signature of `run_evaluation` is:
```python
async def run_evaluation(tenant_id: str, datasource_id: str, dataset: list[dict]) -> dict
```
But the call at line 46 passes `db` (AsyncSession) as the first positional argument:
```python
result = await run_evaluation(db, tenant_id, datasource_id, dataset)
```
This means:
- `tenant_id` receives the AsyncSession object (not a string)
- `datasource_id` receives `admin["tenant_id"]` (wrong tenant)
- `dataset` receives the actual `datasource_id` string
- `dataset` parameter gets an extra 4th argument that the function doesn't accept

The evaluation will either crash immediately (TypeError for 4 positional args when function expects 3) or produce completely wrong results if Python somehow binds them (it won't -- this is a hard crash).

**Fix:** Either remove the `db` parameter from the call:
```python
result = await run_evaluation(tenant_id, datasource_id, dataset)
```
Or add `db` as the first parameter to the `run_evaluation` function signature if it was intended to be used there. Tracing the service code, `db` is not used inside `run_evaluation`, so the fix is to remove it from the call.

### CR-02: execute_sql called with swapped datasource_id and tenant_id in cache hit path

**File:** `backend/app/services/evaluation_service.py:92`

**Issue:** The `execute_sql` function signature is:
```python
async def execute_sql(sql: str, datasource_id: str, dialect: str = "mysql", tenant_id: str | None = None) -> dict
```
The call at line 92:
```python
exec_result = await execute_sql(cached_sql, datasource_id, tenant_id=tenant_id)
```
This looks correct at first glance, BUT the surrounding code has the same parameter shift bug from CR-01 cascading through. Since `run_evaluation` receives `admin["tenant_id"]` as its `datasource_id` parameter (and `datasource_id` as its `dataset` parameter), the `datasource_id` variable inside the function actually holds the tenant ID string, and the `tenant_id` variable holds the AsyncSession object. So the actual execution will target the wrong datasource and pass a non-string tenant_id.

Even if CR-01 is fixed (removing `db` from the call), this line is correct as written -- the positional `datasource_id` and keyword `tenant_id=tenant_id` match the signature properly. However, the cascading effect of CR-01 means these variables contain wrong values at runtime.

**Fix:** Fix CR-01 first. Once `run_evaluation` receives correct argument bindings, this line works correctly as-is. No change needed to this line.

## Warnings

### WR-01: total_time_ms not accumulated for cache-hit queries, skewing avg_time_ms metric

**File:** `backend/app/services/evaluation_service.py:129-130, 233`

**Issue:** `total_time_ms` is only incremented inside the `if cache_type is None:` block (line 130: `total_time_ms += elapsed_ms`). For cache-hit queries, the code takes the `if cache_type:` branch (line 59), executes SQL (line 92), and then skips the pipeline block entirely. `total_time_ms` remains 0 for these queries.

This value feeds into the summary metric at line 273:
```python
"avg_time_ms": int(total_time_ms / total) if total > 0 else 0,
```
Cache-hit queries contribute 0ms to the average, artificially deflating `avg_time_ms` in evaluation reports.

**Fix:** Add the cache-hit execution time to `total_time_ms`:
```python
# After line 107, inside the cache_hit block:
exec_ms = int((time.monotonic() - exec_start) * 1000)
total_time_ms += exec_ms  # ADD THIS LINE
```
Or better, use the `total_ms` computed at line 233 for each result and sum those in a separate accumulator.

### WR-02: EvaluationView trace dialog missing queryInfo prop — reduced context

**File:** `frontend/src/views/EvaluationView.vue:165`

**Issue:** The PipelineTraceDialog component supports an optional `queryInfo` prop that displays datasource name, question, total time, SQL time, status, and error above the step table. MonitoringView.vue uses it correctly (line 97):
```html
<PipelineTraceDialog ... :query-info="traceQueryInfo" />
```
But EvaluationView.vue omits it:
```html
<PipelineTraceDialog v-model="showTraceDialog" :steps="traceSteps" title="评估执行链路" />
```
The evaluation result rows contain `question`, `time_ms`, `error`, `cached`, `cache_type` -- all the data needed to populate queryInfo. Without it, users viewing a trace from the evaluation table lose the summary context (datasource, question, timing) that the monitoring view provides.

**Fix:** Add a computed property for traceQueryInfo (similar to MonitoringView) and pass it:
```html
<PipelineTraceDialog v-model="showTraceDialog" :steps="traceSteps" title="评估执行链路" :query-info="traceQueryInfo" />
```
With a computed:
```ts
const traceQueryInfo = computed(() => {
  const row = traceRow.value
  if (!row) return undefined
  return {
    question: row.question,
    totalTime: row.time_ms,
    status: (row.error ? 'error' : 'success') as 'success' | 'error',
    error: row.error,
    cached: row.cached,
    cache_type: row.cache_type,
  }
})
```

## Info

### IN-01: chatStore handleSSEEvent marks wrong step as failed when data event fails

**File:** `frontend/src/stores/chatStore.ts:301`

**Issue:** When a data SSE event reports failure (`data.success === false`), the code falls through to:
```typescript
const runStep = msg.pipelineSteps?.find(s => s.status === 'running')
```
This finds the FIRST running step, not specifically the data step. In a self-heal scenario where a new SQL step is yielded (line 411 of query.py) before a new data event, there could be a running SQL step in the pipeline. The `find()` would mark that SQL step as failed instead of the data step.

In practice, the existing logic at line 281 (`if (!msg.pipelineSteps?.some(s => s.type === 'data'))`) prevents duplicate data steps, so the data step is usually the only running step. But in edge cases (rapid self-heal yields), this could mark the wrong step.

**Fix:** Change line 301 to specifically find the data step:
```typescript
const runStep = msg.pipelineSteps?.find(s => s.type === 'data' && s.status === 'running')
 || msg.pipelineSteps?.find(s => s.status === 'running')  // fallback
```

---

_Reviewed: 2026-05-06_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
