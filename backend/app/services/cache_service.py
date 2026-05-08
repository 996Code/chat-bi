"""
查询结果缓存服务 — 提供精确缓存和语义缓存两种模式，基于 Redis 实现。

核心概念：
  - 精确缓存（Exact Cache）：用问题的精确字符串哈希作为缓存键。
    相同的问题 + 数据源 + 租户 → 直接命中缓存，跳过 AI 推理和 SQL 执行。
    例如：用户问"上个月销售额"，第二次问同样的问题直接返回缓存。

  - 语义缓存（Semantic Cache）：用问题的"语义相似度"判断是否命中缓存。
    即使用户的措辞不同，只要意思相近就能命中。
    例如："上月营收"和"上个月的收入"语义相似，可以返回相同结果。
    实现方式：基于词重叠的简单相似度算法（_simple_similarity），
    不依赖 embedding 模型，轻量但精度有限。

  - 数据源索引（Datasource Index）：用 Redis Set 跟踪每个数据源的所有缓存键，
    方便按数据源批量清除缓存（当数据源配置变更时）。

缓存键的设计：
  - 精确缓存键：query:{sha256(tenant_id:question:datasource_id)}
  - 语义缓存键：semantic:{sha256(tenant_id:datasource_id)}
  - 数据源索引键：cache_index:{sha256(tenant_id:datasource_id)}
  - 所有键都包含 tenant_id，确保多租户数据隔离

TTL（缓存过期时间）策略：
  - 调用方显式指定 > 结果元数据中的 cache_ttl > 复杂查询用长 TTL > 默认 TTL
  - 复杂查询（返回行数 > 100）用更长的 TTL，因为结果更稳定

本文件与其它文件的关系：
  - app/core/config.py → 提供 Redis 连接配置和缓存 TTL 设置
  - app/core/redis_client.py → 提供 Redis 连接（get_redis）
  - app/ai/pipeline.py → AI 管道调用缓存，避免重复调用 LLM
  - app/api/query.py → 查询接口调用缓存
"""
import hashlib
import json
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

# ── 语义缓存配置 ──
SEMANTIC_CACHE_PREFIX = "semantic:"  # 语义缓存键的前缀
SEMANTIC_CACHE_TTL = settings.query_cache_ttl_seconds  # 语义缓存过期时间（秒）
SEMANTIC_CACHE_MAX_ENTRIES = 500  # 每个数据源最多保存的语义缓存条目数


# ── 内存中的缓存统计计数器 ──

class CacheStats:
    """缓存命中/未命中的统计计数器。

    为什么需要统计？
      - 监控缓存效果：命中率低说明缓存策略需要优化
      - 区分精确命中和语义命中：了解语义缓存的实际贡献
      - 追踪跳过次数：了解有多少查询结果因不符合条件而未缓存

    注意：这个计数器是进程内的（非 Redis），重启后归零。
    多实例部署时各实例的计数器独立，仅作粗略参考。
    """

    def __init__(self):
        self.hits: int = 0           # 精确缓存命中次数
        self.misses: int = 0         # 缓存未命中次数
        self.semantic_hits: int = 0  # 语义缓存命中次数
        self.sets: int = 0           # 缓存写入次数
        self.skipped: int = 0        # 跳过缓存的次数（查询失败或无数据行）

    def record_hit(self, semantic: bool = False):
        """记录一次缓存命中。如果 semantic=True，同时增加语义命中计数。"""
        self.hits += 1
        if semantic:
            self.semantic_hits += 1

    def record_miss(self):
        """记录一次缓存未命中。"""
        self.misses += 1

    def record_set(self):
        """记录一次缓存写入。"""
        self.sets += 1

    def record_skipped(self):
        """记录一次缓存跳过（查询结果不符合缓存条件）。"""
        self.skipped += 1

    def snapshot(self) -> dict:
        """返回当前统计数据的快照（字典格式）。

        返回值示例：
        {
            "hits": 100,
            "misses": 50,
            "semantic_hits": 20,
            "sets": 80,
            "skipped": 10,
            "hit_rate": 0.6667  # 命中率 = hits / (hits + misses)
        }
        """
        total = self.hits + self.misses
        hit_rate = self.hits / total if total > 0 else 0.0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "semantic_hits": self.semantic_hits,
            "sets": self.sets,
            "skipped": self.skipped,
            "hit_rate": round(hit_rate, 4),
        }


# 全局唯一的统计实例 — 整个进程共享
cache_stats = CacheStats()


def _cache_key(question: str, datasource_id: str, tenant_id: str = "") -> str:
    """生成精确缓存的键。

    将 tenant_id + question + datasource_id 拼接后做 SHA-256 哈希，
    生成固定长度的缓存键。这样做的好处：
      1. 避免超长问题文本导致 Redis 键过大
      2. 哈希是确定性的 — 相同输入总是产生相同输出
      3. question 先 strip().lower() — 规范化空白和大小写，
         "上月营收 " 和 "上月营收" 会命中同一个缓存

    参数：
        question: 用户的自然语言问题
        datasource_id: 数据源 ID
        tenant_id: 租户 ID（默认空字符串）

    返回值：
        str — 格式为 "query:{sha256_hash}"
    """
    raw = f"{tenant_id}:{question.strip().lower()}:{datasource_id}"
    return f"query:{hashlib.sha256(raw.encode()).hexdigest()}"


def _semantic_cache_key(question: str, datasource_id: str, tenant_id: str = "") -> str:
    """生成语义缓存的索引键。

    与精确缓存不同，语义缓存的键不包含 question 内容，
    因为问题内容通过索引中的多条记录来匹配，而不是键本身。

    参数：
        question: 用户问题（本函数不使用，保留参数为了接口一致性）
        datasource_id: 数据源 ID
        tenant_id: 租户 ID

    返回值：
        str — 格式为 "semantic:{sha256_hash}"
    """
    raw = f"{tenant_id}:{datasource_id}"
    return f"{SEMANTIC_CACHE_PREFIX}{hashlib.sha256(raw.encode()).hexdigest()}"


async def cache_get(question: str, datasource_id: str, tenant_id: str = "") -> dict | None:
    """从精确缓存中获取查询结果。

    参数：
        question: 用户的自然语言问题
        datasource_id: 数据源 ID
        tenant_id: 租户 ID

    返回值：
        dict | None — 缓存命中返回查询结果字典，未命中返回 None

    工作流程：
        1. 根据问题生成缓存键
        2. 从 Redis 读取缓存值
        3. 命中 → 反序列化 JSON → 返回结果
        4. 未命中 / Redis 异常 → 返回 None
    """
    key = _cache_key(question, datasource_id, tenant_id)
    try:
        redis = await get_redis()
        raw = await redis.get(key)
        if raw:
            cache_stats.record_hit()
            logger.info("Cache HIT for key %s", key[:16])
            return json.loads(raw)
        cache_stats.record_miss()
    except Exception as e:
        # Redis 异常时记录未命中，而不是抛出异常 — 缓存是可选的，不能影响主流程
        logger.warning("Redis cache get failed: %s", e)
        cache_stats.record_miss()
    return None


async def cache_set(question: str, datasource_id: str, result: dict, tenant_id: str = "", ttl: int | None = None) -> None:
    """将查询结果写入精确缓存。

    只缓存成功的、有数据行的查询结果。失败的查询或空结果不缓存，
    因为它们可能是临时错误，缓存会导致用户看不到修复后的结果。

    参数：
        question: 用户的自然语言问题
        datasource_id: 数据源 ID
        result: 查询结果字典，必须包含 success 和 rows 字段
        tenant_id: 租户 ID
        ttl: 缓存过期时间（秒），None 则使用默认策略

    写入流程：
        1. 检查结果是否符合缓存条件（成功 + 有数据行）
        2. 确定有效 TTL
        3. 写入 Redis（使用 setex 带过期时间）
        4. 将缓存键加入数据源索引（方便按数据源批量清除）
        5. 将问题加入语义缓存索引
    """
    if not result.get("success") or not result.get("rows"):
        cache_stats.record_skipped()
        return
    key = _cache_key(question, datasource_id, tenant_id)

    # Determine TTL: explicit > per-datasource metadata > default
    effective_ttl = ttl or _resolve_ttl(datasource_id, result)
    try:
        redis = await get_redis()
        # setex = SET with EXpiry — 原子操作，设置值的同时指定过期时间
        # json.dumps(result, default=str) — 将结果序列化为 JSON
        #   default=str 处理无法序列化的类型（如 datetime），转为字符串
        await redis.setex(key, effective_ttl, json.dumps(result, default=str))
        cache_stats.record_set()
        logger.info("Cached query result (TTL=%ds)", effective_ttl)

        # Index key in per-datasource set for targeted cache invalidation
        # 将缓存键加入数据源索引（Redis Set），方便按数据源批量清除缓存
        index_key = _datasource_index_key(datasource_id, tenant_id)
        await redis.sadd(index_key, key)
        await redis.expire(index_key, effective_ttl)

        # Also index for semantic lookup
        _add_to_semantic_index(question, key, datasource_id, tenant_id)
    except Exception as e:
        logger.warning("Redis cache set failed: %s", e)


def _resolve_ttl(datasource_id: str, result: dict) -> int:
    """确定缓存的有效期（TTL），优先级从高到低。

    优先级：
        1. 调用方在 result 中指定的 cache_ttl（允许单次查询自定义 TTL）
        2. 复杂查询（行数 > 100）使用更长的 TTL（默认 7200 秒 = 2 小时）
           原因：复杂查询的结果更稳定，短时间内不太可能变化
        3. 默认 TTL（来自配置 settings.query_cache_ttl_seconds）

    参数：
        datasource_id: 数据源 ID（预留，可用于按数据源配置不同 TTL）
        result: 查询结果字典

    返回值：
        int — TTL 秒数
    """
    # Allow per-query TTL hint from the caller
    meta_ttl = result.get("cache_ttl")
    if meta_ttl and isinstance(meta_ttl, int) and meta_ttl > 0:
        return meta_ttl

    # Complexity-based TTL: use longer TTL for complex queries (more stable results)
    row_count = len(result.get("rows", []))
    if row_count > 100:
        return getattr(settings, 'query_cache_ttl_complex', 7200)

    return settings.query_cache_ttl_seconds


async def cache_delete(question: str, datasource_id: str, tenant_id: str = "") -> None:
    """删除指定问题的精确缓存。

    参数：
        question: 用户的自然语言问题
        datasource_id: 数据源 ID
        tenant_id: 租户 ID
    """
    key = _cache_key(question, datasource_id, tenant_id)
    try:
        redis = await get_redis()
        await redis.delete(key)
    except Exception as e:
        logger.warning("Redis cache delete failed: %s", e)


def _datasource_index_key(datasource_id: str, tenant_id: str = "") -> str:
    """生成数据源索引的键。

    数据源索引是一个 Redis Set，存储该数据源的所有精确缓存键。
    用途：当数据源配置变更时，可以通过索引批量清除该数据源的所有缓存。

    参数：
        datasource_id: 数据源 ID
        tenant_id: 租户 ID

    返回值：
        str — 格式为 "cache_index:{sha256_hash}"
    """
    raw = f"{tenant_id}:{datasource_id}"
    return f"cache_index:{hashlib.sha256(raw.encode()).hexdigest()}"


async def cache_clear_all() -> int:
    """清除所有查询缓存（精确缓存 + 语义缓存索引 + 数据源索引）。

    使用 Redis SCAN 命令遍历匹配的键，逐个删除。
    注意：SCAN 是渐进式扫描，不会阻塞 Redis，适合大量键的场景。

    返回值：
        int — 删除的缓存条目数量
    """
    try:
        redis = await get_redis()
        deleted = 0
        # scan_iter(match="pattern") — 渐进式扫描匹配的键
        # 不使用 KEYS 命令，因为 KEYS 会阻塞 Redis（生产环境禁用）
        async for key in redis.scan_iter(match="query:*"):
            await redis.delete(key)
            deleted += 1
        async for key in redis.scan_iter(match="semantic:*"):
            await redis.delete(key)
            deleted += 1
        async for key in redis.scan_iter(match="cache_index:*"):
            await redis.delete(key)
            deleted += 1
        logger.info("Cleared %d cache entries", deleted)
        return deleted
    except Exception as e:
        logger.warning("Cache clear all failed: %s", e)
        return 0


async def cache_clear_datasource(datasource_id: str, tenant_id: str = "") -> int:
    """清除指定数据源的所有查询缓存。

    清除策略：
        1. 通过数据源索引（Redis Set）找到该数据源的所有精确缓存键，逐个删除
        2. 删除数据源索引本身
        3. 删除该数据源的语义缓存索引

    参数：
        datasource_id: 数据源 ID
        tenant_id: 租户 ID

    返回值：
        int — 删除的缓存条目数量
    """
    try:
        redis = await get_redis()
        deleted = 0

        # Delete via per-datasource index set if available
        index_key = _datasource_index_key(datasource_id, tenant_id)
        # smembers — 获取 Set 中的所有成员（即该数据源的所有缓存键）
        cache_keys = await redis.smembers(index_key)
        if cache_keys:
            for key in cache_keys:
                await redis.delete(key)
                deleted += 1
            await redis.delete(index_key)

        # Delete semantic cache index for this datasource
        sem_key = _semantic_cache_key("", datasource_id, tenant_id)
        if await redis.exists(sem_key):
            await redis.delete(sem_key)
            deleted += 1

        logger.info("Cleared %d cache entries for datasource %s", deleted, datasource_id)
        return deleted
    except Exception as e:
        logger.warning("Cache clear datasource failed: %s", e)
        return 0


# ── 语义缓存 ──

def _simple_similarity(a: str, b: str) -> float:
    """基于词重叠的简单相似度计算。

    算法：两个句子的词集合的交集大小 / 较大集合的大小（Jaccard 变体）。

    为什么不用 embedding？
      - embedding 需要调用 LLM 或额外模型，增加延迟和成本
      - 词重叠对于 BI 查询场景已经足够（用户问题通常较短且关键词明确）
      - 例如："上月销售额" vs "上个月的营收" → 共享词 "上月" → 相似度 > 0.5

    参数：
        a: 第一个句子
        b: 第二个句子

    返回值：
        float — 0.0 到 1.0 之间的相似度分数
    """
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    # & 是集合交集运算符，| 是集合并集
    # 这里用 max 而非并集大小做分母，使得短句匹配长句时分数更高
    return len(words_a & words_b) / max(len(words_a), len(words_b))


async def semantic_cache_get(question: str, datasource_id: str, tenant_id: str = "", threshold: float = 0.8) -> dict | None:
    """从语义缓存中查找相似问题的缓存结果。

    工作流程：
        1. 获取该数据源的语义缓存索引（一个 JSON 数组，包含历史问题和缓存键）
        2. 遍历索引，计算当前问题与每个历史问题的相似度
        3. 找到相似度最高且超过阈值的条目
        4. 用该条目的缓存键从 Redis 读取结果

    参数：
        question: 用户的自然语言问题
        datasource_id: 数据源 ID
        tenant_id: 租户 ID
        threshold: 相似度阈值（默认 0.8），低于此值不命中

    返回值：
        dict | None — 命中返回查询结果（带 _semantic_match=True 标记），未命中返回 None
    """
    try:
        redis = await get_redis()
        index_key = _semantic_cache_key(question, datasource_id, tenant_id)
        raw = await redis.get(index_key)
        if not raw:
            return None

        # 索引格式：[{"q": "上月销售额", "k": "query:abc123...", "ts": null}, ...]
        index = json.loads(raw)
        best_score = 0.0
        best_key = None

        # 遍历索引，找最相似的问题
        for entry in index:
            score = _simple_similarity(question, entry["q"])
            if score > best_score:
                best_score = score
                best_key = entry["k"]

        # 相似度超过阈值才命中
        if best_score >= threshold and best_key:
            cached = await redis.get(best_key)
            if cached:
                cache_stats.record_hit(semantic=True)
                logger.info("Semantic cache HIT (similarity=%.2f)", best_score)
                result = json.loads(cached)
                # 标记这是语义匹配的结果，前端可以提示用户"这是相似问题的结果"
                result["_semantic_match"] = True
                return result
    except Exception as e:
        logger.warning("Semantic cache lookup failed: %s", e)
    return None


async def _add_to_semantic_index(question: str, cache_key: str, datasource_id: str, tenant_id: str = "") -> None:
    """将一条问题加入语义缓存索引。

    索引结构：每个数据源维护一个 JSON 数组，存储 {问题, 缓存键, 时间戳}。
    当新问题写入缓存时，同时将其加入索引，供后续语义匹配查找。

    参数：
        question: 用户的自然语言问题
        cache_key: 精确缓存的键（用于命中后读取实际结果）
        datasource_id: 数据源 ID
        tenant_id: 租户 ID

    处理逻辑：
        1. 读取现有索引（如果有的话）
        2. 去重：如果同一问题已存在，先移除旧条目
        3. 追加新条目
        4. 如果索引超过最大条目数，裁剪最旧的条目
        5. 写回 Redis，设置与精确缓存相同的 TTL
    """
    try:
        redis = await get_redis()
        index_key = _semantic_cache_key(question, datasource_id, tenant_id)
        raw = await redis.get(index_key)
        index = json.loads(raw) if raw else []

        # Avoid duplicates — 同一个问题不重复索引
        index = [e for e in index if e["q"] != question.lower()]
        index.append({"q": question.lower().strip(), "k": cache_key, "ts": None})

        # Trim oldest entries — 保留最新的 SEMANTIC_CACHE_MAX_ENTRIES 条
        # 为什么保留最新的？因为新问题更可能反映当前数据状态
        if len(index) > SEMANTIC_CACHE_MAX_ENTRIES:
            index = index[-SEMANTIC_CACHE_MAX_ENTRIES:]

        await redis.set(index_key, json.dumps(index), ex=SEMANTIC_CACHE_TTL)
    except Exception as e:
        logger.warning("Semantic cache index update failed: %s", e)
