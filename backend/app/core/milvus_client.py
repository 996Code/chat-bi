"""
ChatBI v2 — Milvus Client

对标: 海泰 metric_service.py — Milvus 连接管理 + Collection 操作

v2 改进:
  - 单例 + 健康检查: get_milvus_client() 优先返回缓存的连接,
    调用 reset_milvus_client() 可强制重建 (用于降级后恢复)
  - is_milvus_healthy(): 轻量探测, 不抛异常, 返回 bool
    (vector_store 降级逻辑用: 检查是否可以从 Mock 恢复到 Milvus)

架构角色:
  - 全局单例 MilvusClient, 通过 get_milvus_client() 获取
  - 与 vector_store 层配合: 当 Milvus 不可用时, vector_store 降级为 Mock 模式
  - 健康检查定期探测, 恢复后自动重建连接

与 redis_client 的区别:
  - redis_client 是 async, milvus_client 是 sync (pymilvus 同步 API)
  - Milvus 连接失败不由这里处理, 由调用方 try/except 接管
  - 没有自动重连: reset_milvus_client() 后下次 get 会重建
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

    单例策略:
      - 已有连接 → 直接返回 (不做 PING 验证, 因为 pymilvus 的 HTTP 连接池自带心跳)
      - 无连接 → 创建新连接
      - 连接异常 → 抛异常, 调用方自行处理

    注意: 本函数是同步的, 在 async 上下文中调用时需 asyncio.to_thread 包装。
    但多数调用方 (如 vector_store) 在初始化时同步调用, 无需包装。
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

    健康检查流程:
      1. _client 为 None → 尝试创建
      2. 创建失败 → 返回 False
      3. 创建成功 → 调用 list_collections() 验证连通性
      4. list_collections 失败 → 重置单例, 返回 False (下次调用会重建)

    为什么用 list_collections 而非其他:
      - list_collections 是轻量级操作, 不涉及数据传输
      - 能验证: 连通性 + 认证 (token 有效) + 服务端可用
      - 不需要特定的 collection 存在
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
    """强制重置单例 (降级恢复时调用, 下次 get_milvus_client 会重建连接)。

    适用场景:
      - 连接配置变更后 (如 milvus_url 或 token 更新)
      - 降级恢复时: vector_store 从 Mock 模式切换回 Milvus 模式
      - 应用 shutdown 时: 确保资源释放

    注意: close 失败不抛异常, 仅打日志。旧连接会被 Python GC 回收。
    """
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