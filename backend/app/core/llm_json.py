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
    """
    if not content:
        return None

    text = content.strip()

    # 1. 先尝试直接解析 (纯 JSON)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. 去 markdown 代码块包裹 (```json ... ``` 或 ``` ... ```)
    text = re.sub(r"^```(?:json|JSON)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. 提取 JSON 对象 (第一个 { 到最后一个 })
    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except json.JSONDecodeError:
            pass

    # 4. 提取 JSON 数组 (第一个 [ 到最后一个 ])
    arr_match = re.search(r"\[.*\]", text, re.DOTALL)
    if arr_match:
        try:
            return json.loads(arr_match.group(0))
        except json.JSONDecodeError:
            pass

    return None
