"""Schema 选择节点（Schema Selection Node）—— 两步 LLM 交互，替代 RAG 检索。

═══════════════════════════════════════════════════════════════
文件用途
═══════════════════════════════════════════════════════════════
这是 LangGraph AI 管道中的核心节点之一。当用户提出自然语言问题时，
数据库可能有几十甚至上百张表、上千个字段。如果把所有表结构都塞给
SQL 生成节点，LLM 会"信息过载"，生成的 SQL 质量急剧下降。

本节点的解决方案：**两步 LLM 交互**（Two-Step LLM Interaction）

  Step 1 — 选表（_select_tables）
    输入：用户问题 + 所有表名（只有名字和说明，不含字段）
    输出：相关的表名列表
    原因：表名数量少（通常 < 30），LLM 能准确判断相关性

  Step 2 — 选字段（_select_columns）
    输入：用户问题 + Step 1 选中的表的完整字段列表
    输出：每张表需要的字段名
    原因：字段数量多，需要先缩小范围再精确选择

最后用 _build_schema_context 把选中的表+字段拼成结构化文本，
作为 schema_context 传给下游的 SQL 生成节点。

═══════════════════════════════════════════════════════════════
与其他文件的关系
═══════════════════════════════════════════════════════════════
- shared_utils.py  → get_llm() 创建 LLM 实例
- db/models.py     → MetadataConfig ORM 模型，存储数据源的元数据
- db/session.py    → async_session_factory 异步数据库会话工厂
- core/config.py   → settings 全局配置（模型名、温度等）
- 上游节点         → intent_node 提供 question / datasource_id / tenant_id
- 下游节点         → sql_generation_node 消费 schema_context

═══════════════════════════════════════════════════════════════
LangGraph 概念速查
═══════════════════════════════════════════════════════════════
- 节点（Node）：图中的一个处理步骤，接收 state 字典，返回更新
- 状态（State）：在节点之间传递的字典，LangGraph 自动合并返回值
- ainvoke：LangChain LLM 的异步调用方法，返回 AIMessage 对象
"""
import json  # json：将数据库中存储的 JSON 字符串解析为 Python 字典

from app.ai.nodes.shared_utils import get_llm  # get_llm：统一创建 LLM 实例的工厂函数，封装了模型名、API Key 等配置
from app.core.config import settings  # settings：全局配置对象，从 .env 环境变量加载
from app.core.logging import get_logger  # get_logger：项目统一的日志器工厂，自动附加模块名
from app.db.models import MetadataConfig  # MetadataConfig：SQLAlchemy ORM 模型，对应 metadata_configs 表，存储数据源的元数据（表结构、关联关系等）
from app.db.session import async_session_factory  # async_session_factory：异步数据库会话工厂，用于创建 AsyncSession
from sqlalchemy import select  # select：SQLAlchemy 2.0 风格的查询构建函数

logger = get_logger(__name__)  # __name__ 即 "app.ai.nodes.schema_selection"，日志中可追踪来源

# ═══════════════════════════════════════════════════════════════
# 系统提示词（System Prompt）
# ───────────────────────────────────────────────────────────────
# LLM 调用需要两条消息：system（角色设定）+ human（具体任务）。
# 把提示词定义为模块级常量，避免每次调用都重新创建字符串，
# 也方便集中管理和修改。
# ═══════════════════════════════════════════════════════════════

TABLE_SELECTION_SYSTEM = """你是一个数据库专家。根据用户问题，从以下表列表中选出相关的表。

要求:
1. 只选择确实需要的表，不要多选
2. 考虑表之间的关联关系，如果需要 JOIN 就把关联表也选上
3. 每行输出一个表名，不要编号，不要解释
4. 只输出表名，不要其他内容"""
# ↑ 关键设计：要求 LLM 只输出表名，每行一个。
#   这种"结构化输出"比让 LLM 输出 JSON 更可靠——
#   JSON 格式容易出错（漏括号、多逗号），而逐行表名解析更鲁棒。

COLUMN_SELECTION_SYSTEM = """你是一个数据库专家。根据用户问题和选中的表，从字段列表中选出需要的字段。

要求:
1. 必须包含 JOIN 所需的外键字段（如 xxx_id）
2. 必须包含主键字段
3. 包含问题中明确提到的字段
4. 包含 GROUP BY / WHERE / ORDER BY 可能需要的字段
5. 每行输出: 表名.字段名，不要编号，不要解释
6. 只输出需要的字段，不要其他内容"""
# ↑ 输出格式 "表名.字段名"（如 orders.order_id），
#   后续用 rsplit(".", 1) 解析，比 JSON 更容错。


async def _select_tables(question: str, metadata: dict) -> list[str]:
    """Step 1: 让 LLM 从所有表中选择与用户问题相关的表。

    参数:
        question: 用户的自然语言问题，如"上个月销售额最高的产品是什么"
        metadata: 数据源的元数据字典，包含:
            - models: list[dict] — 所有数据模型（表）的信息
            - relationships: list[dict] — 表之间的关联关系
    返回:
        list[str] — LLM 选中的表名列表，如 ["orders", "products"]

    Python 提示:
        - async def 定义异步协程，调用时需要 await
        - list[str] 是 Python 3.9+ 的类型注解语法，等价于 List[str]
    """
    models = metadata.get("models", [])  # .get(key, default) 安全取值，key 不存在时返回 [] 而非抛 KeyError
    rels = metadata.get("relationships", [])

    if not models:
        return []  # 防御性编程：没有表信息时直接返回空，避免后续 LLM 调用浪费 token

    # ── 构建轻量表列表 ──────────────────────────────────────
    # 关键设计：Step 1 只给 LLM 表名+说明，不给字段详情。
    # 原因：字段信息太长（一张表可能 50+ 字段），全塞进去
    #       LLM 会"注意力分散"，选表准确率反而下降。
    table_lines = []
    for m in models:
        if m.get("_deleted"):  # 跳过已软删除的表（_deleted 是数据模型管理中的逻辑删除标记）
            continue
        name = m.get("name", "?")
        # 优先取 description，没有则取 comment，都没有则为空字符串
        # or 的短路特性：第一个为真值就返回，否则继续往后
        desc = m.get("description", "") or m.get("comment", "") or ""
        line = f"- {name}"
        if desc:
            line += f" ({desc})"  # 有说明时附在表名后面，帮助 LLM 理解表的用途
        table_lines.append(line)

    # ── 添加关联关系 ──────────────────────────────────────
    # 关联关系帮助 LLM 判断 JOIN 需要哪些表。
    # 限制最多 20 条（rels[:20]），防止 prompt 过长。
    if rels:
        table_lines.append("")  # 空行分隔，提高可读性
        table_lines.append("表之间的关联关系:")
        for r in rels[:20]:
            ft = r.get("from_table", "")
            fc = r.get("from_column", "")
            tt = r.get("to_table", "")
            tc = r.get("to_column", "")
            if ft and fc and tt and tc:  # 四个字段都非空才输出，过滤掉不完整的关联记录
                table_lines.append(f"- {ft}.{fc} -> {tt}.{tc}")

    # ── 构建完整 prompt ──────────────────────────────────
    # chr(10) 是换行符 \n，用 join 把列表拼成多行文本
    prompt = f"""表列表:
{chr(10).join(table_lines)}

用户问题: {question}

请选出相关的表。"""

    # temperature=0.0 让 LLM 输出最确定性的结果（不做随机采样）
    # max_tokens=300 限制输出长度——选表只需几行表名，300 token 足够
    llm = get_llm(max_tokens=300, temperature=0.0)

    try:
        # ainvoke 是 LangChain LLM 的异步调用方法
        # 传入消息列表：system 设定角色，human 提供具体任务
        # 等价于 OpenAI Chat API 的 messages 参数
        response = await llm.ainvoke([
            ("system", TABLE_SELECTION_SYSTEM),
            ("human", prompt),
        ])
        text = response.content.strip()  # response.content 是 LLM 返回的文本

        # ── 解析 LLM 返回的表名 ──────────────────────────
        # 构建合法表名集合，用于验证 LLM 输出
        # 集合推导式 {expr for item in iterable if condition}，
        # 比列表推导式查找更快（O(1) vs O(n)），适合做成员检查
        table_names = {m["name"] for m in models if not m.get("_deleted")}
        selected = []
        for line in text.split("\n"):
            # lstrip("-0123456789.) *") 去掉 LLM 可能添加的前缀符号
            # 如 "- 1. orders" → "orders"，"* products" → "products"
            name = line.strip().lstrip("-0123456789.) *")
            # 双重检查：表名必须合法 + 不重复（用列表而非集合保持顺序）
            if name in table_names and name not in selected:
                selected.append(name)
        logger.info("Table selection: %s -> %s", question[:50], selected)
        return selected
    except Exception as e:
        # 防御性编程：LLM 调用可能因网络、限流等原因失败
        # 返回空列表而非抛异常，让流程继续（后续有兜底逻辑）
        logger.warning("Table selection failed: %s", e)
        return []


async def _select_columns(question: str, selected_tables: list[str], metadata: dict) -> dict[str, list[str]]:
    """Step 2: 让 LLM 从选中表的字段中挑选 SQL 生成所需的字段。

    参数:
        question: 用户的自然语言问题
        selected_tables: Step 1 选出的表名列表
        metadata: 数据源元数据字典（同 _select_tables）
    返回:
        dict[str, list[str]] — 表名到字段名列表的映射，
        如 {"orders": ["order_id", "product_id", "amount"], "products": ["product_id", "name"]}

    Python 提示:
        - dict[str, list[str]] 是 Python 3.9+ 的泛型类型注解
        - 字典推导式 {k: v for ...} 用于快速构建字典
    """
    models = metadata.get("models", [])
    # 构建表名→模型的映射字典，后续用 O(1) 查找代替 O(n) 遍历
    # 字典推导式：{key_expr: value_expr for item in iterable}
    model_map = {m["name"]: m for m in models}

    # ── 构建选中表的字段列表 ──────────────────────────────
    # 与 Step 1 不同，这里要展示每个字段的详细信息：
    # 类型、是否可空、主键/外键标记、注释——这些信息帮助 LLM
    # 判断哪些字段是 SQL 生成所必需的。
    col_lines = []
    for t in selected_tables:
        m = model_map.get(t)
        if not m:  # 防御：表名可能在 metadata 中不存在（数据不一致时）
            continue
        desc = m.get("description", "") or m.get("comment", "") or ""
        col_lines.append(f"表: {t} ({desc})")
        for c in m.get("columns", []):
            cname = c.get("name", "?")
            ctype = c.get("type", "?")
            ccomment = c.get("comment", "") or ""
            # 用标记 [主键] [外键] 让 LLM 识别关键字段
            pk = " [主键]" if c.get("primary") else ""
            # column_key 值含义：PRI=主键, FK=外键, MUL=非唯一索引（常用于外键列）
            fk = " [外键]" if c.get("column_key") in ("FK", "MUL") else ""
            nullable = "NULL" if c.get("nullable") else "NOT NULL"
            line = f"  - {cname} ({ctype}) {nullable}{pk}{fk}"
            if ccomment:
                line += f" — {ccomment}"  # 中文破折号，字段注释帮助 LLM 理解字段含义
            col_lines.append(line)
        col_lines.append("")  # 表之间空一行，提高 LLM 可读性

    if not col_lines:
        return {}  # 没有可用的字段信息，返回空字典

    prompt = f"""选中的表和字段:
{chr(10).join(col_lines)}

用户问题: {question}

请选出需要的字段。"""

    # max_tokens=500 比 Step 1 多，因为字段选择输出更长
    llm = get_llm(max_tokens=500, temperature=0.0)

    try:
        response = await llm.ainvoke([
            ("system", COLUMN_SELECTION_SYSTEM),
            ("human", prompt),
        ])
        text = response.content.strip()

        # ── 解析 "表名.字段名" 格式 ──────────────────────
        # 先初始化结果字典，每张表对应一个空列表
        # 字典推导式：{t: [] for t in selected_tables}
        result: dict[str, list[str]] = {t: [] for t in selected_tables}
        for line in text.split("\n"):
            line = line.strip().lstrip("-0123456789.) *")
            if "." not in line:  # 跳过不含点号的行（LLM 可能输出空行或说明文字）
                continue
            # rsplit(".", 1) 从右侧分割，最多分 1 次
            # 用 rsplit 而非 split：防止表名中包含点号（如 schema.table.col）
            parts = line.rsplit(".", 1)
            if len(parts) != 2:
                continue
            tname, cname = parts[0].strip(), parts[1].strip()
            # 只接受已知表的字段，忽略 LLM 幻觉（hallucination）产生的表名
            if tname in result and cname:
                result[tname].append(cname)

        logger.info("Column selection: %s", result)
        return result
    except Exception as e:
        # LLM 调用失败时返回空字典，后续 _build_schema_context 有兜底逻辑
        logger.warning("Column selection failed: %s", e)
        return {}


def _build_schema_context(selected_tables: list[str], selected_columns: dict[str, list[str]], metadata: dict) -> str:
    """根据 LLM 选出的表和字段，构建结构化的 schema 上下文文本。

    这个文本最终会作为 schema_context 传给 SQL 生成节点，
    告诉 LLM "你可以用哪些表、哪些字段来写 SQL"。

    参数:
        selected_tables: Step 1 选出的表名列表
        selected_columns: Step 2 选出的字段映射 {表名: [字段名]}
        metadata: 数据源元数据字典
    返回:
        str — 格式化的 schema 上下文文本，示例：
            可用的数据库表结构：
            表名: orders
            说明: 订单表
            字段:
              - order_id (INT) NOT NULL [主键]
              - product_id (INT) NOT NULL [外键] — 关联产品
            关联:
              - product_id -> products.product_id

    Python 提示:
        - 这是一个同步函数（没有 async），因为只做字符串拼接，不涉及 I/O
        - setdefault(key, default) 在 key 不存在时设置默认值并返回，存在则返回已有值
    """
    models = metadata.get("models", [])
    rels = metadata.get("relationships", [])
    model_map = {m["name"]: m for m in models}

    # ── 构建关联关系 map ──────────────────────────────────
    # rel_map: {表名: [关联信息列表]}
    # 双向记录：A→B 的关联同时记录在 A 和 B 下，
    # 这样无论从哪张表出发都能找到关联表
    rel_map: dict[str, list[dict]] = {}
    for r in rels:
        ft = r.get("from_table", "")
        fc = r.get("from_column", "")
        tt = r.get("to_table", "")
        tc = r.get("to_column", "")
        if ft and fc and tt and tc:
            # 正向：from_table → to_table
            rel_map.setdefault(ft, []).append({
                "column": fc,
                "referenced_table": tt,
                "referenced_column": tc,
            })
            # 反向：to_table → from_table（双向，方便从任一端查找关联）
            rel_map.setdefault(tt, []).append({
                "column": tc,
                "referenced_table": ft,
                "referenced_column": fc,
            })

    # ── 逐表构建输出文本 ──────────────────────────────────
    lines = ["可用的数据库表结构：", ""]
    for t in selected_tables:
        m = model_map.get(t)
        if not m:
            continue  # 防御：跳过 metadata 中不存在的表
        lines.append(f"表名: {t}")
        desc = m.get("description", "") or m.get("comment", "")
        if desc:
            lines.append(f"说明: {desc}")
        lines.append("字段:")

        cols = selected_columns.get(t, [])
        all_cols = m.get("columns", [])
        # 兜底逻辑：如果 LLM 没选任何字段（返回空列表），则使用该表全部字段
        # 宁可多给信息，也不能让 SQL 生成节点缺少字段而写出错误 SQL
        if not cols:
            cols = [c.get("name", "") for c in all_cols]

        # 确保主键和外键字段始终包含——即使 LLM 漏选了
        # 主键是 JOIN 的基础，外键是关联的桥梁，缺了 SQL 就写不对
        for c in all_cols:
            cname = c.get("name", "")
            if c.get("primary") or c.get("column_key") in ("PRI", "FK", "MUL"):
                if cname not in cols:  # 避免重复添加
                    cols.append(cname)

        # 按选中顺序输出字段（保持 LLM 的选择顺序，通常更符合查询逻辑）
        col_map = {c.get("name"): c for c in all_cols}  # 字段名→字段信息的映射
        for cname in cols:
            c = col_map.get(cname)
            if not c:
                continue  # 防御：跳过 metadata 中不存在的字段名
            nullable = "NULL" if c.get("nullable") else "NOT NULL"
            primary = " [主键]" if c.get("primary") else ""
            comment = f" — {c['comment']}" if c.get("comment") else ""
            lines.append(f"  - {c.get('name', '?')} ({c.get('type', 'unknown')}) {nullable}{primary}{comment}")

        # 如果该表有关联关系，附在字段列表后面
        if t in rel_map:
            lines.append("关联:")
            for rel in rel_map[t]:
                lines.append(
                    f"  - {rel['column']} -> {rel['referenced_table']}.{rel['referenced_column']}"
                )
        lines.append("")  # 表之间空行分隔

    return "\n".join(lines)


async def schema_selection_node(state: dict) -> dict:
    """LangGraph 节点（Node）：两步 LLM schema 选择，替代 RAG 检索。

    这是 LangGraph 图中的一个节点函数。LangGraph 的约定：
    - 输入：state 字典（由上游节点传入，包含 question、datasource_id 等）
    - 输出：返回一个字典，LangGraph 自动将其合并到 state 中

    执行流程:
        1. 从 state 取出 question / datasource_id / tenant_id
        2. 从数据库查询该数据源的元数据（MetadataConfig）
        3. Step 1: 调用 LLM 选出相关表
        4. Step 2: 调用 LLM 选出需要的字段
        5. 构建精确的 schema_context 文本
        6. 返回更新后的 state 字段

    参数:
        state: LangGraph 状态字典，包含:
            - question (str): 用户问题
            - datasource_id (str): 数据源 ID
            - tenant_id (str): 租户 ID（多租户隔离）
    返回:
        dict — 更新的 state 字段:
            - schema_context (str): 结构化的表结构描述，供 SQL 生成节点使用
            - raw_metadata (str): 原始 JSON 字符串，供其他节点参考
            - selected_tables (list[str]): 选中的表名列表
            - selected_columns (dict[str, list[str]]): 选中的字段映射

    Python 提示:
        - async with ... as db: 是异步上下文管理器，确保数据库会话用完自动关闭
        - dict 的合并：LangGraph 会把返回的 dict 浅合并到 state 中
    """
    question = state.get("question", "")
    datasource_id = state.get("datasource_id", "")
    tenant_id = state.get("tenant_id", "")

    # ── 参数校验 ──────────────────────────────────────────
    # 缺少 datasource_id 或 tenant_id 时无法查询元数据，
    # 返回空结果而非抛异常——保证管道不会因单个节点失败而中断
    if not datasource_id or not tenant_id:
        logger.warning("Schema selection: missing datasource_id or tenant_id")
        return {"schema_context": "", "raw_metadata": "", "selected_tables": [], "selected_columns": {}}

    # ── 从数据库获取元数据 ────────────────────────────────
    # MetadataConfig 表存储了每个数据源的表结构信息（JSON 格式）
    # 使用 async with 确保会话在使用后自动关闭，避免连接泄漏
    try:
        async with async_session_factory() as db:
            # SQLAlchemy 2.0 select() 风格查询
            # where() 添加过滤条件：按数据源 ID + 租户 ID 查询（多租户隔离）
            query = select(MetadataConfig).where(
                MetadataConfig.datasource_id == datasource_id,
                MetadataConfig.tenant_id == tenant_id,
            )
            # await 等待异步查询完成
            config_result = await db.execute(query)
            # scalar_one_or_none(): 期望 0 或 1 条结果
            #   - 0 条返回 None
            #   - 1 条返回对象
            #   - 多条抛异常（数据不一致的信号）
            config = config_result.scalar_one_or_none()
            raw_metadata = config.config if config else ""
    except Exception as e:
        logger.warning("Schema selection: failed to fetch metadata: %s", e)
        raw_metadata = ""

    if not raw_metadata:
        logger.info("Schema selection: no metadata for datasource %s", datasource_id)
        return {"schema_context": "", "raw_metadata": "", "selected_tables": [], "selected_columns": {}}

    # 将 JSON 字符串解析为 Python 字典
    metadata = json.loads(raw_metadata)

    # ── Step 1: 选择表 ────────────────────────────────────
    selected_tables = await _select_tables(question, metadata)

    # 兜底逻辑：如果 LLM 没选出任何表（可能因为问题太模糊或 LLM 出错），
    # 取前 5 张未删除的表作为备选——宁可多给信息，也不能让后续节点无表可用
    if not selected_tables:
        logger.warning("Schema selection: LLM returned no tables, using all as fallback")
        selected_tables = [m["name"] for m in metadata.get("models", [])[:5] if not m.get("_deleted")]

    # ── Step 2: 选择字段 ──────────────────────────────────
    selected_columns = await _select_columns(question, selected_tables, metadata)

    # ── 构建 schema context ───────────────────────────────
    schema_context = _build_schema_context(selected_tables, selected_columns, metadata)

    # ── 附加所有表名列表 ──────────────────────────────────
    # 关键防御：防止 SQL 生成节点中的 LLM "幻觉"（hallucination）出
    # 不存在的表名。在 schema_context 末尾列出所有合法表名，
    # 相当于给 LLM 一个"白名单"约束。
    all_table_names = [m["name"] for m in metadata.get("models", []) if not m.get("_deleted")]
    schema_context += f"\n可用表名: {', '.join(all_table_names)}\n"

    logger.info(
        "Schema selection: %d tables selected for question: %s",
        len(selected_tables),
        question[:80],  # 只记录问题前 80 字符，避免日志过长
    )
    logger.info("Schema context preview:\n%s", schema_context[:800])  # 只预览前 800 字符

    # 返回的字典会被 LangGraph 自动合并到 state 中
    # 只包含需要更新/新增的字段，不需要返回整个 state
    return {
        "schema_context": schema_context,
        "raw_metadata": raw_metadata,
        "selected_tables": selected_tables,
        "selected_columns": selected_columns,
    }