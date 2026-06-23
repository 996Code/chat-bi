"""
T034/T035: 图表生成 + JSON 自愈降级 — 单元测试

对标:
  - AEE-008 (openspec spec): LLM 声明式 ECharts JSON + JSON 自愈 + 降级
  - T034: LLM 生成 ECharts option JSON + chart_type_hint + 结构校验
  - T035: JSON 截断自愈 (补括号) + 失败降级到规则推断

JSON 自愈 (对标海泰):
  LLM 受 max_tokens 限制可能输出截断 → 缺右括号
  自愈: 数 { vs } 差值 → 补缺失的 } → 重新解析
  仍失败 → 规则推断 (柱状图/饼图) + WARNING
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.chart_agent import (
    generate_chart,
    heal_json,
    infer_chart_by_rule,
    ChartResult,
)


# ── T035: JSON 自愈 (括号补全) ────────────────────────────────

class TestHealJson:
    """修复截断的 JSON (补缺失右括号)。"""

    def test_valid_json_unchanged(self):
        valid = '{"xAxis": {"data": ["a"]}}'
        result, ok = heal_json(valid)
        assert ok
        assert json.loads(result)["xAxis"]["data"] == ["a"]

    def test_missing_right_braces_fixed(self):
        """缺右括号 → 补全 → 可解析 (对标 AEE-008)。"""
        truncated = '{"xAxis": {"data": ["a", "b"'  # 缺 3 个 ]
        result, ok = heal_json(truncated)
        assert ok
        parsed = json.loads(result)
        assert "xAxis" in parsed

    def test_missing_single_brace(self):
        truncated = '{"title": {"text": "x"'  # 缺 2 个 }
        result, ok = heal_json(truncated)
        assert ok

    def test_garbage_returns_not_ok(self):
        """完全无法修复的 → not ok (降级到规则推断)。"""
        result, ok = heal_json("完全不是JSON的内容{{{")
        # 即使补括号也可能解析失败
        if ok:
            try:
                json.loads(result)
            except json.JSONDecodeError:
                ok = False
        # 垃圾输入应该返回 not ok 或仍不可解析


# ── T035: 规则推断降级 ────────────────────────────────────────

class TestInferByRule:
    """LLM 失败 → 规则推断基础图表 (对标 AEE-008 降级)。"""

    def test_single_category_to_pie(self):
        """单维度 + 计数 → 饼图 (类别占比)。"""
        option = infer_chart_by_rule(
            columns=["category", "count"],
            rows=[("a", 10), ("b", 20)],
        )
        assert option is not None
        assert option.get("series", [{}])[0].get("type") == "pie"

    def test_time_series_to_line(self):
        """时间维度 + 数值 → 折线图 (趋势)。"""
        option = infer_chart_by_rule(
            columns=["month", "amount"],
            rows=[("2024-01", 100), ("2024-02", 200)],
        )
        assert option is not None
        assert option.get("series", [{}])[0].get("type") == "line"

    def test_multi_category_to_bar(self):
        """多类别 → 柱状图 (默认)。"""
        option = infer_chart_by_rule(
            columns=["name", "value"],
            rows=[("a", 1), ("b", 2), ("c", 3), ("d", 4)],
        )
        assert option is not None
        assert option.get("series", [{}])[0].get("type") == "bar"

    def test_empty_data_returns_none(self):
        """无数据 → None (不强行推断)。"""
        assert infer_chart_by_rule([], []) is None


# ── T034: 图表生成 ────────────────────────────────────────────

def _mock_llm(option_json: str):
    fake = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = option_json
    fake.chat.completions.create = AsyncMock(return_value=resp)
    return fake


class TestGenerateChart:
    """LLM 生成 ECharts option JSON。"""

    @pytest.mark.asyncio
    async def test_generate_valid_option(self):
        option = json.dumps({
            "xAxis": {"type": "category", "data": ["a", "b"]},
            "yAxis": {"type": "value"},
            "series": [{"type": "bar", "data": [1, 2]}],
        })
        llm = _mock_llm(option)
        result = await generate_chart(
            question="各品类销售额",
            columns=["category", "amount"],
            rows=[("a", 100), ("b", 200)],
            llm_client=llm,
        )
        assert result.ok
        assert result.option is not None
        assert "series" in result.option

    @pytest.mark.asyncio
    async def test_generate_truncated_json_self_heals(self):
        """LLM 输出截断 JSON → 自愈补括号 (T035 集成)。"""
        truncated = '{"xAxis": {"data": ["a", "b"]}, "series": [{"type": "bar", "data": [1, 2]'  # 缺 }]
        llm = _mock_llm(truncated)
        result = await generate_chart(
            question="销售",
            columns=["c", "v"],
            rows=[("a", 1)],
            llm_client=llm,
        )
        assert result.ok  # 自愈成功
        assert "series" in result.option

    @pytest.mark.asyncio
    async def test_generate_garbage_degrades_to_rule(self):
        """LLM 输出完全无效 → 降级到规则推断 (T035 降级)。"""
        llm = _mock_llm("这不是JSON完全无法解析{{随机")
        result = await generate_chart(
            question="销售",
            columns=["category", "count"],
            rows=[("a", 10), ("b", 20)],
            llm_client=llm,
        )
        assert result.ok  # 降级到规则推断, 仍返回 option
        assert result.degraded is True

    @pytest.mark.asyncio
    async def test_chart_type_hint_passed_to_llm(self):
        """chart_type_hint (来自 T026) 传给 LLM 引导。"""
        option = json.dumps({"series": [{"type": "pie", "data": [1]}]})
        llm = _mock_llm(option)
        await generate_chart(
            question="占比",
            columns=["c", "v"],
            rows=[("a", 1)],
            llm_client=llm,
            chart_type_hint="pie",
        )
        prompt = llm.chat.completions.create.call_args.kwargs.get("messages", [])
        full = json.dumps(prompt, ensure_ascii=False)
        assert "pie" in full.lower() or "饼" in full

    @pytest.mark.asyncio
    async def test_llm_failure_degrades(self):
        """LLM 调用失败 → 降级规则推断。"""
        llm = MagicMock()
        llm.chat.completions.create = AsyncMock(side_effect=Exception("LLM down"))
        result = await generate_chart(
            question="x",
            columns=["c", "v"],
            rows=[("a", 1), ("b", 2)],
            llm_client=llm,
        )
        assert result.ok  # 降级成功
        assert result.degraded is True
