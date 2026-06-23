"""
SEC-007: Unicode 清洗 — 单元测试

对标 spec: NFKC 归一化 + 去零宽/方向控制/私有区字符
防注入: 零宽字符隐藏恶意指令, 方向控制字符 (RLO) 篡改显示
"""
from __future__ import annotations

from app.core.text_sanitize import sanitize_text


class TestSanitizeText:
    def test_normal_text_unchanged(self):
        assert sanitize_text("本月销售额") == "本月销售额"

    def test_nfkc_fullwidth_to_halfwidth(self):
        """NFKC: 全角字母数字 → 半角 (防 ＳＥＬＥＣＴ 绕过)。"""
        assert sanitize_text("ＳＥＬＥＣＴ") == "SELECT"

    def test_nfkc_fullwidth_digits(self):
        assert sanitize_text("１２３") == "123"

    def test_zero_width_removed(self):
        """零宽空格 (U+200B) 去除。"""
        text = "销售\u200b额"
        assert sanitize_text(text) == "销售额"

    def test_zero_width_joiner_removed(self):
        """零宽连接符 (U+200D) 去除。"""
        text = "a\u200db"
        assert sanitize_text(text) == "ab"

    def test_directional_override_removed(self):
        """RLO 方向覆盖 (U+202E) 去除 — 防显示篡改。"""
        text = "abc\u202edef"
        cleaned = sanitize_text(text)
        assert "\u202e" not in cleaned

    def test_all_directional_controls_removed(self):
        for ch in ["\u202a", "\u202b", "\u202c", "\u202d", "\u202e"]:
            assert ch not in sanitize_text(f"x{ch}y")

    def test_private_use_area_removed(self):
        """私有区字符 (U+E000-U+F8FF) 去除。"""
        text = "x\ue001y"
        assert sanitize_text(text) == "xy"

    def test_none_returns_empty(self):
        assert sanitize_text(None) == ""

    def test_empty_returns_empty(self):
        assert sanitize_text("") == ""

    def test_whitespace_preserved(self):
        assert sanitize_text("a b  c") == "a b  c"

    def test_combined_attack(self):
        """组合攻击: 全角 + 零宽 + 方向控制。"""
        text = "Ｓ\u200bＥ\u202eＬＥＣＴ"
        cleaned = sanitize_text(text)
        assert cleaned == "SELECT"
        assert "\u200b" not in cleaned
        assert "\u202e" not in cleaned
