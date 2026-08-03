"""
LLM JSON 响应解析 — 统一处理 markdown 包裹/前后多余文字

对标:
  - 反思发现: 所有调 LLM 返回 JSON 的地方 (intent/retriever/thinking/chart/healer)
    都直接 json.loads, 没处理 markdown 包裹 → 偶发"非法JSON"降级
  - LLM 常见输出格式:
    1. ```json\n{...}\n```  (markdown 包裹)
    2. 好的, 结果是:\n{...}  (前后多余文字)
    3. {...}  (纯 JSON, 正常)

策略 (剥洋葱):
  1. 去 markdown 代码块包裹
  2. 提取第一个 { 到最后一个 } 的子串 (JSON 对象)
  3. 提取第一个 [ 到最后一个 ] 的子串 (JSON 数组)
  4. 都失败 → 返回 None (调用方降级)

为什么剥洋葱而非正则:
  - 正则很难处理嵌套 JSON 的边界 (如字符串内部含 {} 或 [])
  - 按层次"先尝试, 再降级"更可靠
  - 先尝试直接解析 (纯 JSON 场景最快), 再逐步降级

应用场景:
  - intent: 解析 LLM 返回的意图识别 JSON
  - retriever: 解析 LLM 返回的表选择 JSON
  - thinking: 解析 LLM 返回的思考过程 JSON
  - chart: 解析 LLM 返回的图表配置 JSON
  - healer: 解析 LLM 返回的 SQL 修复建议 JSON
"""
from __future__ import annotations

import json
import re
from typing import Any


def parse_json_response(content: str | None) -> Any | None:
    """从 LLM 响应里提取 JSON (容错 markdown 包裹/前后文字)。

    Args:
        content: LLM 原始返回文本

    Returns:
        解析后的 Python 对象 (dict/list), 或 None (无法解析)

    解析策略 (剥洋葱):
      1. 直接 json.loads (纯 JSON, 最快路径)
      2. 去 markdown 代码块包裹后解析 (```json ... ```)
      3. 提取第一个 { 到最后一个 } 的 JSON 对象
      4. 提取第一个 [ 到最后一个 ] 的 JSON 数组
      5. 全部失败 → 返回 None (调用方降级)

    为什么返回 None 而非抛异常:
      - 调用方有降级策略 (如重试、使用默认值)
      - None 比异常更容易处理链式降级

    注意: 步骤 3 和 4 提取的是最外层 { } 或 [ ] 区间,
    如果字符串内部包含 { } 或 [ ] (如 JSON 字符串值), 仍能正确解析。
    但如果 LLM 在 JSON 外部也用了 { } (如说"结果是{...}"), 可能提取错误。
    这种情况极少见, 目前未做特殊处理。
    """
    if not content:
        return None

    text = content.strip()

    # 1. 先尝试直接解析 (纯 JSON)
    # 这是最常见的场景, 且是最快的路径
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. 去 markdown 代码块包裹 (```json ... ``` 或 ``` ... ```)
    # LLM 经常用 markdown 代码块包裹 JSON 输出
    # 注意: 同时支持 ```json 和 ``` 两种前缀
    text = re.sub(r"^```(?:json|JSON)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. 提取 JSON 对象 (第一个 { 到最后一个 })
    # 用于 LLM 在 JSON 前后加了文字描述的场景
    # re.DOTALL 让 . 匹配换行符, 支持跨行 JSON
    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except json.JSONDecodeError:
            pass

    # 4. 提取 JSON 数组 (第一个 [ 到最后一个 ])
    # 少数 LLM 返回 JSON 数组的场景
    arr_match = re.search(r"\[.*\]", text, re.DOTALL)
    if arr_match:
        try:
            return json.loads(arr_match.group(0))
        except json.JSONDecodeError:
            pass

    # 5. 全部失败 → 返回 None
    return None
