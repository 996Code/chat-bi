# ChatBI 文档索引

> 自然语言转 SQL 的 BI 平台 — 让不会 SQL 的人也能自助完成数据查询和可视化

---

## 快速导航

| 文档 | 说明 |
|------|------|
| [项目概览](.planning/项目概览.md) | 项目背景、核心价值、技术栈、团队角色 |
| [需求文档](.planning/需求文档.md) | v1 全部 88 个需求 + v2 需求 + 范围外清单 |
| [路线图](.planning/路线图.md) | 4 个 Phase 规划、时间线、依赖关系、风险缓冲 |
| [规划文档](.planning/规划文档.md) | 详细架构设计、数据库表设计、API 规范、前端路由 |
| [项目状态](.planning/项目状态.md) | 当前进度、活跃任务、阻塞项 |
| [检查清单](.planning/检查清单.md) | 各 Phase 验收检查项 |
| [测试用例](.planning/测试用例.md) | 测试策略、场景清单 |
| [经验教训](.planning/经验教训.md) | 开发过程中的经验总结 |
| [方法论](.planning/方法论.md) | 开发流程、协作规范 |
| [操作手册](操作手册.md) | 部署、配置、API 使用、故障排查、Docker 一键部署 |
| [Docker 部署](操作手册.md#11-docker-部署) | 单容器架构、deploy.sh、上层 nginx 代理配置 |
| [开发记录](docs/开发记录-2026-05-02.md) | 每日开发会话记录 |

## 评审与审计

| 文档 | 说明 |
|------|------|
| [代码评审](backend/代码评审.md) | 第一轮代码评审 |
| [代码评审-最终](backend/代码评审-最终.md) | 最终代码评审（19 项发现） |
| [完整性审计](backend/完整性审计.md) | 功能完成度对比（45% 完成） |
| [测试缺口](backend/测试缺口.md) | 294 个缺失测试用例 |
| [架构评审](架构评审.md) | 第一轮架构评审 |
| [架构评审-最新](架构评审-最新.md) | 最新架构评审 |
| [前端评审](前端评审.md) | 第一轮前端评审 |
| [前端评审-最新](前端评审-最新.md) | 最新前端评审 |
| [安全审计](安全审计.md) | 第一轮安全审计 |
| [安全审计-最新](安全审计-最新.md) | 最新安全审计 |

---

## 文档关系图

```
项目概览 (.planning/项目概览.md)
  │
  ├── 需求文档 (.planning/需求文档.md)          ← 所有开发的依据
  │     │
  │     └── 路线图 (.planning/路线图.md)        ← 需求 → Phase 映射
  │           │
  │           └── 规划文档 (.planning/规划文档.md) ← 技术设计方案
  │                 │
  │                 ├── Phase 1~4 计划 (.planning/phases/)
  │                 └── 检查清单 (.planning/检查清单.md)
  │
  ├── 项目状态 (.planning/项目状态.md)          ← 实时进度追踪
  │     └── 经验教训 (.planning/经验教训.md)     ← 历史经验
  │
  ├── 操作手册 (操作手册.md)                    ← 部署和使用指南
  │     └── 开发记录 (docs/开发记录-*.md)       ← 每日开发日志
  │
  └── 评审与审计/
        ├── 代码评审-最终 (backend/代码评审-最终.md)  ← 代码质量
        ├── 完整性审计 (backend/完整性审计.md)         ← 需求覆盖度
        ├── 测试缺口 (backend/测试缺口.md)             ← 测试覆盖度
        ├── 架构评审-最新 (架构评审-最新.md)           ← 架构质量
        ├── 前端评审-最新 (前端评审-最新.md)           ← 前端质量
        └── 安全审计-最新 (安全审计-最新.md)           ← 安全合规
```

---

## 需求追溯矩阵

需求 → 路线图 → 计划 → 代码 → 测试 → 评审

| 需求编号 | 路线图 Phase | 计划文件 | 代码文件 | 测试文件 | 评审状态 |
|---------|-------------|---------|---------|---------|---------|
| AUTH-01~08 | Phase 1 | 01-02-PLAN.md | app/api/auth.py | test_auth.py | 通过 |
| TENANT-02 | Phase 1 | 01-03-PLAN.md | app/services/datasource_service.py | test_datasource_query.py | 通过 |
| DS-01, DS-05~07 | Phase 1 | 01-03-PLAN.md | app/api/datasource.py | test_datasource_query.py | 通过 |
| DSO-01~03, DSO-09 | Phase 1 | 01-03-PLAN.md | app/services/connection_pool.py | test_datasource_query.py | 通过 |
| META-01 | Phase 1 | 01-03-PLAN.md | app/db/models.py | test_phase2_extra.py | 通过 |
| SQL-01, 02, 07, 08 | Phase 1 | 01-04-PLAN.md | app/ai/graph.py, nodes/* | test_datasource_query.py | 通过 |
| CHART-02 | Phase 1 | 01-04-PLAN.md | app/ai/chart_type.py | test_phase2.py | 通过 |
| SEC-02 | Phase 1 | 01-04-PLAN.md | app/ai/nodes/execution.py | test_unit.py | 通过 |
| API-01, API-03 | Phase 1 | 01-04-PLAN.md | app/api/* | test_additional.py | 通过 |
| UX-11 | Phase 1 | 01-05-PLAN.md | frontend/src/components/ | test_e2e.py | 通过 |
| DS-02 | Phase 2 | — | app/services/connection_pool.py | test_phase2.py | 通过 |
| SQL-03~06 | Phase 2 | — | app/ai/nodes/*, rag_schema_service.py | test_phase2.py | 部分通过 |
| CHART-01, 03~08 | Phase 2 | — | app/ai/chart_type.py, ChartRenderer.vue | test_phase2.py | 通过 |
| API-02, API-05 | Phase 2 | — | app/api/query.py, export.py | test_phase2.py | 通过 |
| UX-01~05, 08, 09 | Phase 2 | — | frontend/src/views/* | test_e2e_comprehensive.py | 部分通过 |
| OPS-01, OPS-05 | Phase 2 | — | app/core/rate_limiter.py, analytics_service.py | test_comprehensive.py | 部分通过 |

---

*最后更新: 2026-05-02*
