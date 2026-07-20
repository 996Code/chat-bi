"""
T037: Relevant Recall — 按需召回 Agent 记忆注入 prompt

对标:
  - Claude Code §5.4: scanMemoryFiles → formatManifest → 轻量选择
  - config.memory_max_recall_count = 5
  - 宁缺毋滥: 无相关记忆返回空, 不灌无关内容

设计:
  - recall_memories(question, max_count): 从 agent_memory 的记忆清单里
    按关键词相关性选最多 max_count 条, 读取内容
  - 不调 LLM (省调用), 用关键词重叠度排序
  - format_memories_for_prompt: 格式化注入 SQL 生成 prompt 的动态段
  - extract_memory_from_turn: LLM 自主提炼 — 对话后判断是否产生值得保留的新知识
  - consolidate_memories: 记忆整理 — LLM 合并去重碎片化记忆
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def recall_memories(
    question: str,
    memory_dir: str = "memory",
    max_count: int | None = None,
    data_source_id: str | None = None,
    tenant_id: str | None = None,
) -> list[dict]:
    """按相关性召回记忆 (最多 max_count 条)。

    对标 Claude Code §5.4: 不全量灌入, 只召回相关的。
    策略: 记忆 description 与问题的关键词重叠度 (轻量, 不调 LLM)。

    Args:
        question: 用户问题
        memory_dir: 记忆目录 (直接指定时优先使用)
        max_count: 最多返回条数 (None → config.memory_max_recall_count)
        data_source_id: 数据源 ID (与 tenant_id 一起构造 memory_dir)
        tenant_id: 租户 ID

    Returns:
        [{name, description, content}, ...] 按相关性降序, 无匹配返回空
    """
    if max_count is None:
        from app.core.config import get_settings
        max_count = get_settings().memory_max_recall_count

    # 构造记忆目录: 优先用 memory_dir, 否则用 tenant_id + data_source_id
    if not memory_dir or memory_dir == "memory":
        if tenant_id and data_source_id:
            memory_dir = f"memory/{tenant_id}/{data_source_id}"
        elif tenant_id:
            memory_dir = f"memory/{tenant_id}"

    mem_path = Path(memory_dir)
    if not mem_path.exists():
        mem_path.mkdir(parents=True, exist_ok=True)

    # 种子数据: 目录为空时从 _template/ 复制默认记忆
    _ensure_seed_memories(mem_path)

    # 扫描记忆清单 (id + name + description)
    memories_meta = []
    for md_file in sorted(mem_path.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            # 跳过已整理的记忆 (已被合并, 不再注入 prompt)
            if re.search(r"^\s*consolidated:\s*true", content[:500], re.MULTILINE):
                continue
            # 跳过 linkage 类型 (结构化数据, 不适合注入 prompt; 通过图谱 confidence 间接影响 SQL 生成)
            if re.search(r"^\s*type:\s*linkage", content[:500], re.MULTILINE):
                continue
            # 提取 frontmatter 的 id + name + description
            id_match = re.search(r"^id:\s*(.+)$", content, re.MULTILINE)
            name_match = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
            desc_match = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
            if name_match:
                memories_meta.append({
                    "id": id_match.group(1).strip() if id_match else md_file.stem,
                    "name": name_match.group(1).strip(),
                    "description": desc_match.group(1).strip() if desc_match else "",
                    "content": content,
                    "file": str(md_file),
                })
        except Exception:
            continue

    if not memories_meta:
        return []

    # 关键词相关性排序 (description + name 与问题的词重叠)
    # 分词: 英文按 \b 边界拆, 中文按单字拆 (避免 "GMV计算" 被当成一个 token)
    def _tokenize(text: str) -> set[str]:
        """分词: 英文单词 + 中文单字, 全部小写。"""
        tokens: set[str] = set()
        for m in re.finditer(r"[a-zA-Z0-9]+", text):
            tokens.add(m.group().lower())
        for m in re.finditer(r"[\u4e00-\u9fff]", text):
            tokens.add(m.group())
        return tokens

    question_words = _tokenize(question)
    scored = []
    for mem in memories_meta:
        mem_words = _tokenize(mem["name"] + " " + mem["description"])
        overlap = len(question_words & mem_words)
        if overlap > 0:
            scored.append((overlap, mem))

    if not scored:
        return []  # 无相关记忆, 宁缺毋滥

    # 按重叠度降序, 取前 max_count
    scored.sort(key=lambda x: x[0], reverse=True)
    return [mem for _, mem in scored[:max_count]]


def format_memories_for_prompt(memories: list[dict]) -> str:
    """格式化记忆为 prompt 片段 (注入 SQL 生成动态段)。

    对标 Claude Code: formatMemoryManifest — 给模型约束性提示。
    """
    if not memories:
        return ""

    lines = ["【Agent 记忆 (相关业务知识)】"]
    for mem in memories:
        lines.append(f"[{mem['name']}] {mem['description']}")
        content = mem.get("content", "")
        body = re.sub(r"^---\n.*?\n---\n?", "", content, flags=re.DOTALL).strip()
        if body:
            lines.append(body[:200])  # 截断防 prompt 爆炸
    return "\n".join(lines)


async def extract_memory_from_turn(
    question: str,
    sql: str,
    tables: list[str],
    reply: str,
    memory_dir: str,
) -> dict | None:
    """LLM 自主提炼 — 判断本轮对话是否产生了值得跨会话保留的新知识。

    对标 Claude Code: Agent 在对话中自动识别值得记住的事实和偏好。
    只提取"业务约定/字段含义/用户偏好"类知识, 不记录一次性查询结果。

    Args:
        question: 用户问题
        sql: 生成的 SQL
        tables: 涉及的表名
        reply: Agent 回复
        memory_dir: 记忆目录 (memory/{tenant_id}/{data_source_id}/)

    Returns:
        提炼结果 {name, description, type, content} 或 None (不值得记)
    """
    # 读取已有记忆, 避免重复
    existing_summaries = _get_existing_memory_summaries(memory_dir)

    prompt = (
        "你是 BI Agent 的记忆提炼模块。判断本轮对话是否产生了值得跨会话保留的新知识。\n\n"
        "【值得记的】\n"
        "- 新发现的业务约定 (如 status=2 表示审核中)\n"
        "- 字段含义的澄清 (如 avg_rating 是所有评价的均值, 不是中位数)\n"
        "- 用户表达的偏好 (如 查占比时用百分比格式)\n"
        "- 修正了之前的错误理解\n\n"
        "【不值得记的】\n"
        "- 一次性的查询结果 (如 本月销售额 120 万)\n"
        "- 与已有记忆重复的知识\n"
        "- 纯技术细节 (SQL 优化建议)\n"
        "- 用户临时性的表述 (如 换个图表)\n\n"
        f"【已有记忆 (避免重复)】\n{existing_summaries}\n\n"
        f"【本轮对话】\n"
        f"用户问: {question}\n"
        f"生成 SQL: {sql[:300]}\n"
        f"涉及表: {', '.join(tables[:5])}\n"
        f"Agent 回复: {reply[:200]}\n\n"
        "判断: 如果有值得记的新知识, 返回 JSON (严格格式):\n"
        '{"should_save": true, "name": "英文slug", "description": "一句话描述(用于关键词匹配召回)", '
        '"type": "project", "content": "记忆正文(Markdown)"}\n\n'
        "如果无新知识值得记, 返回:\n"
        '{"should_save": false}\n\n'
        "只返回 JSON, 不要解释。"
    )

    try:
        from app.core.llm_client import llm_chat
        content, _ = await llm_chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        from app.core.llm_json import parse_json_response
        parsed = parse_json_response(content)
        if not parsed or not isinstance(parsed, dict):
            return None
        if not parsed.get("should_save"):
            return None
        # 校验必填字段
        name = parsed.get("name", "").strip()
        desc = parsed.get("description", "").strip()
        mem_content = parsed.get("content", "").strip()
        if not name or not mem_content:
            return None
        # name 安全检查: 允许安全字符 + 空格 + 中文 (name 是显示标题不是文件名)
        if not re.match(r"^[a-zA-Z0-9_\-\u4e00-\u9fff\s\u3000-\u303f\uff00-\uffef]+$", name):
            logger.warning("extract_memory: LLM 生成的 name 不合法: %s", name)
            return None
        return {
            "name": name,
            "description": desc,
            "type": parsed.get("type", "project"),
            "content": mem_content,
        }
    except Exception as e:
        logger.warning("extract_memory_from_turn 失败 (不阻塞): %s", e)
        return None


def _get_existing_memory_summaries(memory_dir: str) -> str:
    """获取已有记忆的摘要 (供 LLM 避免重复)。"""
    mem_path = Path(memory_dir)
    if not mem_path.exists():
        return "(无)"
    summaries = []
    for md_file in sorted(mem_path.glob("*.md")):
        if md_file.name == "MEMORY.md":
            continue
        try:
            content = md_file.read_text(encoding="utf-8")[:500]
            name_match = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
            desc_match = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
            if name_match:
                name = name_match.group(1).strip()
                desc = desc_match.group(1).strip() if desc_match else ""
                summaries.append(f"- {name}: {desc}")
        except Exception:
            continue
    return "\n".join(summaries) if summaries else "(无)"


async def consolidate_memories(
    store,  # AgentMemoryStore
    memory_dir: str,
    ids: list[str] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> dict:
    """整理记忆 — LLM 合并去重碎片化记忆。

    当记忆条目较多时, 将碎片化的记忆合并为更精炼的几条。
    原有记忆标记 consolidated=true (默认隐藏), 合并结果作为新记忆写入。

    Args:
        store: AgentMemoryStore 实例
        memory_dir: 记忆目录
        ids: 只整理指定的记忆 (None = 整理全部未整理的)
        on_progress: 进度回调 (progress 0-100, stage 描述文字)

    Returns:
        {"consolidated": int, "total": int, "detail": str}
    """
    def _progress(pct: int, stage: str):
        if on_progress:
            on_progress(pct, stage)

    _progress(5, "准备中...")

    all_memories = store.list_memories()
    # 过滤: 已整理的不参与, ids 非空时只取指定的 (空列表/None 都表示整理全部)
    # linkage 类型是结构化数据 (co_occurrence/tables), LLM 整理会破坏结构, 跳过
    memories = []
    for m in all_memories:
        if m.get("consolidated"):
            continue
        if m.get("type") == "linkage":
            continue
        if ids and m["id"] not in ids:
            continue
        memories.append(m)

    if len(memories) <= 1:
        _progress(100, "完成")
        return {"consolidated": 0, "total": len(memories), "detail": "记忆条目较少, 无需整理"}

    # 读取所有记忆内容
    _progress(10, "读取记忆内容...")
    all_content = []
    for m in memories:
        full = store.read_memory(m["id"]) or ""
        body = re.sub(r"^---\n.*?\n---\n?", "", full, flags=re.DOTALL).strip()
        if body:
            all_content.append(f"[{m['name']}] ({m.get('type', 'project')}) {m.get('description', '')}\n{body}")

    if not all_content:
        _progress(100, "完成")
        return {"consolidated": 0, "total": len(memories), "detail": "无有效记忆内容可整理"}

    _progress(30, "调用 LLM 整理中...")

    prompt = (
        "你是 BI Agent 的记忆整理模块。将以下碎片化的记忆合并为更精炼的几条。\n\n"
        "规则:\n"
        "- 合并重复或高度相关的记忆\n"
        "- 保留所有有价值的知识, 不要丢失信息\n"
        "- 每条记忆有明确独立的主题\n"
        "- 返回 JSON 数组, 每项格式:\n"
        '  {"name": "简短标题", "description": "一句话描述", "type": "project", "content": "Markdown正文"}\n\n'
        f"【现有记忆 ({len(all_content)} 条)】\n"
        + "\n---\n".join(all_content)
        + "\n\n只返回 JSON 数组, 不要解释。"
    )

    try:
        from app.core.llm_client import llm_chat
        content, _ = await llm_chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )

        _progress(80, "写入整理结果...")
        from app.core.llm_json import parse_json_response
        parsed = parse_json_response(content)
        if not parsed:
            _progress(100, "完成")
            return {"consolidated": 0, "total": len(memories), "detail": "LLM 未返回有效结果"}
        # 兼容: LLM 可能返回 dict 而非 list
        if isinstance(parsed, dict):
            parsed = parsed.get("memories", [parsed])
        if not isinstance(parsed, list):
            _progress(100, "完成")
            return {"consolidated": 0, "total": len(memories), "detail": "LLM 返回格式异常"}

        # 写入整理后的记忆 (每条生成新 UUID, 类型统一为 consolidated)
        saved = 0
        for item in parsed:
            name = item.get("name", "").strip()
            desc = item.get("description", "").strip()
            mem_content = item.get("content", "").strip()
            if not name or not mem_content:
                continue
            store.save_memory(
                name=name,
                description=desc or name,
                content=mem_content,
                memory_type="consolidated",
            )
            saved += 1

        # 标记原始记忆为已整理
        _progress(90, "标记原始记忆...")
        marked = 0
        for m in memories:
            store.mark_consolidated(m["id"])
            marked += 1

        _progress(100, "完成")
        return {
            "consolidated": saved,
            "total": len(memories),
            "detail": f"整理为 {saved} 条精炼记忆, {marked} 条原始记忆已标记为已整理 (默认隐藏)",
        }
    except Exception as e:
        logger.warning("consolidate_memories 失败: %s", e)
        return {"consolidated": 0, "total": len(memories), "detail": f"整理失败: {e}"}


def save_query_memory(question: str, tables: list[str], memory_dir: str = "memory") -> None:
    """记录用户查询到 memory (MEM-01 自主记忆写入闭环)。

    NOTE: 此函数目前无生产调用方 (chat.py/chat_stream.py 已移除调用)。
    与 fewshot 回流功能重叠 (fewshot 更有价值: 含 SQL, 语义检索)。
    保留作为备用, 若后续需要关键词记忆召回可重新接入。
    """
    if not question:
        return
    try:
        from datetime import datetime
        mem_path = Path(memory_dir) / "recent_queries.md"
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        if not mem_path.exists():
            mem_path.write_text(
                "---\nname: recent_queries\ndescription: 用户最近的查询记录和常用表\n---\n\n",
                encoding="utf-8",
            )
        ts = datetime.now().strftime("%m-%d %H:%M")
        tables_str = ", ".join(tables) if tables else "-"
        entry = f"- [{ts}] {question} (表: {tables_str})\n"
        with open(mem_path, "a", encoding="utf-8") as f:
            f.write(entry)
        lines = mem_path.read_text(encoding="utf-8").splitlines()
        if len(lines) > 200:
            fm_end = 0
            for i, line in enumerate(lines):
                if line.strip() == "---" and i > 0:
                    fm_end = i + 1
                    break
            kept = lines[:fm_end] + lines[-100:]
            mem_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except Exception as e:
        logger.debug("save_query_memory 失败 (不阻塞): %s", e)


def _ensure_seed_memories(mem_path: Path) -> None:
    """目录为空时从 _template/ 复制种子记忆 (不覆盖已有内容)。

    与 AgentMemoryStore._ensure_seed_memories 逻辑一致,
    确保管线侧 (recall_memories) 和 API 侧都能自动初始化。
    """
    import shutil
    if any(f for f in mem_path.glob("*.md") if f.name != "MEMORY.md"):
        return
    # memory/{tenant_id}/{data_source_id}/ → _template 在 memory/ 根目录
    template_dir = mem_path.parent.parent / "_template"
    if not template_dir.is_dir():
        return
    try:
        for item in template_dir.iterdir():
            if item.is_file() and item.suffix == ".md" and item.name != "MEMORY.md":
                dest = mem_path / item.name
                if not dest.exists():
                    shutil.copy2(item, dest)
        logger.info("种子 Memory 已初始化到 %s (recall 侧)", mem_path)
    except Exception as e:
        logger.warning("种子 Memory 初始化失败 (不阻塞): %s", e)


def _extract_join_pairs(join_path_section: str) -> set[tuple[str, str]]:
    """从 join_path_section 提取直接 JOIN 的表对（字典序）。

    用于判断某个表对是否有直接 JOIN 路径（W2: 非直接关联标注）。
    join_path_section 格式类似: "biz_orders.user_id = biz_users.id\n..."
    """
    if not join_path_section:
        return set()
    pairs: set[tuple[str, str]] = set()
    # 匹配 table.column = table.column 模式
    for m in re.finditer(r"([a-zA-Z_][\w]*)\.[a-zA-Z_][\w]*\s*=\s*([a-zA-Z_][\w]*)\.[a-zA-Z_][\w]*", join_path_section):
        pairs.add(tuple(sorted([m.group(1), m.group(2)])))
    return pairs


def _build_linkage_content(
    table_a: str,
    table_b: str,
    state,
    direct_join_pairs: set[tuple[str, str]],
    existing_scenes: list[str] | None = None,
) -> str:
    """构建 linkage 记忆的 content。

    Args:
        direct_join_pairs: 本轮查询中有直接 JOIN 的表对集合（判断间接关联）
        existing_scenes: 已记录的场景列表（更新时追加新场景，去重）
    """
    content_parts = []
    pair = tuple(sorted([table_a, table_b]))
    is_direct = pair in direct_join_pairs

    # JOIN 路径
    if is_direct and state.join_path_section:
        # 直接关联：提取该表对的 JOIN 行（而非整段）
        join_lines = []
        for line in state.join_path_section.split("\n"):
            if table_a in line and table_b in line:
                join_lines.append(line.strip())
        if join_lines:
            content_parts.append(f"## JOIN 路径\n" + "\n".join(join_lines) + "\n")
    elif not is_direct:
        # 间接关联：标注经由哪些表
        # 找到连接 table_a 和 table_b 的中间表
        intermediaries = []
        for t in state.current_tables:
            if t in (table_a, table_b):
                continue
            if tuple(sorted([table_a, t])) in direct_join_pairs and tuple(sorted([table_b, t])) in direct_join_pairs:
                intermediaries.append(t)
        via = "、".join(intermediaries) if intermediaries else "其他表"
        content_parts.append(f"## 关联方式\n间接关联（经由 {via}）\n")

    # 典型场景（追加模式：去重）
    scenes = list(existing_scenes) if existing_scenes else []
    if state.question and state.question not in scenes:
        scenes.append(state.question)
    if scenes:
        content_parts.append("## 典型场景\n" + "\n".join(f"- {s}" for s in scenes) + "\n")

    # 聚合方式
    if hasattr(state, "thinking") and state.thinking and hasattr(state.thinking, "aggregation"):
        content_parts.append(f"## 聚合方式\n{state.thinking.aggregation}\n")

    # SQL 示例（截断）
    if state.sql:
        content_parts.append(f"## SQL 示例\n```sql\n{state.sql[:200]}\n```\n")

    return "\n".join(content_parts)


def _extract_existing_scenes(content: str) -> list[str]:
    """从已有 linkage content 提取已记录的场景列表（去重用）。"""
    if not content:
        return []
    scenes: list[str] = []
    in_scenes = False
    for line in content.split("\n"):
        if line.startswith("## 典型场景"):
            in_scenes = True
            continue
        if line.startswith("## "):
            in_scenes = False
            continue
        if in_scenes and line.strip().startswith("- "):
            scenes.append(line.strip()[2:])
    return scenes


def persist_linkage_memory(mem_store, state) -> None:
    """沉淀链路经验到 linkage 记忆 (E1 Task 2.1)。

    从 state 直接读取表对（不解析 SQL），对每对表创建或更新 linkage 记忆。
    单表查询跳过（无 JOIN 信号）。

    Args:
        mem_store: AgentMemoryStore 实例
        state: AgentState 实例，需包含 current_tables/join_path_section/thinking/question/sql
    """
    from itertools import combinations

    # 单表查询跳过
    if len(state.current_tables) < 2:
        return

    # 预计算直接 JOIN 表对集合（W2: 判断间接关联）
    direct_join_pairs = _extract_join_pairs(state.join_path_section)

    # 生成所有表对组合
    table_pairs = list(combinations(sorted(state.current_tables), 2))

    for table_a, table_b in table_pairs:
        # 检查是否已有该表对的 linkage 记忆
        existing = mem_store.get_linkage_memory(table_a, table_b)

        if existing:
            # 已存在：co_occurrence + 1，追加新场景（W1: 若 question 新颖）
            new_co = existing["co_occurrence"] + 1

            # 读取原内容（list_memories 不返回 content，需要 read_memory）
            original_content = mem_store.read_memory(existing["id"]) or ""

            # 提取已记录的场景（去重用）
            existing_scenes = _extract_existing_scenes(original_content)

            # 重建 content（含追加新场景 + 保留 JOIN 路径等）
            new_content = _build_linkage_content(
                table_a, table_b, state, direct_join_pairs, existing_scenes
            )

            mem_store.save_memory(
                name=f"linkage-{table_a}-{table_b}",
                description=f"表 {table_a} 和 {table_b} 的共现经验",
                content=new_content,
                memory_type="linkage",
                mem_id=existing["id"],
                extra_metadata={
                    "co_occurrence": new_co,
                    "tables": [table_a, table_b]
                }
            )
            logger.info(f"更新 linkage 记忆 {table_a}-{table_b}: co_occurrence={new_co}")
        else:
            # 新表对：创建 linkage 记忆
            content = _build_linkage_content(table_a, table_b, state, direct_join_pairs)

            mem_store.save_memory(
                name=f"linkage-{table_a}-{table_b}",
                description=f"表 {table_a} 和 {table_b} 的共现经验",
                content=content,
                memory_type="linkage",
                extra_metadata={
                    "co_occurrence": 1,
                    "tables": [table_a, table_b]
                }
            )
            logger.info(f"创建 linkage 记忆 {table_a}-{table_b}")
