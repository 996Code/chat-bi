"""
ChatBI v2 — Checkpointer Tests (T067)

覆盖:
  - save_turn / load_conversation 往返
  - 多轮追加 + 顺序
  - get_last_state 返回最后轮次状态
  - list_conversations 元数据排序
  - delete_conversation 删除 + 幂等
  - 租户隔离
  - OBS-004: 一致性校验 (turn 编号不连续, JSON 解析失败, 消息为空)
  - 不存在的对话返回空
"""
from __future__ import annotations

import json
import logging

import pytest

from app.core.checkpointer import Checkpointer, get_checkpointer


@pytest.fixture
def cp(tmp_path):
    """创建临时目录的 Checkpointer (隔离测试)。"""
    return Checkpointer(base_dir=str(tmp_path / "checkpoints"))


class TestSaveAndLoad:
    """save_turn + load_conversation 往返。"""

    def test_save_and_load_single_turn(self, cp):
        cp.save_turn("t1", "c1", 1, {"current_sql": "SELECT 1"}, [{"role": "user", "content": "hi"}])
        turns = cp.load_conversation("t1", "c1")
        assert len(turns) == 1
        assert turns[0]["turn"] == 1
        assert turns[0]["state"]["current_sql"] == "SELECT 1"
        assert len(turns[0]["messages"]) == 1

    def test_multi_turn_append(self, cp):
        cp.save_turn("t1", "c1", 1, {"step": 1}, [{"role": "user"}])
        cp.save_turn("t1", "c1", 2, {"step": 2}, [{"role": "assistant"}])
        cp.save_turn("t1", "c1", 3, {"step": 3}, [{"role": "user"}, {"role": "assistant"}])
        turns = cp.load_conversation("t1", "c1")
        assert len(turns) == 3
        assert turns[0]["turn"] == 1
        assert turns[2]["turn"] == 3
        assert len(turns[2]["messages"]) == 2

    def test_load_nonexistent_returns_empty(self, cp):
        assert cp.load_conversation("t1", "no_such_conv") == []

    def test_tenant_isolation(self, cp):
        cp.save_turn("tenant_A", "conv1", 1, {"a": 1}, [{"role": "user"}])
        cp.save_turn("tenant_B", "conv1", 1, {"b": 2}, [{"role": "user"}])
        turns_a = cp.load_conversation("tenant_A", "conv1")
        turns_b = cp.load_conversation("tenant_B", "conv1")
        assert turns_a[0]["state"]["a"] == 1
        assert turns_b[0]["state"]["b"] == 2


class TestGetLastState:
    """get_last_state 返回最后轮次状态。"""

    def test_returns_last_turn_state(self, cp):
        cp.save_turn("t1", "c1", 1, {"sql": "v1"}, [])
        cp.save_turn("t1", "c1", 2, {"sql": "v2"}, [])
        state = cp.get_last_state("t1", "c1")
        assert state is not None
        assert state["sql"] == "v2"
        assert state["last_turn_number"] == 2

    def test_nonexistent_returns_none(self, cp):
        assert cp.get_last_state("t1", "nope") is None


class TestListConversations:
    """list_conversations 元数据 + 排序。"""

    def test_list_returns_metadata(self, cp):
        cp.save_turn("t1", "conv_a", 1, {}, [])
        cp.save_turn("t1", "conv_b", 1, {}, [])
        convs = cp.list_conversations("t1")
        assert len(convs) == 2
        ids = {c["conversation_id"] for c in convs}
        assert ids == {"conv_a", "conv_b"}

    def test_list_empty_tenant(self, cp):
        assert cp.list_conversations("no_such_tenant") == []


class TestDeleteConversation:
    """delete_conversation 删除 + 幂等。"""

    def test_delete_existing(self, cp):
        cp.save_turn("t1", "c1", 1, {}, [])
        assert cp.delete_conversation("t1", "c1") is True
        assert cp.load_conversation("t1", "c1") == []

    def test_delete_nonexistent_returns_false(self, cp):
        assert cp.delete_conversation("t1", "nope") is False


class TestOBS004Consistency:
    """OBS-004: 一致性校验 — turn 编号不连续 / JSON 解析失败 / 消息为空。"""

    def test_non_sequential_turn_numbers_logged(self, cp, caplog):
        """turn 编号不连续 → ERROR 日志。"""
        path = cp._checkpoint_path("t1", "c1")
        # 手动写入不连续的 turn
        with open(path, "w") as f:
            f.write(json.dumps({"turn": 1, "state": {}, "messages": [{"role": "user"}]}) + "\n")
            f.write(json.dumps({"turn": 3, "state": {}, "messages": [{"role": "user"}]}) + "\n")

        with caplog.at_level(logging.ERROR, logger="app.core.checkpointer"):
            turns = cp.load_conversation("t1", "c1")
        assert len(turns) == 2
        assert any("OBS-004" in r.message and "turn" in r.message for r in caplog.records)

    def test_malformed_jsonl_line_handled(self, cp, caplog):
        """JSON 解析失败 → ERROR 日志, 不崩溃。"""
        path = cp._checkpoint_path("t1", "c1")
        with open(path, "w") as f:
            f.write(json.dumps({"turn": 1, "state": {}, "messages": [{"role": "user"}]}) + "\n")
            f.write("BROKEN JSON LINE\n")
            f.write(json.dumps({"turn": 2, "state": {}, "messages": [{"role": "user"}]}) + "\n")

        with caplog.at_level(logging.ERROR, logger="app.core.checkpointer"):
            turns = cp.load_conversation("t1", "c1")
        # 解析失败的那行被跳过, 只返回 2 条有效记录
        assert len(turns) == 2
        assert any("OBS-004" in r.message and "JSON" in r.message for r in caplog.records)

    def test_zero_total_messages_logged(self, cp, caplog):
        """所有 turn 的 messages 都为空 → ERROR 日志。"""
        cp.save_turn("t1", "c1", 1, {}, [])
        cp.save_turn("t1", "c1", 2, {}, [])

        with caplog.at_level(logging.ERROR, logger="app.core.checkpointer"):
            turns = cp.load_conversation("t1", "c1")
        assert len(turns) == 2
        assert any("OBS-004" in r.message and "消息为空" in r.message for r in caplog.records)


class TestGetCheckpointerSingleton:
    """get_checkpointer 全局单例。"""

    def test_singleton_identity(self):
        a = get_checkpointer()
        b = get_checkpointer()
        assert a is b
