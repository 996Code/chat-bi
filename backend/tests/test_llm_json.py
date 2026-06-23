"""LLM JSON 解析工具测试"""
from __future__ import annotations

from app.core.llm_json import parse_json_response


class TestParseJsonResponse:
    def test_plain_json(self):
        assert parse_json_response('{"a": 1}') == {"a": 1}

    def test_markdown_json_block(self):
        assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}

    def test_markdown_no_lang(self):
        assert parse_json_response('```\n{"a": 1}\n```') == {"a": 1}

    def test_text_before_json(self):
        assert parse_json_response('好的, 结果是:\n{"a": 1}') == {"a": 1}

    def test_text_after_json(self):
        assert parse_json_response('{"a": 1}\n\n以上是结果') == {"a": 1}

    def test_json_array(self):
        assert parse_json_response('[{"x": 1}, {"y": 2}]') == [{"x": 1}, {"y": 2}]

    def test_markdown_array(self):
        assert parse_json_response('```json\n[1, 2, 3]\n```') == [1, 2, 3]

    def test_nested_json(self):
        assert parse_json_response('{"models": ["a", "b"], "reason": "test"}') == {
            "models": ["a", "b"], "reason": "test"
        }

    def test_none_returns_none(self):
        assert parse_json_response(None) is None

    def test_empty_returns_none(self):
        assert parse_json_response("") is None

    def test_garbage_returns_none(self):
        assert parse_json_response("完全不是JSON的文字") is None

    def test_multiline_json(self):
        content = '```json\n{\n  "intent": "TEXT_TO_SQL",\n  "confidence": 0.9\n}\n```'
        result = parse_json_response(content)
        assert result == {"intent": "TEXT_TO_SQL", "confidence": 0.9}
