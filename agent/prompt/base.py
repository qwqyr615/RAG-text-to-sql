"""Prompt 段（Section）与 Provider 抽象。

一段上下文 = 一个 :class:`PromptSectionProvider`。每个 provider 自己负责：

1. 从 :class:`PromptContext` 取原始数据（元数据 / 知识 / 指标 / RAG 检索结果）；
2. 按与用户问题的相关性排序；
3. 在分配到的字符预算内裁剪，并记录丢弃了多少项。

:meth:`PromptSectionProvider.provide` 是模板方法：调用 ``build()`` 后统一做一次预算
兜底，保证任何 provider 都不会超预算，也不会因为单段异常拖垮整次请求。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from prompt.budget import extract_keywords, truncate_text

logger = logging.getLogger(__name__)

__all__ = ["PromptContext", "PromptSection", "PromptSectionProvider"]


@dataclass
class PromptSection:
    """一段 Prompt 上下文及其预算使用情况。"""

    name: str
    title: str
    content: str = ""
    max_chars: int = 0
    original_chars: int = 0
    truncated: bool = False
    dropped_items: int = 0
    error: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.content or "").strip()

    @property
    def used_chars(self) -> int:
        return len(self.content or "")

    def render(self) -> str:
        """渲染成 system prompt 中的一段（空段返回空串，不占位）。"""
        if self.is_empty:
            return ""
        return f"### {self.title}\n{self.content.strip()}"

    def to_dict(self) -> dict[str, Any]:
        """序列化用量信息，供日志与前端展示。"""
        return {
            "name": self.name,
            "title": self.title,
            "used_chars": self.used_chars,
            "max_chars": self.max_chars,
            "truncated": self.truncated,
            "dropped_items": self.dropped_items,
            "error": self.error,
        }


@dataclass
class PromptContext:
    """一次请求的上下文来源。

    关键词按需计算并缓存，4 个 provider 共用同一份，避免重复切词。
    """

    question: str
    metadata_json: dict[str, Any] = field(default_factory=dict)
    knowledge: dict[str, Any] = field(default_factory=dict)
    available_columns: list[str] = field(default_factory=list)
    _keywords: set[str] | None = field(default=None, repr=False, compare=False)

    def keywords(self) -> set[str]:
        """当前问题的关键词集合（带缓存）。"""
        if self._keywords is None:
            self._keywords = extract_keywords(self.question)
        return self._keywords


class PromptSectionProvider(ABC):
    """Prompt 段生产者。子类只需实现 :meth:`build`。"""

    #: 段标识，用于取用（如 ``result.section("rag")``）
    name: str = "section"
    #: 段标题，渲染进 system prompt
    title: str = "Section"
    #: 段优先级，数值越大越重要；总预算不足时先压缩数值小的段
    priority: int = 50
    #: 默认字符预算，可被 settings 覆盖
    default_max_chars: int = 1500

    def __init__(self, max_chars: int | None = None, *, enabled: bool = True) -> None:
        self.max_chars = int(
            max_chars if max_chars is not None else self.default_max_chars
        )
        self.enabled = enabled
        self.last_section: PromptSection | None = None

    def provide(
        self, context: PromptContext, budget: int | None = None
    ) -> PromptSection:
        """模板方法：构建 → 预算兜底 → 记录本次用量。"""
        effective = self._effective_budget(budget)

        if not self.enabled:
            section = PromptSection(
                name=self.name, title=self.title, max_chars=effective
            )
            self.last_section = section
            return section

        try:
            section = self.build(context, effective)
        except Exception as exc:  # noqa: BLE001 - 单段失败不应阻断整次请求
            logger.warning("Prompt 段 %s 构建失败，已跳过：%s", self.name, exc)
            section = PromptSection(
                name=self.name,
                title=self.title,
                max_chars=effective,
                error=str(exc),
            )

        section = self._finalize(section, effective)
        self.last_section = section
        return section

    def _effective_budget(self, budget: int | None) -> int:
        """本次可用预算：不超过本段上限，也不超过调用方分配值。"""
        if budget is None:
            return self.max_chars
        return max(0, min(self.max_chars, int(budget)))

    def _finalize(self, section: PromptSection, budget: int) -> PromptSection:
        """预算兜底：任何 provider 的输出都不会超过分配到的字符数。"""
        section.name = self.name
        section.title = section.title or self.title
        section.content = section.content or ""
        section.original_chars = len(section.content)
        section.max_chars = budget
        if section.original_chars > budget:
            section.content, truncated = truncate_text(section.content, budget)
            section.truncated = section.truncated or truncated
        return section

    @abstractmethod
    def build(self, context: PromptContext, budget: int) -> PromptSection:
        """在 ``budget`` 字符预算内构建本段内容。"""
