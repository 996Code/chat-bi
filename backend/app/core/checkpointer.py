"""
ChatBI v2 — Checkpointer: Session Persistence (T009)

Write simply, rebuild on restore. Append-only JSONL approach.
对标: Claude Code sessionStorage — append-only JSONL + 恢复路径重建状态
      + 海泰 PostgreSQL Checkpointer

架构角色:
  - 每次对话交互 append 一行 JSON 到 .jsonl 文件
  - 恢复时读取所有行, 重建完整对话链
  - 可选内存模式 (测试用), 不强制 PostgreSQL 依赖

数据流:
  Conversation → save_turn() → append to {tenant_id}/{conversation_id}.jsonl
  Restore → load_conversation() → read all lines → validate continuity → return list

核心设计决策:
  - 追加写入 (append-only): 写入快, 不修改历史, 天然支持并发读
  - JSONL 而非 JSON: 每行独立, 避免单文件过大时需要全量重写
  - 路径穿越防御: _checkpoint_path 中验证 candidate 是 base_dir 的子路径
  - OBS-004: 恢复时校验 turn 编号连续性和消息计数一致性
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
        # 确保目录存在, parents=True 支持多级目录创建
        # exist_ok=True 避免并发创建时的 FileExistsError
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _checkpoint_path(self, tenant_id: str, conversation_id: str) -> Path:
        """Get the JSONL checkpoint file path for a conversation (含路径穿越防御)。

        路径结构: {base_dir}/{tenant_id}/{conversation_id}.jsonl
        tenant_id 和 conversation_id 都需非空, 以防止意外创建根目录级别的文件。

        安全:
          - 路径穿越防御: candidate.resolve() 必须在 base_dir 下
          - 空值防御: ValueError 而非路径异常, 语义更清晰
        """
        for name, value in [("tenant_id", tenant_id), ("conversation_id", conversation_id)]:
            if not value or not value.strip():
                raise ValueError(f"Invalid {name}: empty")
        candidate = self.base_dir / tenant_id / f"{conversation_id}.jsonl"
        # resolve() 解析符号链接和相对路径, is_relative_to 检查路径是否逃逸
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

        写入策略:
          - 追加写入 (append-only), 不修改已有数据
          - 每行一个 JSON, include 时间戳用于排序和审计
          - 使用 ensure_ascii=False 保存中文, default=str 序列化非标准类型 (如 datetime)
          - 不设锁: JSONL 追加写入在单进程下是原子的 (write < PIPE_BUF)

        为什么 messages 存全量而非增量:
          - 恢复时不需要做 turn 合并, 直接读取即可
          - 文件大小由 OBS-004 监控, 超出阈值会触发归档
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

        恢复流程:
          1. 读取 JSONL 文件所有行
          2. 逐行 json.loads, 跳过空行
          3. 校验 turn 编号从 1 开始连续递增
          4. 校验总消息数 > 0

        错误处理:
          - 文件不存在 → 返回空列表 (新对话)
          - JSON 解析失败 → 跳过该行, 记录 ERROR 日志 (不丢失其他行)
          - 编号不连续 → 记录 ERROR 日志, 但返回全部数据 (不阻断)
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

        用途:
          - 追问场景: 用户问"那上个月呢?", 需要继承上一轮的 table/SQL 上下文
          - 返回的 state 包含 last_turn_number, 调用方可据此判断是否有历史

        注意: 返回的是 state 的浅拷贝, 修改不会影响原文件。
        """
        turns = self.load_conversation(tenant_id, conversation_id)
        if not turns:
            return None
        last_turn = turns[-1]
        state = dict(last_turn.get("state", {}))  # 复制, 防止修改原数据
        state["last_turn_number"] = last_turn.get("turn", 0)
        return state

    def list_conversations(self, tenant_id: str) -> list[dict[str, Any]]:
        """List all conversations for a tenant (metadata only).

        返回摘要信息, 不包含完整消息内容:
          - conversation_id: 对话 ID
          - turn_count: 轮次数
          - first_turn_at: 开始时间
          - last_turn_at: 最后更新时间 (用于排序)

        按最后更新时间倒序排列, 方便前端展示最近对话。
        """
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
        """Delete a conversation's checkpoint file.

        删除后不可恢复, 调用方需确认。
        如果文件不存在, 返回 False (而非异常)。
        """
        path = self._checkpoint_path(tenant_id, conversation_id)
        if path.exists():
            path.unlink()
            logger.info("Checkpoint deleted: %s/%s", tenant_id, conversation_id)
            return True
        return False


# ── Global instance ────────────────────────────────────────────

_checkpointer: Checkpointer | None = None


def get_checkpointer(base_dir: str | None = None) -> Checkpointer:
    """Get or create the global checkpointer instance.

    单例模式: 首次调用时创建, 后续复用。
    base_dir 仅在首次创建时生效, 后续调用忽略。
    """
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = Checkpointer(base_dir=base_dir)
    return _checkpointer
