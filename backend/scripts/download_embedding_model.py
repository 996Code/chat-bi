#!/usr/bin/env python
"""
下载 BGE-large-zh-v1.5 embedding 模型到项目目录。

用法:
    uv run python backend/scripts/download_embedding_model.py

对标 spec RAG-001: BGE-large-zh-v1.5 (1024 维)。
模型下载到 backend/models/bge-large-zh-v1.5/（项目内，不入 git），
部署时随项目一起拷贝/打镜像，离线可用。

设计:
  - 显式 local_dir，不依赖 HuggingFace 默认缓存 (~/.cache/huggingface)
  - 已下载则跳过（幂等）
  - 模型目录已加入 .gitignore (1.3GB 不入库)
"""
from __future__ import annotations

import sys
from pathlib import Path

# 模型固定配置
MODEL_ID = "BAAI/bge-large-zh-v1.5"
# 项目内固定路径: backend/models/<model>/
# config.py 的 embedding_model_path 也指这里，保持单一来源
MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "bge-large-zh-v1.5"


def main() -> int:
    if MODEL_DIR.exists() and any(MODEL_DIR.iterdir()):
        print(f"✓ 模型已存在: {MODEL_DIR}")
        print("  如需重新下载，先删除该目录。")
        return 0

    print(f"→ 下载 {MODEL_ID}")
    print(f"  目标目录: {MODEL_DIR}")
    MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("✗ 缺少 huggingface_hub，请先: uv add huggingface_hub", file=sys.stderr)
        return 1

    try:
        snapshot_download(
            repo_id=MODEL_ID,
            local_dir=str(MODEL_DIR),
        )
    except Exception as e:
        print(f"✗ 下载失败: {e}", file=sys.stderr)
        print("  如果是网络问题，可设置镜像:", file=sys.stderr)
        print("    HF_ENDPOINT=https://hf-mirror.com", file=sys.stderr)
        return 1

    size_mb = sum(f.stat().st_size for f in MODEL_DIR.rglob("*") if f.is_file()) / 1024 / 1024
    print(f"\n✓ 下载完成: {MODEL_DIR} ({size_mb:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
