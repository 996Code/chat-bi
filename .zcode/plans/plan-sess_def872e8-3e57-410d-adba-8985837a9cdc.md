## 三项任务一起做

### 任务 1: 记录 seed_memories.py 脚本到项目记忆

写入 `/Users/xiaotaotao/.zcode/cli/memories/projects/chat-bi-102b3575d6b54fcb/topics/` 一个新的 topic 文件 + 更新 MEMORY.md 索引。

### 任务 2: 整理记忆支持勾选 + 全选

**需求**: 每条记忆卡片前加 checkbox，全选按钮（只选未整理的），已整理的禁用勾选，"整理记忆"只整理勾选的记忆。

**后端** — `backend/app/api/memory.py`:
- `POST /memory/consolidate` 接受可选 body `ConsolidateRequest { names: list[str] | None }`
- 传 names 时只整理指定的记忆；不传时整理全部（未整理的）

**后端** — `backend/app/ai/recall.py`:
- `consolidate_memories(store, memory_dir, names=None)` — names 不为 None 时只处理指定记忆

**前端** — `frontend/src/api/index.ts`:
- `consolidate(dataSourceId, names?: string[])` — POST 带 body

**前端** — `frontend/src/views/MemoryView.vue`:
- 加 `selectedNames = ref<Set<string>>(new Set())`
- 未整理的记忆卡片左上角加 `el-checkbox`（已整理的禁用）
- 页面顶部"显示已整理"开关旁加全选 checkbox（只选当前未整理的）
- "整理记忆"按钮传 selectedNames，整理后清空勾选 + 刷新
- 整理记忆数 = 0 时按钮 disabled

### 任务 3: 修复 expand_with_relationships BFS 扩散 (核心 bug)

**根因**: `uc_users` 有 45 个邻居，`biz_orders → uc_users` 后 2 跳 BFS 扩散到 94/121 张表。schema_context 注入 32KB，LLM 被淹没。

**修复** — `backend/app/core/config.py`:
- 新增 `rag_max_schema_tables: int = 20`（扩展后总表数上限）

**修复** — `backend/app/ai/schema_utils.py` `expand_with_relationships`:
- BFS 按 1 跳 → 2 跳顺序扩展（当前已是）
- 增加全局计数器：`len(result_set) >= max_total` 时停止添加新表
- 种子表（向量检索命中的）永远保留
- 从 config 读 `rag_max_schema_tables`，超过时 WARNING 日志

**预期效果**: "销量最高的前五个商品分类及其对应的平均客单价" → 4 张种子表 + ~16 张关联表 = 20 张（而非 94 张），schema_context 从 32KB 降到 ~7KB。

### 测试

- 跑 `.venv/bin/python -m pytest backend/tests/ -q` 确认 74 passed 不破坏
- 新增 `test_expand_with_relationships_cap` 测试：构造含超级枢纽的语义层，验证扩展后表数 ≤ max_schema_tables
