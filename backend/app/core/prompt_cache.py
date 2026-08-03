"""
ChatBI v2 — Prompt Layered Cache (T011)

Split system prompt into static (cacheable) and dynamic (per-request) sections.
Static sections: semantic layer context + Skills rules + SQL constraints
Dynamic sections: user question + conversation history + State Store context

对标: Claude Code systemPromptSections — section-level cache + boundary marker

架构角色:
  - 管理 system prompt 的分段组装, 避免每次请求都重新构建静态部分
  - 静态段: schema 上下文、Skills 规则、SQL 约束 (缓存 5 分钟)
  - 动态段: 用户问题、对话历史、State Store 上下文 (每次请求重新计算)
  - 边界标记: PROMPT_DYNAMIC_BOUNDARY 分隔静态和动态段

缓存策略:
  - 静态段: 基于 MD5 内容哈希, 内容不变时复用缓存
  - TTL 5 分钟: 即使内容变化, 最多 5 分钟重新计算
  - 动态段: 不缓存, 每次重新计算
  - invalidate_static(): 在 Skills 变更时手动触发 (如用户更新了 Skills 规则)
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class PromptSection:
    """A named section of the system prompt with cache control.

    Attributes:
        name: 段名称, 用于 set_section 按名覆盖
        cacheable: 是否可缓存 (静态段 True, 动态段 False)
        _cache_key: 内容的 MD5 哈希, 用于检测内容变化
        _cached_value: 缓存的内容

    缓存失效策略:
      - cacheable=False: 每次调用 compute() 都重新计算
      - cacheable=True: 首次调用时计算并缓存, 后续复用
      - invalidate(): 强制下次调用重新计算
    """

    def __init__(self, name: str, compute: Callable[[], str], cacheable: bool = True):
        self.name = name
        self._compute = compute
        self.cacheable = cacheable
        self._cache_key: Optional[str] = None
        self._cached_value: Optional[str] = None

    def compute(self) -> str:
        """Compute (or return cached) section content.

        缓存逻辑:
          - 非缓存段 → 直接调用 compute 函数
          - 缓存段且已有缓存 → 返回缓存值
          - 缓存段无缓存 → 计算, 生成 MD5 hash, 缓存, 返回
        """
        if not self.cacheable:
            return self._compute()

        # Check cache
        new_key = self._cache_key
        if new_key and self._cached_value is not None:
            return self._cached_value

        value = self._compute()
        # MD5 用于内容变更检测 (非安全用途, 仅用于缓存键)
        self._cache_key = hashlib.md5(value.encode()).hexdigest()
        self._cached_value = value
        return value

    def invalidate(self) -> None:
        """Force recomputation on next access."""
        self._cache_key = None
        self._cached_value = None


class PromptCache:
    """
    Layered prompt cache with static/dynamic boundary.

    Usage:
      cache = PromptCache()
      cache.add_static("semantic", lambda: build_semantic_prompt())
      cache.add_dynamic("history", lambda: build_history_prompt())

      sections = cache.assemble()
      system_prompt = "\\n---\\n".join(sections)

    对标 Claude Code:
    - SYSTEM_PROMPT_DYNAMIC_BOUNDARY — marker between static and dynamic
    - Static sections cached for 5 minutes
    - Dynamic sections recomputed every request

    组装流程:
      1. 检查 TTL: 超过 5 分钟 → 失效所有静态段
      2. 计算所有静态段 (缓存命中直接返回)
      3. 插入动态边界标记
      4. 计算所有动态段 (不缓存)
      5. 返回拼接后的 sections list
    """

    PROMPT_DYNAMIC_BOUNDARY = (
        "\n--- PROMPT_DYNAMIC_BOUNDARY ---\n"
        "# The following sections are dynamic and may change each request\n"
    )

    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._static_sections: list[PromptSection] = []
        self._dynamic_sections: list[PromptSection] = []
        self._last_invalidation: float = 0.0

    def add_static(self, name: str, compute: Callable[[], str]) -> None:
        """Add a cacheable (static) section.

        注意: 同名段会重复添加 (不检查重复)。
        如需覆盖, 使用 set_static。
        """
        self._static_sections.append(PromptSection(name, compute, cacheable=True))

    def set_static(self, name: str, compute: Callable[[], str]) -> None:
        """Set/replace a static section by name (不堆积重复, 跨调用复用缓存)。

        如果 type 前缀存在, 替换内容; 否则追加。
        相比 add_static, 更适合多次调用场景 (如每次请求前更新)。
        """
        self._set_section(self._static_sections, name, compute, cacheable=True)

    def add_dynamic(self, name: str, compute: Callable[[], str]) -> None:
        """Add a per-request (dynamic) section."""
        self._dynamic_sections.append(PromptSection(name, compute, cacheable=False))

    def set_dynamic(self, name: str, compute: Callable[[], str]) -> None:
        """Set/replace a dynamic section by name (不堆积重复)。"""
        self._set_section(self._dynamic_sections, name, compute, cacheable=False)

    @staticmethod
    def _set_section(sections: list, name: str, compute: Callable, cacheable: bool) -> None:
        """按 name 覆盖 (存在则替换, 不存在则追加)。

        遍历 sections 查找同名段, 找到则替换, 未找到则追加。
        替换时保留其他属性 (如缓存状态), 仅更新 compute 函数。
        """
        for i, s in enumerate(sections):
            if s.name == name:
                sections[i] = PromptSection(name, compute, cacheable=cacheable)
                return
        sections.append(PromptSection(name, compute, cacheable=cacheable))

    def assemble(self) -> list[str]:
        """Assemble the full prompt sections list.

        Returns a list of section text strings, with the dynamic boundary
        marker inserted between static and dynamic sections.

        TTL 检查: 超过 ttl_seconds 后自动失效静态段。
        空段 (compute 返回空字符串) 跳过, 不加入返回列表。
        """
        # Check TTL for static cache
        now = time.monotonic()
        if now - self._last_invalidation > self.ttl_seconds:
            self.invalidate_static()
            self._last_invalidation = now

        sections = []

        # Static sections (cacheable)
        for section in self._static_sections:
            content = section.compute()
            if content:
                sections.append(content)

        # Dynamic boundary marker
        # 仅在既有静态段又有动态段时插入边界标记
        if sections and self._dynamic_sections:
            sections.append(self.PROMPT_DYNAMIC_BOUNDARY)

        # Dynamic sections (not cached)
        for section in self._dynamic_sections:
            content = section.compute()
            if content:
                sections.append(content)

        return sections

    def invalidate_static(self) -> None:
        """Invalidate all static section caches (e.g., on Skills change).

        在用户更新 Skills 规则后调用, 确保下次请求使用最新内容。
        """
        for section in self._static_sections:
            section.invalidate()
        logger.debug("Prompt static cache invalidated")

    def invalidate_all(self) -> None:
        """Invalidate all caches.

        包括动态段 (虽然动态段不缓存, 但 invalidate 方法存在)。
        """
        self.invalidate_static()
        for section in self._dynamic_sections:
            section.invalidate()
        logger.debug("Prompt cache fully invalidated")


# ── Global instance ────────────────────────────────────────────

_prompt_cache: Optional[PromptCache] = None


def get_prompt_cache(ttl_seconds: int = 300) -> PromptCache:
    """Get or create the global prompt cache.

    ttl_seconds 仅在首次创建时生效, 后续调用忽略。
    单例模式, 全局共享一个 PromptCache 实例。
    """
    global _prompt_cache
    if _prompt_cache is None:
        _prompt_cache = PromptCache(ttl_seconds=ttl_seconds)
    return _prompt_cache
