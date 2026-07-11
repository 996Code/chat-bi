## 记忆整理异步化 — 异步+轮询模式

对标数据源扫描的 `asyncio.create_task()` + 状态轮询模式，但**不新增 DB 表**（记忆是文件存储，用内存 dict 跟踪状态即可）。

### 后端改动

**1. `backend/app/api/memory.py` — 新增状态管理 + 异步端点**

新增内存状态 dict：
```python
# { (tenant_id, ds_id): ConsolidateStatus }
_consolidate_status: dict[tuple[str, str], ConsolidateStatus] = {}

class ConsolidateStatus(BaseModel):
    status: str = "idle"          # idle | running | done | failed
    progress: int = 0             # 0-100
    stage: str = ""               # 当前步骤文字
    result: dict | None = None    # 完成后的结果
    error: str | None = None      # 失败原因
```

改造 `POST /memory/consolidate`：
- 检查是否已在运行（`status == "running"` → 409）
- 设置 `status="running", progress=5, stage="准备中"`
- `asyncio.create_task(_run_consolidate_background(...))`
- 返回 **202 Accepted** + 当前 ConsolidateStatus

新增 `GET /memory/consolidate/status`：
- 返回当前 ConsolidateStatus（无记录时返回 idle）

新增后台任务 `_run_consolidate_background(tenant_id, ds_id, ids)`：
- 独立 session（和扫描一样）
- 分阶段更新进度：
  - 10% "读取记忆内容..."
  - 30% "调用 LLM 整理中..."（最耗时）
  - 80% "写入整理结果..."
  - 90% "标记原始记忆..."
  - 100% "完成"
- 成功：`status="done", result={consolidated, total, detail}`
- 失败：`status="failed", error=str(e)`

**2. `backend/app/ai/recall.py` — 拆分 consolidate_memories 为可回调进度的版本**

新增 `consolidate_memories_async(store, memory_dir, ids, on_progress)`：
- `on_progress(progress: int, stage: str)` 回调函数
- 逻辑和原版完全一致，只在关键步骤间调用 `on_progress`
- 原版 `consolidate_memories` 保留（测试用），内部调用 async 版

### 前端改动

**3. `frontend/src/api/index.ts` — 新增状态查询 API**

```typescript
consolidate(dataSourceId: string, ids?: string[]) {
  return apiClient.post('/memory/consolidate', ids ? { ids } : null, { params: { data_source_id: dataSourceId } })
},
consolidateStatus(dataSourceId: string) {
  return apiClient.get<{ status: string; progress: number; stage: string; result?: any; error?: string }>(
    '/memory/consolidate/status', { params: { data_source_id: dataSourceId } }
  )
},
```

**4. `frontend/src/views/MemoryView.vue` — 轮询进度 UI**

- 点击"整理记忆"后，POST 返回 202，启动 2 秒轮询 `consolidateStatus`
- 按钮显示进度条：`整理中 (30%) — 调用 LLM 整理中...`
- `status === "done"` → 停轮询，显示成功消息，刷新列表
- `status === "failed"` → 停轮询，显示错误消息
- 连续 10 次网络错误 → 停轮询
- `onUnmounted` 清理定时器
- 页面加载时检查是否有 running 状态（用户刷新页面后能恢复进度）

### 测试

- 跑 `.venv/bin/python -m pytest backend/tests/ -q` 确认不破坏
- 新增测试：验证 consolidate 端点返回 202、状态查询返回正确进度、防重复 409