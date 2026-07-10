"""
T034/T035: 图表生成 + JSON 自愈降级

对标:
  - AEE-008 (openspec spec): LLM 声明式 ECharts option JSON + JSON 自愈 + 降级
  - 海泰 visualization_agent: LLM 生成 ECharts, 截断自愈

T034 图表生成:
  - generate_chart: LLM 生成 ECharts option JSON + chart_type_hint 引导
  - 结构校验 (必须有 series)

T035 JSON 自愈 + 降级:
  - heal_json: LLM 输出截断 → 补缺失右括号 → 重新解析 (对标 AEE-008)
  - infer_chart_by_rule: 自愈失败 → 规则推断 (饼/线/柱) + WARNING

降级链 (fail-closed, 不返回空):
  LLM JSON → heal_json 自愈 → 规则推断 → 总有 option 返回
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ChartResult:
    """图表生成结果。"""
    ok: bool = False
    option: dict | None = None
    degraded: bool = False  # 是否降级 (自愈/规则推断)
    error: str | None = None
    config: dict | None = None  # LLM 返回的图表配置 {chart_type, dim_col, measure_cols} (供缓存复用)


# ── T035: JSON 自愈 (补缺失右括号) ────────────────────────────

def heal_json(text: str) -> tuple[str, bool]:
    """修复截断的 JSON (补缺失的 ] 和 })。

    LLM 受 max_tokens 限制输出可能截断, 最常见是缺右括号。
    策略 (对标 AEE-008):
      1. 先尝试原样解析
      2. 失败 → 数 [ ]、{ } 的差值 → 补缺失的右括号
      3. 重新解析, 成功返回

    Returns:
        (修复后的文本, 是否成功可解析)
    """
    if not text:
        return text, False

    text = text.strip()
    # 1. 先尝试原样
    try:
        json.loads(text)
        return text, True
    except json.JSONDecodeError:
        pass

    # 2. 截取到最后一个看起来完整的位置 (去掉尾部不完整的 token)
    # 找最后一个完整的值/括号结束位置
    cleaned = text
    # 去掉尾部可能的不完整字符串值 (引号未闭合)
    # 简单策略: 从后向前找最后一个 , 或 ] 或 } 或 " 闭合点
    last_valid = max(
        cleaned.rfind(","),
        cleaned.rfind("]"),
        cleaned.rfind("}"),
        cleaned.rfind('"'),
    )
    if last_valid > 0 and last_valid < len(cleaned) - 1:
        cleaned = cleaned[: last_valid + 1]

    # 3. 数括号差值, 补缺失的右括号
    open_square = cleaned.count("[")
    close_square = cleaned.count("]")
    open_brace = cleaned.count("{")
    close_brace = cleaned.count("}")

    # 先补 ] 再补 }
    healed = cleaned + ("]" * (open_square - close_square)) + ("}" * (open_brace - close_brace))

    # 4. 重新解析
    try:
        json.loads(healed)
        logger.info("JSON 自愈成功 (补了 %d 个括号)",
                    (open_square - close_square) + (open_brace - close_brace))
        return healed, True
    except json.JSONDecodeError:
        # 再尝试去掉尾部不完整逗号后补括号
        healed2 = re.sub(r",\s*$", "", healed)
        healed2 = healed2 + ("]" * max(0, open_square - healed2.count("]"))) + ("}" * max(0, open_brace - healed2.count("}")))
        try:
            json.loads(healed2)
            return healed2, True
        except json.JSONDecodeError:
            return healed, False


# ── T035: 规则推断降级 ────────────────────────────────────────

_TIME_KEYWORDS = frozenset({
    # 英文
    "month", "date", "day", "year", "time", "quarter", "week",
    "hour", "minute", "timestamp", "datetime", "period",
    # 中文
    "月", "日", "年", "时间", "日期", "季度", "周", "小时", "时",
})

# 饼图最大切片数 (超过此数饼图不再可读, 对标 BI 可视化最佳实践)
_PIE_MAX_SLICES = 10
# 数值列判定阈值: 列中非 None 值有 >= 此比例的数值型, 才视为数值列
_NUMERIC_COL_THRESHOLD = 0.8
# 数据缩放条阈值: 维度值超过此数时显示 dataZoom
_DATAZOOM_THRESHOLD = 20


def _to_float(v) -> float | None:
    """数值类型 (int/float/Decimal) → float, 其他返回 None。

    PG 的 SUM/COUNT 返回 Decimal, 必须显式处理,
    否则 isinstance(Decimal, (int, float)) 为 False → 全部变 0。
    bool 是 int 子类, 必须在 int 前排除。
    """
    from decimal import Decimal
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    return None


def analyze_data_shape(columns: list[str], rows: list[tuple]) -> dict:
    """分析数据特征, 为图表类型选择提供依据。

    自动识别维度列和数值列 (而非假设第一列=维度):
      - 维度列: 非数值型, 或唯一值数远小于行数 (低基数)
      - 数值列: 值以数值为主 (_NUMERIC_COL_THRESHOLD)

    Returns:
        描述数据特征的 dict, 含人类可读 summary 字段。
        col_count == 0 时所有字段返回安全默认值。
    """
    from decimal import Decimal

    row_count = len(rows)
    col_count = len(columns)

    # 安全默认值: col_count == 0 时避免 KeyError
    empty_shape: dict = {
        "row_count": row_count,
        "col_count": 0,
        "dim_unique_count": 0,
        "numeric_col_count": 0,
        "numeric_cols": [],
        "dim_col": None,
        "first_col_is_time": False,
        "has_ratio_col": False,
        "single_value": False,
        "summary": "0行0列",
    }
    if col_count == 0:
        return empty_shape

    # 每列统计: 唯一值数 + 数值占比
    col_stats: list[dict] = []
    for ci, col in enumerate(columns):
        unique_vals: set[str] = set()
        numeric_count = 0
        non_none = 0
        for r in rows:
            if r and ci < len(r) and r[ci] is not None:
                non_none += 1
                v = r[ci]
                unique_vals.add(str(v))
                if isinstance(v, bool):
                    pass
                elif isinstance(v, (int, float, Decimal)):
                    numeric_count += 1
        is_numeric = non_none > 0 and numeric_count / non_none >= _NUMERIC_COL_THRESHOLD
        col_stats.append({
            "name": col,
            "unique_count": len(unique_vals),
            "is_numeric": is_numeric,
            "non_none": non_none,
        })

    # 识别数值列
    numeric_cols = [s["name"] for s in col_stats if s["is_numeric"]]

    # 识别维度列: 第一个非数值列, 若全为数值列则取唯一值最少 (基数最低) 的列
    dim_col: str | None = None
    non_numeric_stats = [s for s in col_stats if not s["is_numeric"]]
    if non_numeric_stats:
        dim_col = non_numeric_stats[0]["name"]
    elif col_stats:
        dim_col = min(col_stats, key=lambda s: s["unique_count"])["name"]

    # 维度列唯一值数
    dim_unique_count = 0
    if dim_col:
        dim_stat = next((s for s in col_stats if s["name"] == dim_col), None)
        if dim_stat:
            dim_unique_count = dim_stat["unique_count"]

    # 时间语义检测
    dim_col_lower = (dim_col or "").lower()
    first_col_is_time = dim_col is not None and any(kw in dim_col_lower for kw in _TIME_KEYWORDS)

    # 占比/百分比列检测
    ratio_keywords = {"ratio", "percent", "pct", "占比", "百分比", "率", "rate", "share", "proportion"}
    has_ratio_col = any(
        any(kw in col.lower() for kw in ratio_keywords)
        for col in numeric_cols
    )

    # 单值汇总: 1行 + 最多1个数值列
    single_value = row_count == 1 and len(numeric_cols) <= 1

    shape = {
        "row_count": row_count,
        "col_count": col_count,
        "dim_unique_count": dim_unique_count,
        "numeric_col_count": len(numeric_cols),
        "numeric_cols": numeric_cols,
        "dim_col": dim_col,
        "first_col_is_time": first_col_is_time,
        "has_ratio_col": has_ratio_col,
        "single_value": single_value,
    }

    # 人类可读摘要 (供 LLM prompt 使用)
    parts = [f"{row_count}行"]
    if dim_col and dim_unique_count:
        parts.append(f"维度列'{dim_col}'有{dim_unique_count}个唯一值")
    if numeric_cols:
        parts.append(f"{len(numeric_cols)}个数值列")
    if first_col_is_time:
        parts.append("维度列含时间语义")
    if has_ratio_col:
        parts.append("含占比列")
    shape["summary"] = ", ".join(parts)

    return shape


def infer_chart_by_rule(columns: list[str], rows: list[tuple]) -> dict | None:
    """规则推断基础图表 (LLM 失败时的降级, 对标 AEE-008)。

    基于数据特征推断 (而非硬编码列名关键词匹配):
      - 单值汇总 (1行1列) → 空图表
      - 维度列含时间语义 → 折线图 (趋势)
      - 占比列 + 维度唯一值 ≤ _PIE_MAX_SLICES → 饼图
      - 维度唯一值 ≤ _PIE_MAX_SLICES + 单数值列 → 饼图 (分布场景)
      - 默认 → 柱状图

    0 行结果: 仍返回图表 (空数据柱状图), 因为 0 是合法结果不是异常。
              None 只在连列都没有时返回。

    Returns:
        ECharts option dict, 或 None (无列结构)
    """
    if not columns:
        return None

    # 分析数据特征
    shape = analyze_data_shape(columns, rows)

    # 单值汇总 → 空图表
    if shape["single_value"]:
        return {}

    # 维度列 → 列索引 (自动识别, 不假设第一列)
    dim_col = shape.get("dim_col") or columns[0]
    col_index = {c: i for i, c in enumerate(columns)}
    dim_idx = col_index.get(dim_col, 0)

    # 数值列索引 (自动识别, 不假设最后一列)
    numeric_cols = shape.get("numeric_cols", [])
    if not numeric_cols and len(columns) >= 2:
        numeric_cols = [columns[-1]]
    measure_indices = [col_index[c] for c in numeric_cols if c in col_index]
    if not measure_indices and len(columns) >= 2:
        measure_indices = [len(columns) - 1]

    # 取维度名称和数值
    names = [str(r[dim_idx]) if r and len(r) > dim_idx else "" for r in rows]
    values = [_to_float(r[measure_indices[0]]) if r and len(r) > measure_indices[0] else None for r in rows]
    values = [v if v is not None else 0 for v in values]

    chart_type = "bar"  # 默认柱状图

    # 时间维度 → 折线图
    if shape["first_col_is_time"]:
        chart_type = "line"
    # 占比/百分比列 → 饼图 (明确的分布语义, 允许更多切片)
    elif shape["has_ratio_col"] and shape["dim_unique_count"] <= _PIE_MAX_SLICES:
        chart_type = "pie"
    # 维度唯一值 ≤ _PIE_MAX_SLICES + 单数值列 → 饼图 (分布场景)
    elif shape["dim_unique_count"] <= _PIE_MAX_SLICES and shape["numeric_col_count"] == 1 and shape["row_count"] > 1:
        chart_type = "pie"

    logger.info("规则推断图表类型: %s (数据特征: %s)", chart_type, shape["summary"])

    if chart_type == "pie":
        return {
            "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
            "legend": {"bottom": 0, "type": "scroll"},
            "series": [{
                "type": "pie",
                "radius": ["35%", "65%"],
                "label": {"show": True, "formatter": "{b}: {d}%"},
                "data": [{"name": n, "value": v} for n, v in zip(names, values)],
            }],
        }

    # bar/line: 支持多数值列 (多系列)
    series = []
    for m_idx in measure_indices:
        col_name = columns[m_idx] if m_idx < len(columns) else ""
        s_vals = [_to_float(r[m_idx]) if r and len(r) > m_idx else None for r in rows]
        s_vals = [v if v is not None else 0 for v in s_vals]
        series.append({"name": col_name, "type": chart_type, "data": s_vals})

    if not series:
        series = [{"type": chart_type, "data": values}]

    return {
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 0, "type": "scroll", "data": [s["name"] for s in series if s.get("name")]},
        "grid": {"left": "3%", "right": "4%", "bottom": "12%", "containLabel": True},
        "xAxis": {"type": "category", "data": names, "axisLabel": {"interval": 0, "rotate": 30 if names and len(str(names[0])) > 4 else 0}},
        "yAxis": {"type": "value"},
        "series": series,
        "dataZoom": [{"type": "slider", "start": 0, "end": 100}] if len(names) > _DATAZOOM_THRESHOLD else [],
    }


# ── T034: 图表生成 ────────────────────────────────────────────

_CHART_PROMPT = """你是 BI 数据可视化专家。根据查询结果的数据特征选择最合适的图表类型。

图表类型选择规则:
- 趋势/时间序列 (维度列含时间语义) → line 折线图
- 占比/分布 (维度唯一值 ≤ 10 + 单数值列, 如各省份销售额) → pie 饼图
- 对比/排名 (多类别 + 数值, 或多数值列) → bar 柱状图
- 关联/相关性 (两个数值维度) → scatter 散点图
- 单个汇总值 (1行1列数值) → 不需要 series, 返回空 option {{{{}}}}

重要: 你只负责选择图表类型和指定数据列映射, 不要自己填充数据!
数据将由系统根据你指定的列映射从完整结果集自动填充。

你必须返回如下结构的 JSON:
- 对于 line/bar/scatter 图:
  {{{{
    "chart_type": "bar|line|scatter",
    "dim_col": "维度列名 (通常是第一列, 类别/时间)",
    "measure_cols": ["数值列名1", "数值列名2"]
  }}}}
- 对于 pie 图:
  {{{{
    "chart_type": "pie",
    "dim_col": "类别列名",
    "measure_cols": ["单个数值列名"]
  }}}}
- 单值汇总 (1行1列):
  {{{{}}}}

规则:
1. 只返回 JSON, 不要解释, 不要 markdown 包裹
2. dim_col 通常是分类或时间维度列
3. measure_cols 是要展示的数值列 (可多个, 但 pie 只能1个)
4. 0 行结果正常返回结构即可

数据特征:
- 数据概况: {data_shape}
- 列: {columns}
- 前5行: {sample}

{hint}

只返回 JSON:"""


def inject_data(
    chart_config: dict,
    columns: list[str],
    rows: list[tuple],
) -> dict | None:
    """根据 LLM 指定的列映射, 从完整 rows 程序化构建 ECharts option。

    这样图表始终包含全量数据, 不受 LLM 只看 5 行摘要的限制。

    Args:
        chart_config: LLM 返回的结构 {chart_type, dim_col, measure_cols}
        columns: 完整结果列名
        rows: 完整结果行

    Returns:
        ECharts option dict, 或 None (配置无效)
    """
    if not chart_config or not columns:
        return None

    chart_type = chart_config.get("chart_type", "bar")
    dim_col = chart_config.get("dim_col")
    measure_cols = chart_config.get("measure_cols") or []

    # 空配置 (单值汇总) → 返回空 option
    if not chart_type or (chart_type == "bar" and not measure_cols and not dim_col):
        if not chart_config:
            return {}

    # 列名 → 索引 (容错: 列名找不到时回退到默认假设)
    col_index = {c: i for i, c in enumerate(columns)}
    dim_idx = col_index.get(dim_col, 0) if dim_col else 0
    measure_indices = [col_index[c] for c in measure_cols if c in col_index]
    # 回退: 如果 LLM 没指定好, 取最后一列做数值
    if not measure_indices and len(columns) >= 2:
        measure_indices = [len(columns) - 1]

    # 提取维度名称 (类别/时间)
    names = []
    for r in rows:
        if r and len(r) > dim_idx:
            names.append(str(r[dim_idx]) if r[dim_idx] is not None else "")
        else:
            names.append("")

    # 饼图: name + value
    if chart_type == "pie":
        if not measure_indices:
            return None
        val_idx = measure_indices[0]
        data = []
        for i, r in enumerate(rows):
            if r and len(r) > val_idx:
                v = _to_float(r[val_idx]) or 0
                data.append({"name": names[i] if i < len(names) else "", "value": v})
        return {
            "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
            "legend": {"bottom": 0, "type": "scroll"},
            "series": [{
                "type": "pie",
                "radius": ["35%", "65%"],
                "label": {"show": True, "formatter": "{b}: {d}%"},
                "data": data,
            }],
        }

    # bar/line/scatter: xAxis(category) + yAxis(value) + series
    series = []
    for m_idx in measure_indices:
        col_name = columns[m_idx] if m_idx < len(columns) else ""
        values = []
        for r in rows:
            if r and len(r) > m_idx:
                v = _to_float(r[m_idx])
                values.append(v if v is not None else 0)
            else:
                values.append(0)
        series.append({"name": col_name, "type": chart_type, "data": values})

    if not series:
        return None

    return {
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 0, "type": "scroll", "data": [s["name"] for s in series]},
        "grid": {"left": "3%", "right": "4%", "bottom": "12%", "containLabel": True},
        "xAxis": {"type": "category", "data": names, "axisLabel": {"interval": 0, "rotate": names and len(str(names[0])) > 4 and 30 or 0}},
        "yAxis": {"type": "value"},
        "series": series,
        "dataZoom": [{"type": "slider", "start": 0, "end": 100}] if len(names) > _DATAZOOM_THRESHOLD else [],
    }


async def generate_chart(
    question: str,
    columns: list[str],
    rows: list[tuple],
    chart_type_hint: str | None = None,
) -> ChartResult:
    """生成 ECharts 图表 (LLM → 自愈 → 规则降级)。

    Args:
        question: 用户问题
        columns: 结果列名
        rows: 结果行
        chart_type_hint: 来自 T026 的图表类型提示

    Returns:
        ChartResult — 始终返回 option (fail-closed, 降级到规则推断也不返回空)
    """
    from app.core.llm_client import llm_chat

    # 数据摘要 (不全量灌入, 防大结果撑爆 prompt)
    sample = rows[:5]
    shape = analyze_data_shape(columns, rows)
    hint = f"用户期望图表类型: {chart_type_hint}" if chart_type_hint else ""

    prompt = _CHART_PROMPT.format(
        columns=columns,
        sample=sample,
        data_shape=shape["summary"],
        hint=hint,
    )

    system_msg = "你是 BI 图表类型决策器, 只返回图表配置 JSON (chart_type/dim_col/measure_cols)。"
    # 1. LLM 生成 (只返回图表类型 + 列映射配置, 不填数据)
    try:
        content, _ = await llm_chat(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            node="generate_chart",
            temperature=0.0,
        )
    except Exception as e:
        logger.warning("图表生成 LLM 失败, 降级规则推断: %s", e)
        option = infer_chart_by_rule(columns, rows)
        return ChartResult(ok=option is not None, option=option, degraded=True, error=str(e))

    # 2. 解析 LLM 返回的图表配置 (chart_type + dim_col + measure_cols)
    #    统一用 parse_json_response, 含 T035 自愈
    from app.core.llm_json import parse_json_response
    chart_config = parse_json_response(content)

    # 2a. JSON 自愈 (补括号)
    if chart_config is None:
        healed, ok = heal_json(content)
        if ok:
            try:
                chart_config = json.loads(healed)
                logger.info("图表配置 JSON 自愈成功")
            except json.JSONDecodeError:
                pass

    # 3. 根据 LLM 配置 + 完整 rows 程序化构建 ECharts option (全量数据注入)
    if chart_config is not None and isinstance(chart_config, dict):
        option = inject_data(chart_config, columns, rows)
        if option is not None:
            # 返回时附上 config, 供调用方缓存 (下次直接 inject 不用重跑 LLM)
            return ChartResult(ok=True, option=option, config=chart_config)

    # 4. LLM 配置无效 → 规则推断降级 (用全量 rows, fail-closed)
    logger.warning("图表配置无效或数据注入失败, 降级规则推断")
    option = infer_chart_by_rule(columns, rows)
    return ChartResult(ok=option is not None, option=option, degraded=True, error="图表配置解析失败, 规则推断降级")
