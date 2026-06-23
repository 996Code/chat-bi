"""
ChatBI v2 — Prompt Layered Cache (T011)

Split system prompt into static (cacheable) and dynamic (per-request) sections.
Static sections: semantic layer context + Skills rules + SQL constraints
Dynamic sections: user question + conversation history + State Store context

对标: Claude Code systemPromptSections — section-level cache + boundary marker
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class PromptSection:
    """A named section of the system prompt with cache control."""

    def __init__(self, name: str, compute: Callable[[], str], cacheable: bool = True):
        self.name = name
        self._compute = compute
        self.cacheable = cacheable
        self._cache_key: Optional[str] = None
        self._cached_value: Optional[str] = None

    def compute(self) -> str:
        """Compute (or return cached) section content."""
        if not self.cacheable:
            return self._compute()

        # Check cache
        new_key = self._cache_key
        if new_key and self._cached_value is not None:
            return self._cached_value

        value = self._compute()
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
        """Add a cacheable (static) section."""
        self._static_sections.append(PromptSection(name, compute, cacheable=True))

    def set_static(self, name: str, compute: Callable[[], str]) -> None:
        """Set/replace a static section by name (不堆积重复, 跨调用复用缓存)。"""
        self._set_section(self._static_sections, name, compute, cacheable=True)

    def add_dynamic(self, name: str, compute: Callable[[], str]) -> None:
        """Add a per-request (dynamic) section."""
        self._dynamic_sections.append(PromptSection(name, compute, cacheable=False))

    def set_dynamic(self, name: str, compute: Callable[[], str]) -> None:
        """Set/replace a dynamic section by name (不堆积重复)。"""
        self._set_section(self._dynamic_sections, name, compute, cacheable=False)

    @staticmethod
    def _set_section(sections: list, name: str, compute: Callable, cacheable: bool) -> None:
        """按 name 覆盖 (存在则替换, 不存在则追加)。"""
        for i, s in enumerate(sections):
            if s.name == name:
                sections[i] = PromptSection(name, compute, cacheable=cacheable)
                return
        sections.append(PromptSection(name, compute, cacheable=cacheable))

    def assemble(self) -> list[str]:
        """Assemble the full prompt sections list.

        Returns a list of section text strings, with the dynamic boundary
        marker inserted between static and dynamic sections.
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
        if sections and self._dynamic_sections:
            sections.append(self.PROMPT_DYNAMIC_BOUNDARY)

        # Dynamic sections (not cached)
        for section in self._dynamic_sections:
            content = section.compute()
            if content:
                sections.append(content)

        return sections

    def invalidate_static(self) -> None:
        """Invalidate all static section caches (e.g., on Skills change)."""
        for section in self._static_sections:
            section.invalidate()
        logger.debug("Prompt static cache invalidated")

    def invalidate_all(self) -> None:
        """Invalidate all caches."""
        self.invalidate_static()
        for section in self._dynamic_sections:
            section.invalidate()
        logger.debug("Prompt cache fully invalidated")


# ── Global instance ────────────────────────────────────────────

_prompt_cache: Optional[PromptCache] = None


def get_prompt_cache(ttl_seconds: int = 300) -> PromptCache:
    """Get or create the global prompt cache."""
    global _prompt_cache
    if _prompt_cache is None:
        _prompt_cache = PromptCache(ttl_seconds=ttl_seconds)
    return _prompt_cache
