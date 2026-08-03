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
    #
    # 数据流说明:
    #   调用方传入 memory_dir 时直接使用 (用于显式指定路径的测试/特殊场景),
    #   否则根据 tenant_id + data_source_id 构造隔离路径, 确保不同租户/数据源
    #   的记忆互不干扰。这是多租户隔离的关键设计 — 租户 A 查"销售额"不会
    #   召回租户 B 的记忆。
    if not memory_dir or memory_dir == "memory":
        if tenant_id and data_source_id:
            memory_dir = f"memory/{tenant_id}/{data_source_id}"
        elif tenant_id:
            memory_dir = f"memory/{tenant_id}"

    mem_path = Path(memory_dir)
    if not mem_path.exists():
        mem_path.mkdir(parents=True, exist_ok=True)

    # 种子数据: 目录为空时从 _template/ 复制默认记忆
    #
    # 设计背景: 首次使用时记忆目录为空, 需要从模板目录复制预设的种子记忆
    # (如常见业务约定), 确保即使没有历史对话也能提供基础知识。
    # 与 AgentMemoryStore._ensure_seed_memories 逻辑一致, 双路径保障。
    _ensure_seed_memories(mem_path)

    # 扫描记忆清单 (id + name + description)
    #
    # 遍历 memory_dir 下所有 .md 文件, 提取 frontmatter 元数据。
    # 只读取描述信息 (description + name) 用于关键词匹配, 不读全文,
    # 避免大文件内容被全部加载到内存。
    # 跳过已整理的记忆 (consolidated) 和结构化 linkage 数据。
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
    #
    # 为什么不用 LLM 排序:
    #   对标 Claude Code §5.4 — 轻量选择, 不调 LLM。关键词重叠度排序
    #   虽然粗糙但足够用 (记忆数量少, 通常 < 50 条), 且零 API 成本。
    #
    # 分词策略: 英文按 \b 边界拆, 中文按单字拆 (避免 "GMV计算" 被当成一个 token)
    # 中文单字分词的取舍: 虽然会丢失"用户画像"这样的双字词关联,
    # 但单字匹配更宽松, 降低漏召回风险 (precision 换 recall)。
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
    # 宁缺毋滥原则: 不灌无关内容污染 prompt, 对标 docstring 中所述

    # 按重叠度降序, 取前 max_count
    # 注意: 这里只按 overlapped token 数量排序, 不做 TF-IDF 加权,
    # 因为记忆条目数少, 简单排序即可满足需求。
    scored.sort(key=lambda x: x[0], reverse=True)
    return [mem for _, mem in scored[:max_count]]


def format_memories_for_prompt(memories: list[dict]) -> str:
    """格式化记忆为 prompt 片段 (注入 SQL 生成动态段)。

    对标 Claude Code: formatMemoryManifest — 给模型约束性提示。

    数据流:
      1. 输入: recall_memories 返回的 [{name, description, content}, ...]
      2. 处理: 提取 frontmatter body, 截断长内容
      3. 输出: 格式化的 Markdown 文本, 注入到 SQL 生成 prompt 的"记忆"段
    """
    if not memories:
        return ""

    lines = ["【Agent 记忆 (相关业务知识)】"]
    for mem in memories:
        lines.append(f"[{mem['name']}] {mem['description']}")
        content = mem.get("content", "")
        # 去掉 frontmatter (--- 之间的内容), 只保留 body 正文
        body = re.sub(r"^---\n.*?\n---\n?", "", content, flags=re.DOTALL).strip()
        if body:
            # 截断前 200 字符: 防止某条记忆过长导致 prompt 膨胀,
            # 200 字符足够表达核心信息, 过长内容应在 consolidate 时精简
            lines.append(body[:200])
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
    #
    # 关键设计: 将已有记忆摘要发给 LLM, 让 LLM 判断本轮是否产生新知识,
    # 避免重复记忆 (如每次对话都记"status=2 表示审核中")。
    # 这是"去重"的第一道防线, 第二道防线在 consolidate_memories 中合并。
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
    """获取已有记忆的摘要 (供 LLM 避免重复)。

    数据流:
      1. 读取 memory_dir 下所有 .md 文件
      2. 提取 frontmatter 中的 name 和 description
      3. 拼接为摘要文本, 传给 LLM

    边界情况:
      - 跳过 MEMORY.md (元数据文件, 不是业务记忆)
      - 目录不存在时返回 "(无)"
    """
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

    触发时机: 通常由定时任务或手动触发, 非每轮对话自动执行。
    设计原则:
      - 合并后不删除原始记忆 (只标记隐藏), 可追溯
      - linkage 类型是结构化数据, 跳过 (LLM 整理会破坏结构)
      - 单条或少条时跳过 (不值得调 LLM)

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
    #
    # 过滤规则说明:
    #   1. consolidated=true: 已被合并过的碎片记忆, 跳过 (避免重复整理)
    #   2. type=linkage: 结构化数据 (co_occurrence/tables), LLM 整理会破坏结构, 跳过
    #   3. ids 过滤: 支持选择性整理, 如只整理最近新增的几条
    #
    # 边界情况: ids 传入空列表 [] 时, 条件 `if ids and m["id"] not in ids` 为 False,
    # 所有条目都通过, 与 None 行为一致。
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
        # 去掉 frontmatter, 只保留 body 正文给 LLM 整理
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
        # 某些模型在返回单条整理结果时倾向用 dict 包裹, 而非数组
        if isinstance(parsed, dict):
            parsed = parsed.get("memories", [parsed])
        if not isinstance(parsed, list):
            _progress(100, "完成")
            return {"consolidated": 0, "total": len(memories), "detail": "LLM 返回格式异常"}

        # 写入整理后的记忆 (每条生成新 UUID, 类型统一为 consolidated)
        #
        # 设计决策: 写入新记忆而非修改原记忆, 保持可追溯性。
        # 原始记忆被标记为 consolidated=true, 在 recall 时默认跳过。
        # 如果整理结果不满意, 可以手动取消标记恢复原始记忆。
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

    数据流:
      1. 输入: 用户问题 + 涉及的表名
      2. 写入: memory/{tenant_id}/recent_queries.md 追加新行
      3. 容量控制: 超过 200 行时截断, 保留前 2 行 frontmatter + 后 100 行

    边界情况:
      - question 为空时直接返回 (不写入空记录)
      - 目录不存在时自动创建 (mkdir parents)
      - 文件不存在时创建带 frontmatter 的新文件
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


def _extract_join_on_conditions(
    join_path_section: str, table_a: str, table_b: str
) -> list[dict[str, str]]:
    """从 join_path_section 提取特定表对的直接 ON 条件 (结构化)。

    join_path_section 有两种格式:
      1. 简单格式: "biz_orders.user_id = biz_users.id"
      2. 多跳格式: "biz_orders LEFT JOIN st_shops ON biz_orders.shop_id = st_shops.id (confidence=1.0)
                     LEFT JOIN pd_categories ON st_shops.category_id = pd_categories.id (confidence=1.0)"

    只提取 table_a 和 table_b **直接 JOIN** 的 ON 条件,
    跳过通过中间表间接关联的路径段。

    Returns:
        [{"on": "biz_orders.user_id = biz_users.id", "join_type": "LEFT"}, ...]
    """
    if not join_path_section:
        return []
    results: list[dict[str, str]] = []
    for line in join_path_section.split("\n"):
        line = line.strip()
        if not line:
            continue
        if table_a not in line or table_b not in line:
            continue

        # 判断是否为多跳格式 (含 JOIN 关键字)
        has_join = bool(re.search(r"\b(?:LEFT|RIGHT|INNER|CROSS)?\s*JOIN\b", line, re.IGNORECASE))

        if has_join:
            # 多跳格式: 按 JOIN 拆成独立段, 每段格式: "table_b ON condition (confidence=x)"
            segments = re.split(r"\b(?:LEFT|RIGHT|INNER|CROSS)?\s*JOIN\b", line, flags=re.IGNORECASE)
            for seg in segments[1:]:  # 跳过第一段 (FROM 表, 不是 JOIN)
                # 提取 ON 子句
                on_match = re.search(r"\bON\s+(.+?)(?:\s+\(confidence|$)", seg, re.IGNORECASE)
                if not on_match:
                    continue
                on_clause = on_match.group(1).strip()
                on_clause = re.sub(r"\s*\(confidence.*$", "", on_clause).strip()
                # 检查 ON 条件是否直接关联 table_a 和 table_b
                col_match = re.search(
                    r"([a-zA-Z_][\w]*)\.[a-zA-Z_][\w]*\s*=\s*([a-zA-Z_][\w]*)\.[a-zA-Z_][\w]*",
                    on_clause,
                )
                if col_match and {col_match.group(1), col_match.group(2)} == {table_a, table_b}:
                    results.append({"on": on_clause, "join_type": "LEFT"})
        else:
            # 简单格式: "table_a.col = table_b.col" — 直接提取等值条件
            col_match = re.search(
                r"([a-zA-Z_][\w]*)\.[a-zA-Z_][\w]*\s*=\s*([a-zA-Z_][\w]*)\.[a-zA-Z_][\w]*",
                line,
            )
            if col_match and {col_match.group(1), col_match.group(2)} == {table_a, table_b}:
                on_clause = col_match.group(0)
                # 去掉可能的 confidence 标注
                on_clause = re.sub(r"\s*\(confidence.*$", "", on_clause).strip()
                results.append({"on": on_clause, "join_type": "LEFT"})
    return results


def _build_linkage_content(
    table_a: str,
    table_b: str,
    state,
    direct_join_pairs: set[tuple[str, str]],
    existing_scenes: list[str] | None = None,
) -> str:
    """构建 linkage 记忆的 content (Markdown body, 给 LLM 看)。

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
        agg_text = state.thinking.aggregation
        # 只保留聚合关键词, 不存 LLM 原始长文本
        agg_clean = _clean_aggregation(agg_text) if agg_text else ""
        if agg_clean:
            content_parts.append(f"## 聚合方式\n{agg_clean}\n")

    return "\n".join(content_parts)


def _extract_existing_scenes(content: str, frontmatter_scenes: list[str] | None = None) -> list[str]:
    """从已有 linkage 记忆提取已记录的场景列表（去重用）。

    优先从 frontmatter_scenes (结构化字段) 读取，回退到 Markdown body 解析。
    """
    if frontmatter_scenes:
        return list(frontmatter_scenes)
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


def _extract_aggregation(state) -> str:
    """从 state.thinking 提取聚合方式 (简洁关键字)。

    LLM 返回的 aggregation 可能是长文本如 "SUM(actual_amount) 按 category 分组",
    只提取首个聚合函数名 (SUM/COUNT/AVG/MAX/MIN) 作为 frontmatter 值。
    """
    if hasattr(state, "thinking") and state.thinking and hasattr(state.thinking, "aggregation"):
        agg = state.thinking.aggregation
        if agg:
            return _clean_aggregation(str(agg))
    return ""


def _clean_aggregation(agg_str: str) -> str:
    """清洗 aggregation 值: 提取聚合关键字, 截断过长文本。"""
    m = re.search(r"\b(SUM|COUNT|AVG|MAX|MIN)\b", agg_str, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    # 无匹配则截取前 20 字符 (兜底)
    return agg_str[:20].strip()


def persist_linkage_memory(mem_store, state, conv_id: str | None = None) -> None:
    """沉淀链路经验到 linkage 记忆 (E1 Task 2.1)。

    从 state 直接读取表对（不解析 SQL），对每对表创建或更新 linkage 记忆。
    单表查询跳过（无 JOIN 信号）。

    结构化 frontmatter 字段 (程序化消费):
      - join_paths: [{"on": "biz_orders.user_id = biz_users.id", "join_type": "LEFT"}, ...]
      - scenes: ["本月各品类销售额", ...]
      - aggregation: "SUM" (标量字符串)

    Markdown body 保留 (给 LLM 注入 prompt 用)。

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

    # 提取聚合方式
    aggregation = _extract_aggregation(state)

    # 生成所有表对组合
    table_pairs = list(combinations(sorted(state.current_tables), 2))

    for table_a, table_b in table_pairs:
        # 提取该表对的 JOIN ON 条件 (结构化)
        join_paths = _extract_join_on_conditions(state.join_path_section, table_a, table_b)

        # 检查是否已有该表对的 linkage 记忆
        existing = mem_store.get_linkage_memory(table_a, table_b)

        if existing:
            # 已存在：co_occurrence + 1，追加新场景（W1: 若 question 新颖）
            new_co = existing["co_occurrence"] + 1

            # 从 frontmatter 读取已有 scenes (结构化字段优先), 回退到 Markdown body
            existing_fm_scenes = existing.get("scenes")
            original_content = mem_store.read_memory(existing["id"]) or ""
            existing_scenes = _extract_existing_scenes(original_content, existing_fm_scenes)

            # 合并 JOIN 路径: 保留已有 + 追加新的 (去重)
            # 清洗已有的脏 on 值 (旧数据可能含 "LEFT JOIN" 或 "confidence")
            existing_join_paths = existing.get("join_paths") or []
            cleaned_existing: list[dict[str, str]] = []
            for jp in existing_join_paths:
                on_val = jp.get("on", "")
                # 脏数据: on 值包含 LEFT JOIN (多跳路径整行被写入)
                if "LEFT JOIN" in on_val.upper() or "JOIN" in on_val.upper().split()[0:1]:
                    # 尝试从脏数据中提取纯 ON 条件
                    col_match = re.search(
                        r"([a-zA-Z_][\w]*)\.[a-zA-Z_][\w]*\s*=\s*([a-zA-Z_][\w]*)\.[a-zA-Z_][\w]*",
                        on_val,
                    )
                    if col_match and {col_match.group(1), col_match.group(2)} == {table_a, table_b}:
                        cleaned_existing.append({"on": col_match.group(0), "join_type": jp.get("join_type", "LEFT")})
                    # 否则丢弃脏数据
                elif "confidence" in on_val.lower():
                    # 去掉 confidence 标注
                    clean_on = re.sub(r"\s*\(confidence.*$", "", on_val).strip()
                    cleaned_existing.append({"on": clean_on, "join_type": jp.get("join_type", "LEFT")})
                else:
                    cleaned_existing.append(jp)
            existing_join_paths = cleaned_existing
            existing_on_set = {jp.get("on", "") for jp in existing_join_paths}
            for jp in join_paths:
                if jp.get("on", "") not in existing_on_set:
                    existing_join_paths.append(jp)
                    existing_on_set.add(jp.get("on", ""))
            merged_join_paths = existing_join_paths

            # 重建 content（含追加新场景 + 保留 JOIN 路径等）
            new_content = _build_linkage_content(
                table_a, table_b, state, direct_join_pairs, existing_scenes
            )

            # 合并 scenes: 已有 + 新问题 (去重)
            merged_scenes = list(existing_scenes)
            if state.question and state.question not in merged_scenes:
                merged_scenes.append(state.question)

            # 合并 aggregation: 优先用新值, 否则清洗旧值
            existing_agg = existing.get("aggregation", "")
            final_aggregation = aggregation or (existing_agg if existing_agg else "")
            # 清洗过长的 aggregation (旧数据可能是 LLM 原始长文本)
            if final_aggregation and len(final_aggregation) > 10:
                final_aggregation = _clean_aggregation(final_aggregation)

            mem_store.save_memory(
                name=f"linkage-{table_a}-{table_b}",
                description=f"表 {table_a} 和 {table_b} 的共现经验",
                content=new_content,
                memory_type="linkage",
                mem_id=existing["id"],
                extra_metadata={
                    "co_occurrence": new_co,
                    "tables": sorted([table_a, table_b]),
                    "join_paths": merged_join_paths,
                    "scenes": merged_scenes,
                    **({"aggregation": final_aggregation} if final_aggregation else {}),
                    **({"conversation_id": conv_id} if conv_id else {}),
                }
            )
            logger.info(f"更新 linkage 记忆 {table_a}-{table_b}: co_occurrence={new_co}")
        else:
            # 新表对：创建 linkage 记忆
            # 防竞态: save_memory 前再次检查 (并发对话可能已创建)
            recheck = mem_store.get_linkage_memory(table_a, table_b)
            if recheck:
                # 竞态命中: 走更新路径 (递归调用, 最多重试 1 次)
                logger.warning(f"竞态检测: {table_a}-{table_b} 已存在, 走更新路径")
                # 把 existing 赋值后重新走上面的更新逻辑
                existing = recheck
                new_co = existing["co_occurrence"] + 1
                existing_fm_scenes = existing.get("scenes")
                original_content = mem_store.read_memory(existing["id"]) or ""
                existing_scenes = _extract_existing_scenes(original_content, existing_fm_scenes)
                existing_join_paths = existing.get("join_paths") or []
                existing_on_set = {jp.get("on", "") for jp in existing_join_paths}
                for jp in join_paths:
                    if jp.get("on", "") not in existing_on_set:
                        existing_join_paths.append(jp)
                        existing_on_set.add(jp.get("on", ""))
                new_content = _build_linkage_content(
                    table_a, table_b, state, direct_join_pairs, existing_scenes
                )
                merged_scenes = list(existing_scenes)
                if state.question and state.question not in merged_scenes:
                    merged_scenes.append(state.question)
                existing_agg = existing.get("aggregation", "")
                final_aggregation = aggregation or (existing_agg if existing_agg else "")
                if final_aggregation and len(final_aggregation) > 10:
                    final_aggregation = _clean_aggregation(final_aggregation)
                mem_store.save_memory(
                    name=f"linkage-{table_a}-{table_b}",
                    description=f"表 {table_a} 和 {table_b} 的共现经验",
                    content=new_content,
                    memory_type="linkage",
                    mem_id=existing["id"],
                    extra_metadata={
                        "co_occurrence": new_co,
                        "tables": sorted([table_a, table_b]),
                        "join_paths": existing_join_paths,
                        "scenes": merged_scenes,
                        **({"aggregation": final_aggregation} if final_aggregation else {}),
                        **({"conversation_id": conv_id} if conv_id else {}),
                    }
                )
                logger.info(f"竞态更新 linkage 记忆 {table_a}-{table_b}: co_occurrence={new_co}")
            else:
                content = _build_linkage_content(table_a, table_b, state, direct_join_pairs)
                scenes = [state.question] if state.question else []
                mem_store.save_memory(
                    name=f"linkage-{table_a}-{table_b}",
                    description=f"表 {table_a} 和 {table_b} 的共现经验",
                    content=content,
                    memory_type="linkage",
                    extra_metadata={
                        "co_occurrence": 1,
                        "tables": sorted([table_a, table_b]),
                        "join_paths": join_paths,
                        "scenes": scenes,
                        **({"aggregation": aggregation} if aggregation else {}),
                        **({"conversation_id": conv_id} if conv_id else {}),
                    }
                )
                logger.info(f"创建 linkage 记忆 {table_a}-{table_b}")


def persist_metric_feedback(
    mem_store,
    state,
    semantic_content,
    conv_id: str | None = None,
) -> list[dict]:
    """运行时指标反哺 — 查询成功后校验 SQL 与指标的关系。

    闭环逻辑:
      1. SQL 命中已知 metric → co_occurrence += 1 (指标越用越可信)
      2. SQL 聚合公式与已知 metric 公式偏差 → 写 metric_suggestion 记忆 (人工校正入口)
      3. SQL 含新指标模式 (未在语义层定义的聚合) → 写 metric_suggestion 记忆 (发现新指标)

    设计原则:
      - 不缝合记忆系统的关键词召回 (指标信息走 schema_context, 不走 memory)
      - 不写死列名模式判断
      - 宁缺毋滥: 无法判断时跳过, 不写低质量 suggestion

    Args:
        mem_store: AgentMemoryStore 实例
        state: AgentState 实例，需包含 sql/current_tables/semantic_content
        semantic_content: SemanticModelContent 实例 (语义层完整内容)
        conv_id: 对话 ID (追溯来源)
    """
    import re as _re

    if not state.sql or not state.current_tables:
        return []

    # 收集当前查询涉及的表的所有已知指标
    known_metrics: dict[str, dict] = {}  # metric_name → {model, metric}
    if semantic_content and hasattr(semantic_content, "models"):
        for model in semantic_content.models:
            if model.name not in state.current_tables:
                continue
            for m in model.metrics:
                known_metrics[m.name] = {"model": model.name, "metric": m}

    # 从 SQL 提取聚合函数 (简化匹配: SUM(col), COUNT(...), AVG(col) 等)
    agg_pattern = _re.compile(
        r"\b(SUM|COUNT|AVG|MAX|MIN)\s*\(\s*([^)]+)\s*\)",
        _re.IGNORECASE,
    )
    sql_aggs: list[tuple[str, str]] = []  # (func, col)
    for match in agg_pattern.finditer(state.sql):
        sql_aggs.append((match.group(1).upper(), match.group(2).strip()))

    if not sql_aggs:
        return []

    # 记录 co_occurrence 变更 (供调用方持久化)
    co_occurrence_updates: list[dict] = []  # [{table_name, metric_name, delta, source, type}]

    # 1. SQL 命中已知 metric → co_occurrence += 1
    # 匹配逻辑: SQL 的聚合函数+列名与 metric 的 formula 有交集
    for metric_name, info in known_metrics.items():
        metric = info["metric"]
        formula_upper = metric.formula.upper()
        # 简化匹配: formula 中包含 SQL 的聚合表达式
        matched = False
        for func, col in sql_aggs:
            agg_expr = f"{func}({col})".upper()
            # 同时匹配带/不带空格的版本
            if agg_expr in formula_upper or f"{func} ( {col} )" in formula_upper:
                matched = True
                break
        if matched:
            # 不再在内存中 +1 (避免并发竞态丢失更新),
            # 改为返回 delta=1, 由 _persist_co_occurrence 在 DB 层原子递增
            co_occurrence_updates.append({
                "table_name": info["model"],
                "metric_name": metric_name,
                "delta": 1,
                "source": metric.source,
                "type": metric.type,
            })
            logger.info(
                "metric_feedback: 指标 %s 命中, delta=1",
                metric_name,
            )

    # 2. 发现新指标模式: SQL 含聚合但不在已知指标中
    # 只对单表聚合生成 suggestion (多表 JOIN 的聚合太复杂, 容易误判)
    if len(state.current_tables) == 1 and known_metrics:
        table_name = state.current_tables[0]
        for func, col in sql_aggs:
            agg_expr = f"{func}({col})"
            # 检查这个聚合是否已被已知指标覆盖
            covered = False
            for info in known_metrics.values():
                if agg_expr.upper() in info["metric"].formula.upper():
                    covered = True
                    break
            if not covered:
                # 写 metric_suggestion 记忆 (供人工在语义层页面采纳)
                try:
                    suggestion_name = f"metric-suggestion-{table_name}-{func.lower()}-{col.replace('.', '_')}"
                    # 检查是否已有同名 suggestion (防重复)
                    existing = mem_store.list_memories()
                    if any(m.get("name") == suggestion_name for m in existing):
                        continue
                    mem_store.save_memory(
                        name=suggestion_name,
                        description=f"表 {table_name} 的新指标建议: {agg_expr}",
                        content=f"## 新指标建议\n\n"
                                f"表: {table_name}\n"
                                f"聚合: {agg_expr}\n"
                                f"来源 SQL: {state.sql[:200]}\n"
                                f"来源问题: {state.question[:100]}\n\n"
                                f"建议在语义层页面添加此指标定义。",
                        memory_type="metric_suggestion",
                        extra_metadata={
                            "table": table_name,
                            "formula": agg_expr,
                            **({"conversation_id": conv_id} if conv_id else {}),
                        },
                    )
                    logger.info(
                        "metric_feedback: 新指标建议 %s (表=%s)",
                        suggestion_name, table_name,
                    )
                except Exception as e:
                    logger.warning("metric_feedback: 写 suggestion 失败: %s", e)

    return co_occurrence_updates
