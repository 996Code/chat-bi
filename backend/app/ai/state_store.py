"""
T036: State Store — 对话状态管理

对标:
  - AEE-005: 结构化状态 (current_tables/sql/filters/result_summary/chart_type)
  - 追问维度继承 (对标海泰 intent_history)
  - Claude Code §6: 压缩后状态补偿从这里读

设计:
  - ConversationState: 结构化状态 dataclass
  - StateStore: 基于 Checkpointer 的 JSONL 持久化 (save/load)
  - 追问时从 StateStore 恢复上轮状态, 继承未改变的维度
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DecisionPoint:
    """关键决策点 (对标 CMP-003: 用户确认过的决策不丢失)。

    type: table_confirm(确认表) / sql_modify(改SQL) / filter_change(改筛选)
    """
    type: str = ""
    description: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "description": self.description, "detail": self.detail}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DecisionPoint":
        return cls(
            type=d.get("type", ""),
            description=d.get("description", ""),
            detail=d.get("detail", {}),
        )


@dataclass
class ConversationState:
    """一轮对话的结构化状态 (对标 AEE-005)。

    每轮 run_agent 后存储; 追问时恢复 + 继承。
    压缩后状态补偿从这里读 (T038)。
    压缩摘要 + 关键决策点 (T039)。
    """
    current_tables: list[str] = field(default_factory=list)
    current_sql: str = ""
    current_filters: dict[str, Any] = field(default_factory=dict)
    result_summary: dict[str, Any] = field(default_factory=dict)
    chart_type: str | None = None
    # T039: 压缩摘要 (旧轮次的一句话总结) + 关键决策
    compressed_summary: str = ""
    decisions: list[DecisionPoint] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_tables": self.current_tables,
            "current_sql": self.current_sql,
            "current_filters": self.current_filters,
            "result_summary": self.result_summary,
            "chart_type": self.chart_type,
            "compressed_summary": self.compressed_summary,
            "decisions": [d.to_dict() for d in self.decisions],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConversationState":
        return cls(
            current_tables=d.get("current_tables", []),
            current_sql=d.get("current_sql", ""),
            current_filters=d.get("current_filters", {}),
            result_summary=d.get("result_summary", {}),
            chart_type=d.get("chart_type"),
            compressed_summary=d.get("compressed_summary", ""),
            decisions=[DecisionPoint.from_dict(dp) for dp in d.get("decisions", [])],
        )

    def inherit_filters(self, new_filters: dict[str, Any]) -> dict[str, Any]:
        """追问维度继承: 本轮显式声明的覆盖锚点, 未提及的继承。

        对标海泰: "本月各品类销售额" → 追问"服装呢" → category=服装, month继承。
        """
        merged = dict(self.current_filters)  # 先继承全部
        merged.update(new_filters)  # 本轮新值覆盖
        return merged


class StateStore:
    """对话状态持久化 (基于 Checkpointer 的 JSONL append-only)。

    对标 AEE-005: Checkpointer 每轮持久化, 追问恢复。
    与 checkpointer.py 的 Checkpointer 互补: Checkpointer 存完整 turn (含 messages),
    StateStore 存结构化 ConversationState (供追问继承 + 压缩补偿)。
    """

    def __init__(self, base_dir: str = "data/states"):
        self._base_dir = Path(base_dir)

    def _path(self, tenant_id: str, conversation_id: str) -> Path:
        return self._base_dir / tenant_id / f"{conversation_id}.jsonl"

    def save(
        self,
        tenant_id: str,
        conversation_id: str,
        turn_number: int,
        state: ConversationState,
    ) -> None:
        """追加一轮状态到 JSONL。"""
        from datetime import datetime, timezone
        self._base_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(tenant_id, conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "turn": turn_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": state.to_dict(),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        logger.debug("State saved: tenant=%s conv=%s turn=%d", tenant_id, conversation_id, turn_number)

    def load(self, tenant_id: str, conversation_id: str) -> ConversationState | None:
        """读取最后一轮状态 (供追问继承)。"""
        path = self._path(tenant_id, conversation_id)
        if not path.exists():
            return None
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        if not lines or not lines[0]:
            return None
        last_line = lines[-1]
        try:
            entry = json.loads(last_line)
            return ConversationState.from_dict(entry.get("state", {}))
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("State load failed: %s", e)
            return None

    def list_turns(self, tenant_id: str, conversation_id: str) -> list[dict[str, Any]]:
        """列出所有轮次 (供前端展开查看摘要 + 决策, T039)。

        Returns:
            [{turn, timestamp, state_dict}, ...] 按轮次顺序
        """
        path = self._path(tenant_id, conversation_id)
        if not path.exists():
            return []
        turns = []
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            if not line:
                continue
            try:
                entry = json.loads(line)
                turns.append({
                    "turn": entry.get("turn", 0),
                    "timestamp": entry.get("timestamp", ""),
                    "state": entry.get("state", {}),
                })
            except json.JSONDecodeError:
                continue
        return turns
