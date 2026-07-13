## 图表选择引擎改进 — KPI 指标卡 + 表格降级

### 问题

V2 当前图表选择比 V1 差在：
1. **单值查询**（如"总销售额"）→ 返回空 `{}`，SSE 路径不发送 chart 事件，前端无任何可视化
2. **默认降级** → 硬塞柱状图，不适合图表的数据也画柱状图
3. **缺少表格模式** → 单列多行等场景没有表格展示

### 改动

#### 1. 后端 `chart_agent.py` — 新增 KPI 和 TABLE 图表类型

**`inject_data` 新增 `kpi` 类型**：
- 返回 ECharts gauge 无指针模式（大数字居中显示 + 列名作标题）
- 前端 `setOption` 直接支持，无需额外组件

**`infer_chart_by_rule` 修改决策树**（对齐 V1 规划）：
1. 单值 (1行1列数值) → **kpi** 指标卡
2. 单列多行 / 维度唯一值 > 50 / 全文本列 → **table**
3. 时间维度 → line
4. 占比/分布 ≤10 → pie
5. 默认 → bar

**`_CHART_PROMPT` 更新**：新增 kpi 和 table 类型说明

**`analyze_data_shape` 新增 `table_recommended` 标志**：维度唯一值 > 50、全文本列等场景

#### 2. 后端 `chat_stream.py` — 修复空 option 不发送 chart 事件

`if state.chart_option:` → `if state.chart_option is not None:`，让 KPI gauge option 正常发送

#### 3. 后端 `dashboard.py` — 处理 kpi/table 类型

- `chart_type="kpi"` 时 `inject_data` 正常返回 gauge option ✅（已兼容）
- `chart_type="table"` 时跳过 `inject_data`，直接返回 None → 看板走表格渲染 ✅（已兼容）

#### 4. 前端 `ChatView.vue` — 支持 KPI 和 TABLE 渲染

- **KPI**：ECharts gauge 无指针模式，`setOption` 直接支持，无需额外组件
- **TABLE**：当 `msg.chart.chart_type === "table"` 时，不调 ECharts，渲染 HTML 表格（复用已有样式）
- chart 容器判断调整，避免空图表

#### 5. 前端 `DashboardView.vue` — 无需调整

看板已有 fallback：`chartOpt.series` → ECharts，否则 → HTML 表格。KPI gauge 有 series 自动走 ECharts，table 无 series 自动走表格。

### 测试

- 跑 `.venv/bin/python -m pytest backend/tests/ -q` 确认不破坏
- 手动验证："总销售额" → KPI 指标卡；不适合图表 → 表格