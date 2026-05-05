"""RAG 检索节点：从数据库获取 metadata 并检索相关表结构。"""
from sqlalchemy import select

from app.ai.nodes.shared_utils import append_all_table_names
from app.core.logging import get_logger
from app.db.models import MetadataConfig
from app.db.session import async_session_factory
from app.services.rag_schema_service import get_rag_schema

logger = get_logger(__name__)


async def rag_retrieval_node(state: dict) -> dict:
    """根据问题和 datasource_id 检索相关表结构并返回 schema_context。

    流程：
    1. 从数据库查询 MetadataConfig 获取 raw metadata JSON
    2. 调用 get_rag_schema 进行 RAG 检索（向量/关键词匹配 + 列剪枝）
    3. 附加完整表名列表，防止 LLM 捏造表名
    4. 返回 schema_context 供后续 SQL 生成节点使用

    优雅降级：如果 metadata 不存在或 RAG 无结果，返回空字符串，
    后续节点可以回退到无 schema 模式。
    """
    question = state.get("question", "")
    datasource_id = state.get("datasource_id", "")
    tenant_id = state.get("tenant_id", "")

    if not datasource_id:
        logger.warning("RAG node: missing datasource_id")
        return {"schema_context": "", "raw_metadata": ""}

    if not tenant_id:
        logger.warning("RAG node: missing tenant_id — skipping retrieval for security")
        return {"schema_context": "", "raw_metadata": ""}

    # Fetch metadata from DB
    try:
        async with async_session_factory() as db:
            query = select(MetadataConfig).where(
                MetadataConfig.datasource_id == datasource_id,
                MetadataConfig.tenant_id == tenant_id,
            )
            config_result = await db.execute(query)
            config = config_result.scalar_one_or_none()
            raw_metadata = config.config if config else ""
    except Exception as e:
        logger.warning("RAG node: failed to fetch metadata: %s", e)
        raw_metadata = ""

    if not raw_metadata:
        logger.info("RAG node: no metadata for datasource %s", datasource_id)
        return {"schema_context": "", "raw_metadata": ""}

    # RAG retrieval
    schema_context = await get_rag_schema(question, raw_metadata, datasource_id=datasource_id)

    # Append all table names for LLM reference
    schema_context = append_all_table_names(schema_context, raw_metadata)

    logger.info(
        "RAG node: retrieved schema_context (%d chars) for question: %s",
        len(schema_context),
        question[:80],
    )
    logger.info("RAG node schema_context preview:\n%s", schema_context[:1000])
    return {"schema_context": schema_context, "raw_metadata": raw_metadata}
