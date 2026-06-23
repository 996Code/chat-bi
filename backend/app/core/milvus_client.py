"""
ChatBI v2 — Milvus Client

对标: 海泰 metric_service.py — Milvus 连接管理 + Collection 操作
"""
from __future__ import annotations

import logging
from typing import Optional

from pymilvus import MilvusClient

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_client: Optional[MilvusClient] = None


def get_milvus_client() -> MilvusClient:
    """Get or create the Milvus client connection.

    对标 redis_client 降级模式: 本地 infra 未起时不阻塞启动。
    连接失败抛异常由调用方 try/except（main.py lifespan 已处理降级）。
    """
    global _client

    if _client is not None:
        return _client

    settings = get_settings()
    _client = MilvusClient(
        uri=settings.milvus_url,
        token=settings.milvus_token,
    )
    logger.info("Milvus connected: %s", settings.milvus_url)
    return _client


def close_milvus() -> None:
    """Close the Milvus connection."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        logger.info("Milvus connection closed")