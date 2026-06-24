"""T043: 负面信号检测测试"""
from __future__ import annotations

from app.services.feedback_detector import detect_negative_signal


class TestNegativeSignal:
    def test_negative_keyword(self):
        assert detect_negative_signal("这个结果不对")
        assert detect_negative_signal("查错了")

    def test_normal_text(self):
        assert not detect_negative_signal("这个月销售额")

    def test_empty(self):
        assert not detect_negative_signal("")
        assert not detect_negative_signal(None)  # type: ignore
