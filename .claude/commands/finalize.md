# /finalize — ChatBI 完工检查 + 提交推送

项目专属的完整闭环流程：代码检查 → 补全测试 → 运行测试 → 补全文档 → 提交推送。

## 步骤

### 1. 代码质量检查

```bash
grep -rn "TODO\|FIXME\|HACK\|XXX" backend/app/ --include="*.py" | grep -v "__pycache__"
grep -rn "TODO\|FIXME\|HACK\|XXX" frontend/src/ --include="*.ts" --include="*.vue"
grep -rn "^ *print(" backend/app/ --include="*.py" | grep -v "__pycache__" | grep -v "test_" | grep -v "conftest"
grep -rn "console\.log\|debugger" frontend/src/ --include="*.ts" --include="*.vue" | grep -v "node_modules" | grep -v ".test."
grep -rn "^ *# import\|^ *# from\|^ *# print" backend/app/ --include="*.py" | grep -v "__pycache__"
```

发现问题 → 列出并询问是否处理。无问题 → 继续。

### 2. SQL 管线完整性验证

```bash
cd backend
.venv/bin/python -c "
from app.ai.nodes.self_heal import extract_error_code, build_fix_prompt, _strip_markdown, self_heal_sql
from app.ai.graph import build_graph, QueryState
from app.ai.nodes.schema_selection import schema_selection_node
from app.ai.nodes.generation import generate_sql
from app.ai.nodes.execution import execute_sql
from app.ai.nodes.intent import classify_intent
from app.ai.nodes.sql_explainer import explain_sql
from app.services.cache_service import cache_get, cache_set, semantic_cache_get
from app.services.audit_service import log_action, get_slow_queries, get_slow_query_stats
from app.services.evaluation_service import run_evaluation
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.backup_service import create_backup, restore_backup
s = QueryState.__annotations__
assert 'table_fixes' in s, 'QueryState 缺少 table_fixes'
assert 'column_fixes' in s, 'QueryState 缺少 column_fixes'
modules = [
    'self_heal', 'graph', 'schema_selection', 'generation', 'execution',
    'intent', 'sql_explainer', 'cache_service', 'audit_service',
    'evaluation_service', 'scheduler', 'backup_service',
]
print(f'✅ {len(modules)} 个模块全部 OK')
"
cd ..
```

任何导入失败 → 报告具体模块，**不要继续**。

### 3. 补全测试脚本

**分析最近代码变更，检查是否有对应的测试覆盖。**

```bash
# 查看最近一次提交以来的代码变更文件
CHANGED_FILES=$(git diff HEAD~1 --name-only 2>/dev/null | grep -v "__pycache__" | grep -v ".md$" | grep -v ".claude/" || echo "")
```

- 有代码变更 → 检查这些文件是否有对应的测试。如果没有或覆盖不足，补充测试用例到 `backend/tests/test_comprehensive.py`
- 无代码变更 → 跳过

**补全测试的原则：**
- 新增的 API 端点 → 补对应的请求测试
- 新增的服务函数 → 补对应的逻辑测试
- 新增的配置项 → 补默认值测试
- 新增的模型 → 补 CRUD 测试
- 已有测试但变更的函数 → 更新断言

### 4. 运行全部测试

```bash
cd backend
.venv/bin/python -m pytest tests/ -x -q --tb=short --ignore=tests/test_sql_quality.py
cd ..
```

- 通过 → 继续
- 失败 → **停止**，修复失败项，重新测试，直到通过
- 修复后重新跑全量测试

### 5. 补全项目文档

**根据代码变更自动补全以下文档：**

```bash
# 获取最近的提交和变更
git log -1 --format="%H %ai %s"
git diff HEAD~1 --stat
```

**a. `doc/开发记录-YYYY-MM-DD.md`：**
- 如果今天还没有 → 创建
- 追加今日改动：提交记录、改动概要表格、关键修复说明
- 更新测试结果（如果改了测试）

**b. `doc/项目状态.md`：**
- 更新"已补全的 Partial 需求"表格（如果新增）
- 更新"近期完成"列表
- 更新"下一步行动"（如果 roadmap 有变更）

**c. `doc/路线图.md`：**
- 如果有任务完成或取消 → 更新路线图状态
- 更新 phase 进度

**d. `README.md`：**
- 如果有新的重要功能 → 更新需求追溯矩阵

### 6. 暂存 + 提交 + 推送

```bash
git add -A
git status --short
git diff --cached --stat
```

展示暂存列表，确认提交信息：

```bash
git commit -m "<提交信息>"
git push
```

## 输出格式

```
[1/6] 代码质量检查 — ✅ 无问题
[2/6] SQL 管线 — ✅ 12 个模块 OK
[3/6] 测试补全 — +5 个新测试用例
[4/6] 全部测试 — ✅ 197 passed
[5/6] 文档补全 — ✅ 开发记录 + 项目状态 已更新
[6/6] 提交推送 — ✅ abc1234 pushed
```

## 规则

- SQL 模块导入失败 → **不要提交**
- 测试失败 → **不要提交**，修复后再跑
- 测试脚本必须随代码变更同步补全
- 文档必须随代码变更同步更新
- 提交信息遵循：feat / fix / docs / refactor
