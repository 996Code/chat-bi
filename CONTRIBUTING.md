# 贡献指南

感谢你对 ChatBI 的关注！我们欢迎任何形式的贡献。

## 🚀 快速开始

### 开发环境搭建

1. **Fork & Clone**

```bash
git clone https://github.com/你的用户名/chat-bi.git
cd chat-bi
```

2. **启动基础设施**

```bash
docker compose -f docker/docker-compose.infra.yml up -d
```

3. **安装后端依赖**

```bash
uv sync
```

4. **安装前端依赖**

```bash
cd frontend && npm install
```

5. **配置环境变量**

```bash
cp backend/.env.example backend/.env
# 编辑 .env 填入 LLM API Key 等必填项
```

6. **下载 Embedding 模型**

```bash
uv run python backend/scripts/download_embedding_model.py
```

7. **启动开发服务**

```bash
# 后端
./start-backend.sh

# 前端 (新终端)
cd frontend && npx vite --host 0.0.0.0 --port 5173
```

---

## 📋 代码规范

### Python

- **Python 3.12+**：使用 `X | None` 代替 `Optional[X]`
- **配置不硬编码**：所有魔法数字/超时/阈值进 `app/core/config.py`，走环境变量
- **安全 Fail-Closed**：安全相关功能出问题拒绝而非放行，降级要 WARNING 日志
- **宁缺毋滥**：检索/匹配失败返回空 + 明确提示，禁止"不要返回空"类指令（诱导幻觉）
- **不碰真实数据库**：测试用 SQLite in-memory（conftest.py 隔离）

### TypeScript / Vue

- Vue 3 Composition API (`<script setup>`)
- Element Plus 组件库
- API 调用统一走 `frontend/src/api/` 封装层

### 通用

- 代码风格与现有文件保持一致
- 注释使用中文
- 函数/类有清晰的 docstring

---

## 🧪 测试

**跑测试再交付**，这是不可妥协的约束：

```bash
# 运行全部测试
uv run pytest backend/tests/ -q

# 带覆盖率
uv run pytest --cov=backend/app

# 只跑核心测试 (无需 LLM API)
uv run pytest backend/tests/test_auth.py backend/tests/test_infrastructure.py backend/tests/test_v2_new_features.py -q
```

---

## 📝 提交规范

### Commit Message 格式

```
<type>(<scope>): <description>
```

类型（type）：

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `refactor` | 重构 (不改变功能) |
| `docs` | 文档更新 |
| `test` | 测试相关 |
| `chore` | 构建/配置/工具 |
| `perf` | 性能优化 |
| `style` | 代码格式 (不影响逻辑) |

示例：

```
feat(T050): 可观测性对话列表增加 token 用量展示
fix(chat): 修复 turn_number 永远是 1 的 bug
docs: 更新 README 和贡献指南
```

### 分支命名

- 功能开发：`feat/T050-observability-token-usage`
- Bug 修复：`fix/turn-number-always-one`
- 文档：`docs/readme`

---

## 🔄 PR 流程

1. 从 `main-v2` 创建功能分支
2. 开发 + 测试（确保测试通过）
3. 提交 PR 到 `main-v2`，描述清楚改动内容
4. 等待 Review

### PR 描述模板

```
## 改动说明
简述本次改动的内容和动机

## 改动类型
- [ ] 新功能
- [ ] Bug 修复
- [ ] 重构
- [ ] 文档
- [ ] 其他

## 测试
- [ ] 已通过 `uv run pytest backend/tests/ -q`

## 相关 Issue
关闭 #xxx
```

---

## 🐛 问题反馈

- 使用 [GitHub Issues](https://github.com/996Code/chat-bi/issues) 提交 Bug 或功能建议
- 安全漏洞请参考 [SECURITY.md](SECURITY.md)

---

## 📌 注意事项

- **不要提交** `backend/.env` 文件（已在 `.gitignore`）
- **不要修改** `doc/Claude-Code-源码深度解读.md`、`doc/海泰ChatBI完整代码分析.md`、`doc/经验教训.md`（只读参考文档）
- **不要修改** `doc/v1-archive/` 目录下的文件（V1 归档）
- LLM API 调用相关的测试可能需要真实 API Key，标记为 `@pytest.mark.slow`

---

感谢你的贡献！ 🎉
