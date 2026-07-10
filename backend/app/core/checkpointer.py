"""
ChatBI v2 — Checkpointer: Session Persistence (T009)

Write simply, rebuild on restore. Append-only JSONL approach.
对标: Claude Code sessionStorage — append-only JSONL + 恢复路径重建状态
      + 海泰 PostgreSQL Checkpointer
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Checkpointer:
    """
    Append-only conversation state persistence.

    Each conversation is stored as a JSONL file under the checkpoints directory.
    Every turn appends one line to the file.

    对标 Claude Code:
    - 写入简单: append one JSON line per turn
    - 恢复时重建: read all lines, build conversation chain
    - 不强制 PostgreSQL 依赖 (内存模式 for testing)
    """

    def __init__(self, base_dir: str | None = None):
        if base_dir is None:
            from app.core.config import get_settings
            base_dir = get_settings().checkpointer_dir
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _checkpoint_path(self, tenant_id: str, conversation_id: str) -> Path:
        """Get the JSONL checkpoint file path for a conversation (含路径穿越防御)。"""
        for name, value in [("tenant_id", tenant_id), ("conversation_id", conversation_id)]:
            if not value or not value.strip():
                raise ValueError(f"Invalid {name}: empty")
        candidate = self.base_dir / tenant_id / f"{conversation_id}.jsonl"
        if not candidate.resolve().is_relative_to(self.base_dir.resolve()):
            raise ValueError("Path traversal detected")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    def save_turn(
        self,
        tenant_id: str,
        conversation_id: str,
        turn_number: int,
        state: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> None:
        """Append a conversation turn to the checkpoint file.

        Args:
            tenant_id: Tenant identifier
            conversation_id: Conversation identifier
            turn_number: Sequential turn number
            state: ConversationState dict (current_tables, current_sql, etc.)
            messages: All messages in this turn (user + assistant + tool calls)
        """
        path = self._checkpoint_path(tenant_id, conversation_id)

        entry = {
            "turn": turn_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": state,
            "messages": messages,
        }

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

        logger.debug(
            "Checkpoint saved: tenant=%s conv=%s turn=%d",
            tenant_id, conversation_id, turn_number,
        )

    def load_conversation(
        self, tenant_id: str, conversation_id: str
    ) -> list[dict[str, Any]]:
        """Load all turns for a conversation.

        Returns a list of turn dicts, each containing {turn, timestamp, state, messages}.
        OBS-004: 恢复后校验 turn 编号连续性 + 消息计数一致性, 不一致则 log ERROR。
        """
        path = self._checkpoint_path(tenant_id, conversation_id)

        if not path.exists():
            logger.debug("No checkpoint file: %s", path)
            return []

        turns = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        turns.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.error(
                            "OBS-004: Checkpointer JSON 解析失败 tenant=%s conv=%s line=%d",
                            tenant_id, conversation_id, len(turns) + 1,
                        )

        # OBS-004: 一致性校验 — turn 编号应该连续递增
        if turns:
            expected_turn = 1
            for i, t in enumerate(turns):
                actual_turn = t.get("turn", 0)
                if actual_turn != expected_turn:
                    logger.error(
                        "OBS-004: Checkpointer turn 编号不一致 tenant=%s conv=%s "
                        "expected_turn=%d actual_turn=%d index=%d",
                        tenant_id, conversation_id, expected_turn, actual_turn, i,
                    )
                expected_turn = actual_turn + 1

            # 校验消息计数: 总消息数应 > 0 (每轮至少有 user 或 assistant 消息)
            total_messages = sum(len(t.get("messages", [])) for t in turns)
            if total_messages == 0:
                logger.error(
                    "OBS-004: Checkpointer 恢复后消息为空 tenant=%s conv=%s turns=%d",
                    tenant_id, conversation_id, len(turns),
                )

        logger.debug(
            "Checkpoint loaded: tenant=%s conv=%s turns=%d",
            tenant_id, conversation_id, len(turns),
        )
        return turns

    def get_last_state(
        self, tenant_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        """Get the most recent turn's state (for context inheritance on follow-up questions).

        对标: 海泰 intent_history — 唯一跨查询持久的状态
        """
        turns = self.load_conversation(tenant_id, conversation_id)
        if not turns:
            return None
        last_turn = turns[-1]
        state = dict(last_turn.get("state", {}))  # 复制, 防止修改原数据
        state["last_turn_number"] = last_turn.get("turn", 0)
        return state

    def list_conversations(self, tenant_id: str) -> list[dict[str, Any]]:
        """List all conversations for a tenant (metadata only)."""
        tenant_dir = self.base_dir / tenant_id
        if not tenant_dir.exists():
            return []

        convs = []
        for file_path in tenant_dir.glob("*.jsonl"):
            conv_id = file_path.stem
            # Read first and last lines for metadata
            turns = self.load_conversation(tenant_id, conv_id)
            if turns:
                convs.append({
                    "conversation_id": conv_id,
                    "turn_count": len(turns),
                    "first_turn_at": turns[0].get("timestamp"),
                    "last_turn_at": turns[-1].get("timestamp"),
                })
        return sorted(convs, key=lambda c: c.get("last_turn_at", ""), reverse=True)

    def delete_conversation(self, tenant_id: str, conversation_id: str) -> bool:
        """Delete a conversation's checkpoint file."""
        path = self._checkpoint_path(tenant_id, conversation_id)
        if path.exists():
            path.unlink()
            logger.info("Checkpoint deleted: %s/%s", tenant_id, conversation_id)
            return True
        return False


# ── Global instance ────────────────────────────────────────────

_checkpointer: Checkpointer | None = None


def get_checkpointer(base_dir: str | None = None) -> Checkpointer:
    """Get or create the global checkpointer instance."""
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = Checkpointer(base_dir=base_dir)
    return _checkpointer
