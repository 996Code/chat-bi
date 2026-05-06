# /finalize — ChatBI 完工检查 + 提交推送

当前项目专属的完工检查流程。工作目录：仓库根目录。

## 步骤

### 1. 代码质量检查（通用）

从仓库根目录执行：

```bash
# TODO/FIXME/HACK/XXX
grep -rn "TODO\|FIXME\|HACK\|XXX" backend/app/ --include="*.py" | grep -v "__pycache__"
grep -rn "TODO\|FIXME\|HACK\|XXX" frontend/src/ --include="*.ts" --include="*.vue"

# print/console.log 泄漏
grep -rn "^ *print(" backend/app/ --include="*.py" | grep -v "__pycache__" | grep -v "test_"
grep -rn "console\.log\|debugger" frontend/src/ --include="*.ts" --include="*.vue" | grep -v "test_" | grep -v "node_modules"

# 注释代码残留
grep -rn "^ *# import\|^ *# from\|^ *# print" backend/app/ --include="*.py" | grep -v "__pycache__"
```

发现问题 → 列出并询问是否处理。无问题 → 继续。

### 2. 运行测试（通用发现）

自动发现项目测试脚本并执行：

```bash
# 优先 pytest（如果 backend/tests 存在）
if [ -d backend/tests ]; then
  cd backend && .venv/bin/python -m pytest tests/ -x -q --tb=short --ignore=tests/test_sql_quality.py 2>&1 | tail -5
  cd ..
fi

# 备选：npm test
if [ ! -d backend/tests ] && [ -f frontend/package.json ]; then
  cd frontend && npm test -- --run 2>&1 | tail -5
  cd ..
fi
```

- 通过 → 继续
- 失败 → **停止**，报告失败
- 无测试 → 跳过

### 3. SQL 功能完整性（个性化）

ChatBI 核心 SQL 管线模块导入检查：

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
s = QueryState.__annotations__
assert 'table_fixes' in s, 'QueryState 缺少 table_fixes'
assert 'column_fixes' in s, 'QueryState 缺少 column_fixes'
print('✅ 7 个 SQL 模块全部 OK')
"
cd ..
```

任何导入失败 → 报告具体模块，**不要提交**。

### 4. 文档更新检查（个性化）

```bash
# 检查今天的开发记录
TODAY_LOG="doc/开发记录-$(date +%Y-%m-%d).md"
[ -f "$TODAY_LOG" ] && echo "今日记录存在" || echo "⚠️ 缺少今日开发记录: $TODAY_LOG"

# 检查项目状态最后修改时间
git log -1 --format="%ci" -- doc/项目状态.md
```

- 今天无开发记录，或最近代码变更未反映在文档中 → 提示需要更新
- 用户确认更新 → 自动追加到 `doc/开发记录-YYYY-MM-DD.md` 和 `doc/项目状态.md`

### 5. 暂存 + 提交 + 推送

```bash
git add -A
git status --short
git diff --stat
```

展示暂存列表，确认提交信息后：

```bash
git commit -m "<提交信息>"
git push
```

提交信息根据 `git diff` 自动生成 conventional commit 格式，或采用用户提供的描述。

## 输出格式

```
[1/5] 代码质量检查 — ✅ 无问题
[2/5] 测试 — ✅ 192 passed
[3/5] SQL 管线 — ✅ 7/7 OK
[4/5] 文档检查 — ✅ 已更新
[5/5] 提交推送 — ✅ abc1234 pushed
```

## 规则

- 测试失败或 SQL 模块导入失败 → **不要提交**
- 不修改业务逻辑代码，只做检查
- 提交信息遵循：feat / fix / docs / refactor
