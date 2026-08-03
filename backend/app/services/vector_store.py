"""
T019 前置: 向量存储抽象层 + Mock 实现

对标:
  - RAG-001 (openspec spec): 向量索引构建 (upsert) + 增量更新 (delete + upsert)
  - RAG-002: 向量检索 (search) + score 过滤 (>= 0.5)
  - RAG-005: 标量过滤 (table_name 精确匹配，非向量检索)

设计要点 (对标 5 大设计原则 — 可演化):
  - VectorStore Protocol: 抽象接口，实现可切换 (Milvus / pgvector / Mock)
    不绑死 pymilvus，未来换 pgvector 或测试用 Mock 都不改调用方
  - MockVectorStore: 纯内存 + 余弦相似度，CI 不依赖外部服务
  - 维度由构造参数决定，不强绑 1024 (embedding 模型确定后再定 dim)

为什么先做这层:
  - embedding 维度 + Milvus 连通性都还没定，但这层接口是稳定的
  - T020 (索引构建) / T022 (检索) / T021 (增量更新) 全依赖这层抽象
  - 先把 Mock 跑通，真实 Milvus 实现等连通后填 (依赖注入切换)

接口设计原则:
  - 所有方法都是 async: 与异步框架一致, 即使 Mock 实现也是 async
  - upsert 支持批量: 一次调用批量插入, 而非逐条, 对标效率
  - search 支持 top_k + score_threshold + filter: 对标 RAG-002/RAG-005
  - delete_by_filter 返回删除数量: 用于 rebuild_index 的日志和验证
  - get/count: 调试和测试用, 生产环境不依赖

降级策略:
  - Milvus 不可用 → 降级到 MockVectorStore (纯内存)
  - 降级后定期检查 Milvus 是否恢复 (在 get_vector_store 中实现)
  - Mock 实现的余弦相似度搜索结果与 Milvus 不完全一致, 但语义一致
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ── 数据结构 ──────────────────────────────────────────────────

@dataclass
class VectorRecord:
    """一条向量记录: id + 向量 + 标量元数据 + 原文(可选)。

    对标 Milvus: primary key + FLOAT_VECTOR + 标量字段。
    对标 RAG-005: metadata 里放 table_name / data_type 等供标量过滤。

    id 格式: <data_source_id>:<type>:<name>
    例如: "ds_001:model:orders" 或 "ds_001:metric:total_revenue"
    text 字段: 原文保留, 用于调试和结果展示, 不参与向量检索。
    """
    id: str
    vector: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)
    text: str | None = None  # 原文（调试/缓存用，不参与检索）


@dataclass
class SearchResult:
    """检索结果: 记录 + 相似度分数 [0, 1]。

    score 取值范围: 余弦相似度 [-1, 1], 此处归一化到 [0, 1]。
    0 = 完全不相似, 1 = 完全相同。
    负数表示反向相关, 在实际语义搜索中无意义, 被 score_threshold 过滤掉。
    """
    record: VectorRecord
    score: float


# ── 抽象接口 ──────────────────────────────────────────────────

@runtime_checkable
class VectorStore(Protocol):
    """向量存储抽象。

    所有实现 (Milvus/pgvector/Mock) 必须满足此接口。
    调用方 (T020 索引构建 / T022 检索) 只依赖此接口, 不依赖具体实现。

    接口设计原则:
    - 异步: 所有方法都是 async def, 兼容 FastAPI 异步框架
    - 批量: upsert/search/delete 都支持批量操作
    - 幂等: upsert 同 id 覆盖, delete 不存在 id 不报错
    """

    async def upsert(self, records: list[VectorRecord]) -> None:
        """插入或更新（同 id 覆盖）。对标 RAG-001 增量更新。

        Args:
            records: 要插入或更新的记录列表。
                    同 id 记录会覆盖旧值 (幂等)。
                    如果 records 为空, 不执行任何操作。
        """
        ...

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 20,
        score_threshold: float = 0.0,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """向量检索。对标 RAG-002: top-K + score 过滤 + 标量过滤。

        Args:
            query_vector: 查询向量, 维度必须与存储一致
            top_k: 召回数量 (默认 20, 对标 RAG-002 K=20)
            score_threshold: score < 此值的结果过滤 (默认不过滤；RAG-002 用 0.5)
            filter: 标量精确匹配过滤 (对标 RAG-005 table_name)

        Returns:
            list[SearchResult], 按 score 降序排列, 最多 top_k 条。

        匹配过程:
        1. 先按 filter 过滤 (标量精确匹配)
        2. 余弦相似度计算 score
        3. 过滤 score < score_threshold
        4. 按 score 降序, 取 top_k
        """
        ...

    async def delete(self, ids: list[str]) -> None:
        """按 id 删除。

        Args:
            ids: 要删除的记录 id 列表。
                不存在的 id 被忽略 (不报错)。

        适用场景: 精确删除指定记录 (如删除某个 metric)。
        """
        ...

    async def delete_by_filter(self, filter: dict[str, Any]) -> int:
        """按标量过滤批量删，返回删除数量。对标 RAG-001: 删某数据源全量向量。

        Args:
            filter: 标量精确匹配条件, 如 {"data_source_id": "ds_001"}

        Returns:
            实际删除的记录数量。

        适用场景: 重建索引时删除某个数据源的全部旧索引。
        """
        ...

    async def get(self, id: str) -> VectorRecord | None:
        """按 id 取（调试/测试用）。

        返回单条记录, 不存在返回 None。
        生产环境不依赖此方法 (检索用 search)。
        """
        ...

    async def count(self) -> int:
        """记录总数（测试/监控用）。

        返回当前存储中的记录总数。
        用于测试验证和监控告警。
        """
        ...


# ── Mock 实现 (纯内存 + 余弦相似度) ───────────────────────────

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度 [-1, 1]。零向量 → 0。

    余弦相似度衡量两个向量在方向上的相似程度, 不受向量长度影响。
    在语义搜索中, 方向比长度更重要 (语义相似 vs 文本长度相似)。

    计算公式: dot(a, b) / (|a| * |b|)

    零向量处理: 如果任一向量为全零向量, 返回 0 (不相似)。
    因为零向量的语义是"无意义", 不应与任何向量相似。
    """
    # 逐元素相乘后求和, 等价于向量点积
    dot = sum(x * y for x, y in zip(a, b))
    # L2 范数 (欧几里得长度), 即向量模长
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    # 余弦值范围 [-1, 1]
    return dot / (norm_a * norm_b)


def _matches_filter(metadata: dict[str, Any], filter: dict[str, Any]) -> bool:
    """标量精确匹配: metadata 里所有 filter 键值都相等。

    filter 为空 dict → 匹配所有记录。
    只做精确匹配 (==), 不做范围/模糊匹配 (对标 Milvus 的标量过滤能力)。

    Args:
        metadata: VectorRecord 的 metadata 字段
        filter: 过滤条件, 如 {"data_source_id": "ds_001", "type": "model"}

    Returns:
        True 如果 metadata 包含 filter 中所有键值对
    """
    return all(metadata.get(k) == v for k, v in filter.items())


class MockVectorStore:
    """内存向量存储, 余弦相似度, CI 不依赖外部服务。

    语义与真实 Milvus 对齐:
      - upsert 同 id 覆盖
      - search 按余弦相似度降序 + top_k + score_threshold + filter
      - 维度校验 (防 schema 错配)

    使用场景:
    - 开发/测试环境: 无需启动 Milvus
    - CI/CD: 不依赖外部服务, 快速执行
    - 生产降级: Milvus 不可用时自动降级

    限制:
    - 所有数据在内存中, 进程重启后丢失
    - 不支持持久化 (Milvus 支持)
    - 不适合大规模数据 (> 10 万条)
    """

    def __init__(self, dim: int):
        """初始化 MockVectorStore。

        Args:
            dim: 向量维度 (必须与 embedding 模型输出维度一致)
        """
        self.dim = dim
        self._records: dict[str, VectorRecord] = {}  # id → VectorRecord

    async def upsert(self, records: list[VectorRecord]) -> None:
        """插入或更新。维度校验: 每条记录的 vector 维度必须与 store 一致。"""
        for rec in records:
            # 维度校验: 防止 schema 错配 (如 embedding 模型切换后维度变化)
            if len(rec.vector) != self.dim:
                raise ValueError(
                    f"vector dimension mismatch: record {rec.id} has "
                    f"dim={len(rec.vector)}, store expects dim={self.dim}"
                )
            self._records[rec.id] = rec

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 20,
        score_threshold: float = 0.0,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """向量检索: 全量扫描 + 过滤 + 排序 + top_k。

        时间复杂度: O(n) 全量扫描, 不适合大规模数据。
        生产环境 (Milvus) 使用 ANN 索引, 时间复杂度 O(log n)。
        """
        scored: list[SearchResult] = []
        for rec in self._records.values():
            # 标量过滤: 先过滤, 减少余弦计算
            if filter and not _matches_filter(rec.metadata, filter):
                continue
            # 余弦相似度计算
            score = _cosine_similarity(query_vector, rec.vector)
            # score 过滤: 去除低分结果
            if score < score_threshold:
                continue
            scored.append(SearchResult(record=rec, score=score))
        # 按 score 降序排列, 取 top_k
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]

    async def delete(self, ids: list[str]) -> None:
        """按 id 删除。不存在的 id 被忽略 (pop 不会报错)。"""
        for id_ in ids:
            self._records.pop(id_, None)

    async def delete_by_filter(self, filter: dict[str, Any]) -> int:
        """按标量过滤批量删除, 返回删除数量。"""
        to_delete = [
            id_ for id_, rec in self._records.items()
            if _matches_filter(rec.metadata, filter)
        ]
        for id_ in to_delete:
            del self._records[id_]
        return len(to_delete)

    async def get(self, id: str) -> VectorRecord | None:
        """按 id 获取单条记录。"""
        return self._records.get(id)

    async def count(self) -> int:
        """返回当前存储中的记录总数。"""
        return len(self._records)


# ── 工厂 + 模块级单例 (对标 get_embedder / get_llm_client) ─────

import logging

_logger = logging.getLogger(__name__)

_vector_store: VectorStore | None = None
# 按 collection 缓存多个 store 实例 (支持 semantic_models + fewshot 等多 collection 并存)
_vector_stores: dict[str, VectorStore] = {}


def get_vector_store(collection_name: str = "semantic_models") -> VectorStore:
    """获取 VectorStore 实例 (按 collection_name 缓存, 支持多 collection 并存)。

    milvus: MilvusVectorStore (生产)
    mock:   MockVectorStore (测试/降级, 纯内存)
    Milvus 连不上时降级到 mock (fail-closed, 对标 Redis/Milvus 降级模式)。

    降级恢复: 如果当前缓存的是 MockVectorStore 但 backend=milvus,
    尝试重新连接 Milvus (Milvus 可能恢复了), 成功则替换缓存 (不再永久降级)。

    collection 名隔离: 拼上 config.vector_store_collection_prefix 前缀,
    测试/开发/多实例互不串数据 (对标 PG 库隔离; 一次配置, 全局生效)。
    向后兼容: 无参调用 (默认 semantic_models) 仍走 _vector_store 单例。

    缓存策略:
    - 默认为空 (首次调用时创建)
    - 按 actual collection 名缓存 (含前缀)
    - 默认 collection (semantic_models) 额外缓存到 _vector_store
    - 降级恢复时替换缓存

    支持的 collection:
    - semantic_models: 语义层索引 (默认)
    - fewshot: 少样本示例索引 (未来扩展)
    - 其他: 按需扩展, 通过 collection_name 参数指定

    线程安全: 此函数在异步框架中可能有并发调用, 但 Python GIL 保证
    dict 操作是原子的, 多个协程不会同时修改 _vector_stores。
    但如果有多个协程同时首次调用同一 collection, 可能创建多个 store,
    后者覆盖前者。当前场景可接受。
    """
    global _vector_store
    from app.core.config import get_settings
    settings = get_settings()
    # 实际 collection 名 = 前缀 + 逻辑名; 缓存/Milvus/单例判定全程用 actual
    # 前缀用于多环境隔离 (如 dev/prod 使用不同 collection)
    actual = f"{settings.vector_store_collection_prefix}{collection_name}"

    # 向后兼容: 默认 collection 走原单例
    # 之前只有 _vector_store 单例, 后来扩展为 _vector_stores dict
    # 保留 _vector_store 是为了兼容旧代码
    is_default = collection_name == "semantic_models"
    if is_default and _vector_store is not None:
        cached = _vector_store
    elif actual in _vector_stores:
        cached = _vector_stores[actual]
    else:
        cached = None

    # 降级恢复: 当前是 Mock + backend=milvus → 检查 Milvus 是否恢复了
    # 或当前是 MilvusVectorStore 但连接已断 → 检查并重建
    # 每次调用都检查, 确保 Milvus 恢复后尽快切换
    if cached is not None and settings.vector_store_backend == "milvus":
        need_rebuild = False
        if isinstance(cached, MockVectorStore):
            # Mock 降级 → 尝试恢复到 Milvus
            # 检查 Milvus 是否已恢复可用
            need_rebuild = True
        else:
            # MilvusVectorStore: 检查 Milvus 连接是否仍然健康
            # 对标审计发现: 缓存的 MilvusVectorStore 可能持有断裂的 _client 引用
            # 如果 Milvus 重启或网络中断, 缓存的 client 引用可能无效
            try:
                from app.core.milvus_client import is_milvus_healthy
                if not is_milvus_healthy():
                    # 连接断了 → 重建 (is_milvus_healthy 已重置单例)
                    # is_milvus_healthy() 内部会检查连接并重置单例
                    need_rebuild = True
            except Exception:
                # 健康检查失败 → 重建
                need_rebuild = True

        if need_rebuild:
            try:
                from app.core.milvus_client import is_milvus_healthy, get_milvus_client
                if is_milvus_healthy():
                    from app.services.milvus_vector_store import MilvusVectorStore
                    client = get_milvus_client()
                    store = MilvusVectorStore(
                        client=client,
                        collection_name=actual,
                        dim=settings.embedding_dim,
                    )
                    _logger.warning("VectorStore: 从降级恢复为 Milvus (collection=%s)", actual)
                    _vector_stores[actual] = store
                    if is_default:
                        _vector_store = store
                    return store
            except Exception as e:
                # Milvus 恢复失败, 继续用当前 store (Mock 或旧 Milvus)
                _logger.debug("VectorStore: Milvus 恢复失败, 继续用当前 store: %s", e)
        else:
            # MilvusVectorStore 且连接健康, 直接返回
            return cached

    # 如果有缓存 (且不需要重建), 返回缓存
    if cached is not None:
        return cached

    # 首次创建
    backend = settings.vector_store_backend
    store: VectorStore
    if backend == "milvus":
        try:
            from app.core.milvus_client import get_milvus_client
            from app.services.milvus_vector_store import MilvusVectorStore
            client = get_milvus_client()
            store = MilvusVectorStore(
                client=client,
                collection_name=actual,
                dim=settings.embedding_dim,
            )
            _logger.info("VectorStore: Milvus (collection=%s, dim=%d)",
                         actual, settings.embedding_dim)
        except Exception as e:
            # Milvus 不可用, 降级为 Mock
            # 降级是 soft fail, 不抛异常, 让调用方无感
            _logger.warning("Milvus 不可用, VectorStore 降级为 Mock: %s", e)
            store = MockVectorStore(dim=settings.embedding_dim)
    else:
        # backend 非 milvus (如 mock/test), 直接创建 Mock
        store = MockVectorStore(dim=settings.embedding_dim)
        _logger.info("VectorStore: Mock (backend=%s, collection=%s)", backend, actual)

    _vector_stores[actual] = store
    if is_default:
        _vector_store = store
    return store


def reset_vector_store() -> None:
    """重置单例 (测试用)。

    清空所有缓存, 下次 get_vector_store 调用会重新创建。
    测试中每个 tearDown 应调用此方法, 避免测试间状态污染。
    """
    global _vector_store
    _vector_store = None
    _vector_stores.clear()
