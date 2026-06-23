-- ChatBI v2 — PostgreSQL 初始化脚本
-- PG 首次启动时由 docker-entrypoint-initdb.d 自动执行。
--
-- pgvector 扩展: Phase 3 (RAG 检索) 向量列要用。
-- 镜像 pgvector/pgvector:pg16 已内置此扩展的二进制，这里只需 ENABLE。

CREATE EXTENSION IF NOT EXISTS vector;

-- 验证扩展可用（部署后可连库跑: SELECT extname FROM pg_extension WHERE extname='vector';）
