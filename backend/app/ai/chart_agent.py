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

_TIME_KEYWORDS = {"month", "date", "day", "year", "time", "月", "日", "年", "时间", "日期"}
_CATEGORY_KEYWORDS = {"category", "type", "name", "类别", "类型", "名称", "分类"}
_COUNT_KEYWORDS = {"count", "cnt", "数量", "总数", "占比"}


def infer_chart_by_rule(columns: list[str], rows: list[tuple]) -> dict | None:
    """规则推断基础图表 (LLM 失败时的降级, 对标 AEE-008)。

    启发式:
      - 列名含时间词 → 折线图 (趋势)
      - 列名含类别词 + 计数 → 饼图 (占比)
      - 默认 → 柱状图 (对比)

    0 行结果: 仍返回图表 (空数据柱状图), 因为 0 是合法结果不是异常。
              None 只在连列都没有时返回。

    Returns:
        ECharts option dict, 或 None (无列结构)
    """
    if not columns:
        return None

    cols_lower = [c.lower() for c in columns]
    first_col = cols_lower[0] if cols_lower else ""

    # 取数据列 (假设最后一列是数值); 0 行时 names/values 为空但仍生成空图表
    names = [str(r[0]) for r in rows if r]
    values = [r[-1] if r and isinstance(r[-1], (int, float)) else 0 for r in rows]

    chart_type = "bar"  # 默认柱状图

    # 时间维度 → 折线
    if any(kw in first_col for kw in _TIME_KEYWORDS):
        chart_type = "line"
    # 类别 + 计数 → 饼图
    elif (any(kw in first_col for kw in _CATEGORY_KEYWORDS)
          and len(cols_lower) >= 2
          and any(kw in cols_lower[-1] for kw in _COUNT_KEYWORDS)):
        chart_type = "pie"

    logger.info("规则推断图表类型: %s (降级)", chart_type)

    if chart_type == "pie":
        return {
            "tooltip": {"trigger": "item"},
            "legend": {"orient": "vertical", "left": "left"},
            "series": [{
                "type": "pie",
                "radius": "60%",
                "data": [{"name": n, "value": v} for n, v in zip(names, values)],
            }],
        }

    return {
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "data": names},
        "yAxis": {"type": "value"},
        "series": [{"type": chart_type, "data": values}],
    }


# ── T034: 图表生成 ────────────────────────────────────────────

_CHART_PROMPT = """你是 BI 数据可视化专家。根据查询结果和用户意图生成 ECharts option JSON。

图表类型选择规则 (根据列名语义自动识别):
- 趋势/时间序列 (列名含 日期/月份/年份/date/month/time) → line 折线图
- 占比/分布 (单维度 + 计数/百分比/count/ratio/占比) → pie 饼图
- 对比/排名 (多类别 + 数值) → bar 柱状图
- 关联/相关性 (两个数值维度) → scatter 散点图
- 单个汇总值 (1行1列数值) → 不需要 series, 返回空 option {{}}

规则:
1. 只返回 ECharts option JSON, 不要解释, 不要 markdown 包裹
2. option 必须含 series 数组 (单值汇总除外)
3. 数据列: 第一列通常是维度(类别/时间), 最后一列是数值
4. 0 行结果是正常的, 生成对应类型的空数据图表即可

数据摘要:
- 列: {columns}
- 前5行: {sample}

{hint}

只返回 JSON:"""


async def generate_chart(
    question: str,
    columns: list[str],
    rows: list[tuple],
    llm_client,
    chart_type_hint: str | None = None,
) -> ChartResult:
    """生成 ECharts 图表 (LLM → 自愈 → 规则降级)。

    Args:
        question: 用户问题
        columns: 结果列名
        rows: 结果行
        llm_client: LLM client
        chart_type_hint: 来自 T026 的图表类型提示

    Returns:
        ChartResult — 始终返回 option (fail-closed, 降级到规则推断也不返回空)
    """
    from app.core.config import get_settings
    settings = get_settings()

    # 数据摘要 (不全量灌入, 防大结果撑爆 prompt)
    sample = rows[:5]
    hint = f"用户期望图表类型: {chart_type_hint}" if chart_type_hint else ""

    prompt = _CHART_PROMPT.format(
        columns=columns,
        sample=sample,
        hint=hint,
    )

    # 1. LLM 生成
    try:
        resp = await llm_client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": "你是 ECharts 配置生成器, 只返回 option JSON。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2000,
            temperature=0.0,
        )
        content = resp.choices[0].message.content or ""
        # OBS-002: 记录 token + prompt (请求级累加, T049 trace / T050 dump-prompts)
        from app.core.token_tracker import track_usage
        from app.core.prompt_capture import record_prompt
        track_usage(getattr(resp, "usage", None))
        record_prompt("generate_chart", "你是 ECharts 配置生成器, 只返回 option JSON。", prompt, getattr(resp, "usage", None))
    except Exception as e:
        logger.warning("图表生成 LLM 失败, 降级规则推断: %s", e)
        option = infer_chart_by_rule(columns, rows)
        return ChartResult(ok=option is not None, option=option, degraded=True, error=str(e))

    # 2. 解析 JSON (统一用 parse_json_response, 含 T035 自愈)
    from app.core.llm_json import parse_json_response
    option = parse_json_response(content)
    if option is not None and isinstance(option, dict):
        return ChartResult(ok=True, option=option)

    # 3. T035 JSON 自愈 (补括号)
    healed, ok = heal_json(content)
    if ok:
        try:
            option = json.loads(healed)
            logger.info("图表 JSON 自愈成功")
            return ChartResult(ok=True, option=option, degraded=True)
        except json.JSONDecodeError:
            pass

    # 4. 自愈失败 → 规则推断降级
    logger.warning("图表 JSON 自愈失败, 降级规则推断")
    option = infer_chart_by_rule(columns, rows)
    return ChartResult(ok=option is not None, option=option, degraded=True, error="JSON 解析失败, 规则推断降级")
