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
    # 对话标题 + 首条用户问题 (用于侧边栏历史展示)
    title: str = ""
    first_question: str = ""
    # 本轮完整信息 (用于历史对话恢复: 显示问题/结果/图表)
    question: str = ""
    reply: str = ""
    columns: list[str] = field(default_factory=list)
    rows_sample: list[list] = field(default_factory=list)  # 前 N 行 (防大结果撑爆)
    chart_option: dict | None = None
    # T039: 压缩摘要 (旧轮次的一句话总结) + 关键决策
    compressed_summary: str = ""
    decisions: list[DecisionPoint] = field(default_factory=list)
    # T050: 本轮 LLM prompt 记录 (dump-prompts 导出用, 仅 DEBUG 模式填充)
    prompts: list[dict] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_tables": self.current_tables,
            "current_sql": self.current_sql,
            "current_filters": self.current_filters,
            "result_summary": self.result_summary,
            "chart_type": self.chart_type,
            "title": self.title,
            "first_question": self.first_question,
            "question": self.question,
            "reply": self.reply,
            "columns": self.columns,
            "rows_sample": self.rows_sample,
            "chart_option": self.chart_option,
            "compressed_summary": self.compressed_summary,
            "decisions": [d.to_dict() for d in self.decisions],
            "prompts": self.prompts,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConversationState":
        return cls(
            current_tables=d.get("current_tables", []),
            current_sql=d.get("current_sql", ""),
            current_filters=d.get("current_filters", {}),
            result_summary=d.get("result_summary", {}),
            chart_type=d.get("chart_type"),
            title=d.get("title", ""),
            first_question=d.get("first_question", ""),
            question=d.get("question", ""),
            reply=d.get("reply", ""),
            columns=d.get("columns", []),
            rows_sample=d.get("rows_sample", []),
            chart_option=d.get("chart_option"),
            compressed_summary=d.get("compressed_summary", ""),
            decisions=[DecisionPoint.from_dict(dp) for dp in d.get("decisions", [])],
            prompts=d.get("prompts"),
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


def _turns_to_messages(turns: list[dict[str, Any]]) -> list[dict[str, str]]:
    """把 StateStore 轮次转成 role/content messages (供压缩器估算 token + 摘要)。

    每轮 → 一条 user (问题) + 一条 assistant (SQL/结果)。
    跳过空问题轮 (失败/闲聊未持久化的轮次)。
    """
    messages: list[dict[str, str]] = []
    for t in turns:
        st = t.get("state", {}) or {}
        question = (st.get("question") or "").strip()
        if not question:
            continue
        messages.append({"role": "user", "content": question})
        # assistant 回复: SQL + 结果行数 (够压缩器判断信息密度)
        parts = []
        sql = (st.get("current_sql") or "").strip()
        if sql:
            parts.append(f"SQL: {sql[:200]}")
        row_count = (st.get("result_summary") or {}).get("row_count")
        if row_count is not None:
            parts.append(f"结果: {row_count} 行")
        messages.append({"role": "assistant", "content": "; ".join(parts) or "(无结果)"})
    return messages


def _format_state_compensation(latest_state: dict[str, Any], semantic_tables: list[str] | None = None) -> str:
    """状态补偿 (对标 CMP-002): 压缩后补回当前结构化状态, 防 Agent 失忆。

    补偿项: 语义层用到的表 / 当前 SQL / 当前筛选条件 / 结果摘要。
    """
    parts: list[str] = []
    # 语义层表 (由调用方从检索结果传入, 压缩后仍需知道"当前在查哪些表")
    if semantic_tables:
        parts.append(f"当前查询涉及表: {', '.join(semantic_tables)}")
    sql = (latest_state.get("current_sql") or "").strip()
    if sql:
        parts.append(f"当前SQL: {sql[:300]}")
    filters = latest_state.get("current_filters") or {}
    if filters:
        parts.append(f"当前筛选: {filters}")
    rs = latest_state.get("result_summary") or {}
    if rs.get("row_count") is not None:
        parts.append(f"上轮结果: {rs['row_count']} 行")
    return "\n".join(parts)


async def format_history_text(
    turns: list[dict[str, Any]],
    llm_client=None,
    semantic_tables: list[str] | None = None,
) -> str:
    """把历史轮次格式化为注入 LLM 的上下文文本 (多轮对话核心, 对标 ARC-04 + Claude compact)。

    完整面貌 (CMP-002):
        [历史摘要]           ← 旧轮次超 token 阈值时压缩成摘要 (无超阈则省略)
        [最近 N 轮完整历史]   ← 保留最近 compression_keep_recent_turns 轮原文
        [状态补偿]           ← 当前表/SQL/筛选/结果 (压缩后防失忆)

    压缩判断用 should_compress (token > 模型上限 70%), 触发则 await compact_history
    把旧轮次 LLM 摘要 + 保留最近 N 轮 (对标 Claude §6.2)。失败降级截断 (不崩)。

    Args:
        turns: list_turns() 返回值 [{turn, timestamp, state}, ...]
        llm_client: LLM client (压缩摘要用; None 则超阈也只能截断)
        semantic_tables: 当前查询涉及的语义层表名 (状态补偿用)

    Returns:
        格式化历史文本; 无历史返回 ""
    """
    if not turns:
        return ""

    messages = _turns_to_messages(turns)
    if not messages:
        return ""

    from app.core.config import get_settings
    settings = get_settings()
    keep = settings.compression_keep_recent_turns

    # ── 压缩判断 (CMP-001: token 超模型上限 70% 触发) ──
    from app.ai.compressor import should_compress, compact_history, get_compression_circuit_breaker
    summary = ""
    if should_compress(messages, settings.llm_max_tokens):
        # CMP-004 熔断器: 连续压缩失败则跳过 (省 API), 与 SQL 自愈熔断器一致
        cb = get_compression_circuit_breaker()
        if cb.is_tripped():
            logger.warning("压缩熔断器已触发, 跳过压缩直接截断")
            recent_messages = messages[-(keep * 2):] if len(messages) > keep * 2 else messages
        else:
            # compact_history 是 async def (内部调 LLM 生成摘要), 必须 await
            result = await compact_history(messages, keep_recent=keep, llm_client=llm_client)
            if result.error:
                cb.record_failure()
            elif result.summary:
                cb.record_success()
            if result.summary:
                summary = result.summary
                logger.info(
                    "对话压缩触发: %d 条消息 → 摘要 %d 字 + 保留最近 %d 轮",
                    len(messages), len(summary), keep,
                )
            # compact_history 返回的 recent_messages 是保留的最近 N 轮
            recent_messages = result.recent_messages
    else:
        # 未超阈: 全部保留 (仍截断到最近 keep 轮, 防 prompt 过长)
        recent_messages = messages[-(keep * 2):] if len(messages) > keep * 2 else messages

    # ── 拼装完整面貌 (CMP-002): 摘要 + 最近轮次 + 状态补偿 ──
    sections: list[str] = []
    if summary:
        sections.append(f"【历史摘要】\n{summary}")
    # 最近轮次格式化 (从 messages 还原可读文本)
    recent_turns = _messages_to_text(recent_messages)
    if recent_turns:
        sections.append(f"【最近对话】\n{recent_turns}")
    # 状态补偿 (用最后一轮的 state)
    latest_state = (turns[-1].get("state", {}) or {}) if turns else {}
    compensation = _format_state_compensation(latest_state, semantic_tables)
    if compensation:
        sections.append(f"【当前状态】\n{compensation}")

    return "\n\n".join(sections)


def _messages_to_text(messages: list[dict[str, str]]) -> str:
    """role/content messages → 可读多轮文本 (供 format_history_text 的最近轮次段)。"""
    lines: list[str] = []
    turn_no = 0
    for i, m in enumerate(messages):
        if m["role"] == "user":
            turn_no += 1
            lines.append(f'轮{turn_no}: 用户问"{m["content"]}"')
        else:
            lines.append(f"     {m['content']}")
    return "\n".join(lines)
