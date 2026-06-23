# Intent Classification

## Status: ADDED

## 概述

对标 Claude Code 的 5 种意图分流 + Pydantic 强约束输出 + confidence 降级。v1 只有 DataQuery/Other 二分类，v2 支持 5 种意图。

## 需求

### INT-001: 5 种意图分类

| 意图 | 示例 | 处理链路 |
|------|------|---------|
| `TEXT_TO_SQL` | "本月销售额" | 走完整 SQL 生成链路 |
| `CLARIFICATION` | "那个呢" | 需要上下文补全或追问用户 |
| `GENERAL` | "你好 / 你能做什么" | 通用对话，不查数据库 |
| `CHART_MODIFY` | "换成饼图" | 只改图表类型，不改 SQL |
| `EXPLANATION` | "这个 SQL 什么意思" | 解释已有 SQL 或结果 |

**验收标准**：
- [ ] "本月各品类销售额" → TEXT_TO_SQL
- [ ] "那个呢"（无上下文时）→ CLARIFICATION → 追问 "你指的是？"
- [ ] "那个呢"（上文有"本月销售额"）→ TEXT_TO_SQL（关联上文）
- [ ] "你好" → GENERAL
- [ ] "换成饼图" → CHART_MODIFY → 只改图表不改 SQL
- [ ] "这个 SQL 什么意思" → EXPLANATION → 解释

### INT-002: Pydantic 强约束输出

**对标 Claude Code**：结构化输出 + Pydantic BaseModel（海泰 IntentClassifierOutput）

LLM 必须输出结构化 JSON：
```python
class IntentOutput(BaseModel):
    intent: Literal["TEXT_TO_SQL", "CLARIFICATION", "GENERAL", "CHART_MODIFY", "EXPLANATION"]
    normalized_question: str     # 标准化后的查询问题
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str                  # 分类理由
    chart_type_hint: str | None  # 用户是否提到了图表类型偏好
```

- confidence < 0.6 → 自动降级为 CLARIFICATION（追问用户）
- LLM 输出不符合 schema → 重试（最多 2 次）

**验收标准**：
- [ ] intent=CLARIFICATION, confidence=0.3 → 自动追问 "请问你指的是什么？"
- [ ] "帮我看看上个月的订单情况，用柱状图" → normalized_question="上月订单统计", chart_type_hint="bar"

### INT-003: 追问上下文补全 + 维度继承

**对标 Claude Code**：多轮对话 history 完整传递
**对标海泰**：Leader prompt 第13-14条 — 相对时间展开 + 维度继承与合并

追问时：
- 从 State Store 读取上轮查询的上下文（当前表、SQL、筛选条件）
- 从对话历史读取上一轮的用户问题和 Agent 回复
- 结合新问题 → LLM 生成完整意图

**海泰相对时间展开规则**（对标海泰系统提示词第13条）：
- LLM 负责日历推算（"上个月"→ 基于锚点时间做日历推算），不靠正则匹配
- 示例：锚点="2024年11月" → 用户问"上个月的数据" → normalized_question="2024年10月"

**海泰维度继承规则**（对标海泰系统提示词第14条）：
- 本轮明确说的维度以本轮为准（覆盖锚点同维度）
- 本轮未提到的维度从锚点继承
- 示例：锚点="产科2024年11月处方合格率" → 用户问"外科呢" → "外科2024年11月处方合格率"

**验收标准**：
- [ ] 单独问"按品类拆分"→ CLARIFICATION（不知道拆分什么）
- [ ] 上轮问"本月销售额"后再问"按品类拆分"→ TEXT_TO_SQL（"本月各品类销售额"）
- [ ] 上轮问"2024年11月产科处方合格率" → 追问"上个月呢" → normalized="2024年10月产科处方合格率"
- [ ] 上轮问"2024年11月产科处方合格率" → 追问"外科呢" → normalized="2024年11月外科处方合格率"

### INT-004: normalized_question 剥离可视化措辞（对标海泰意图识别第10条）

**对标海泰**：剥离"用折线图展示"、"画成饼图"等纯可视化指令 → 因为它们与 Schema 检索（向量匹配）无关，保留会造成语义噪音

- 剥离：图表类型指定、展示格式要求
- 保留：所有业务筛选条件（科室、时间、指标名称等）
- chart_type_hint 单独提取 → 供 Chart Agent 使用

示例：用户输入"用饼状图展示部门名称为产科在2024年11月每位医生的门诊处方合格率" → normalized_question="部门名称为产科在2024年11月每位医生的门诊处方合格率", chart_type_hint="pie"