"""
ChatBI v2 — Milvus Client

对标: 海泰 metric_service.py — Milvus 连接管理 + Collection 操作

v2 改进:
  - 单例 + 健康检查: get_milvus_client() 优先返回缓存的连接,
    调用 reset_milvus_client() 可强制重建 (用于降级后恢复)
  - is_milvus_healthy(): 轻量探测, 不抛异常, 返回 bool
    (vector_store 降级逻辑用: 检查是否可以从 Mock 恢复到 Milvus)
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_client: Optional[object] = None  # MilvusClient | None


def get_milvus_client() -> object:
    """Get or create the Milvus client connection.

    对标 redis_client 降级模式: 本地 infra 未起时不阻塞启动。
    连接失败抛异常由调用方 try/except（main.py lifespan 已处理降级）。
    """
    global _client

    if _client is not None:
        return _client

    from pymilvus import MilvusClient

    settings = get_settings()
    _client = MilvusClient(
        uri=settings.milvus_url,
        token=settings.milvus_token,
    )
    logger.info("Milvus connected: %s", settings.milvus_url)
    return _client


def is_milvus_healthy() -> bool:
    """轻量健康检查: Milvus client 存在且能 list_collections。

    不抛异常, 返回 bool (用于 vector_store 判断能否从 Mock 降级恢复到 Milvus)。
    """
    global _client
    if _client is None:
        try:
            get_milvus_client()
        except Exception:
            return False
    try:
        _client.list_collections()  # type: ignore[union-attr]
        return True
    except Exception as e:
        logger.warning("Milvus 健康检查失败: %s", e)
        # 连接已断, 重置单例以便下次重新连接
        _client = None
        return False


def reset_milvus_client() -> None:
    """强制重置单例 (降级恢复时调用, 下次 get_milvus_client 会重建连接)。"""
    global _client
    if _client is not None:
        try:
            _client.close()  # type: ignore[union-attr]
        except Exception:
            pass
        _client = None


def close_milvus() -> None:
    """Close the Milvus connection (app shutdown)。"""
    reset_milvus_client()
    logger.info("Milvus connection closed")