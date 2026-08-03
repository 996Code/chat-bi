"""
ChatBI v2 — Logging Configuration

对标: v1 logger.py — TimedRotatingFileHandler 按天轮转

架构角色:
  - 应用启动时初始化, 配置 root logger 的 handler 和 formatter
  - 双重输出: 控制台 (INFO 级别) + 文件 (DEBUG 级别, 按天轮转)
  - 静音第三方库: 减少 httpx/urllib3 等库的冗余日志

日志级别策略:
  - 控制台: INFO 级别 (日常开发查看)
  - 文件: DEBUG 级别 (问题排查时需要详细日志)
  - 第三方库: WARNING 级别 (避免淹没应用日志)

轮转策略:
  - 按天轮转 (midnight 触发)
  - 保留天数由 config.log_retention_days 控制 (默认 14 天)
  - 编码 UTF-8, 支持中文日志
"""
from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logging() -> None:
    """Configure structured logging with daily rotation.

    初始化步骤:
      1. 确保日志目录存在 (自动创建)
      2. 配置 root logger 级别 (来自 config.log_level)
      3. 添加控制台 handler (INFO 级别, 简洁格式)
      4. 添加文件 handler (DEBUG 级别, 含行号格式)
      5. 静音第三方库 (httpx/httpcore/urllib3/pymilvus)

    为什么 root logger 而非 app logger:
      - 所有 import logging.getLogger(__name__) 创建的子 logger 都继承 root
      - 一次配置, 全局生效
      - 避免每个模块重复配置 handler

    注意: 本函数应在应用启动时调用一次, 重复调用会添加重复 handler。
    """
    from app.core.config import get_settings

    settings = get_settings()

    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    # Console handler: 简洁格式, 适合开发时实时查看
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # File handler (daily rotation, 14 days retention)
    # 文件日志包含行号 (%(lineno)d), 方便问题定位
    file_handler = TimedRotatingFileHandler(
        filename=log_dir / "chatbi.log",
        when="midnight",
        interval=1,
        backupCount=settings.log_retention_days,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)
    root_logger.addHandler(file_handler)

    # Quiet noisy third-party loggers
    # httpx/httpcore: LLM API 调用会产生大量 debug 日志
    # urllib3: 数据库连接池内部的 debug 日志
    # pymilvus: Milvus gRPC 的 debug 日志
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pymilvus").setLevel(logging.WARNING)

    # Log startup info
    logger = logging.getLogger(__name__)
    logger.info("ChatBI v2 logging initialized (level=%s)", settings.log_level)
