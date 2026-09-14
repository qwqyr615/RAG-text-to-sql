"""SQL Agent 的 Prompt 组装：静态 system 模板 + 动态四段上下文。

职责划分：

- **静态部分**（工作流约束、方言、循环预算说明）写在
  ``core/prompts.SQL_AGENT_SYSTEM_TEMPLATE``，整体只描述「怎么做」；
- **动态部分**由本模块的 :class:`SQLAgentPromptBuilder` 每次请求组装，包含
  元数据段 / 指标段 / 知识段 / RAG 示例段四段，通过 ``{context_block}`` 注入。

总预算由 ``settings.prompt_total_budget`` 控制：各段先按自己的预算裁剪，若合计仍然
超出总预算，则按段优先级从低到高压缩，直到落入预算。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from core.config import settings
from core.prompts import SQL_AGENT_SYSTEM_TEMPLATE
from prompt.base import PromptContext, PromptSection, PromptSectionProvider
from prompt.providers import (
    DataResourceProvider,
    KnowledgeProvider,
    MetricsProvider,
    RagExampleProvider,
)

logger = logging.getLogger(__name__)

__all__ = ["PromptBuildResult", "SQLAgentPromptBuilder"]

EMPTY_CONTEXT_HINT = (
    "本次没有可用的业务上下文。请先调用 sql_db_list_tables 查看可用表，"
    "再用 sql_db_schema 确认目标表的真实字段名，不要猜测列名。"
)


@dataclass
class PromptBuildResult:
    """一次请求的上下文组装结果。"""

    context_block: str
    sections: list[PromptSection] = field(default_factory=list)
    total_budget: int = 0
    used_chars: int = 0

    @property
    def section_map(self) -> dict[str, PromptSection]:
        return {section.name: section for section in self.sections}

    def section(self, name: str) -> PromptSection | None:
        """按段名取用，例如 ``result.section("rag")``。"""
        return self.section_map.get(name)

    def usage(self) -> dict[str, Any]:
        """各段预算使用情况，供日志与前端展示。"""
        return {
            "total_budget": self.total_budget,
            "used_chars": self.used_chars,
            "over_budget": bool(self.total_budget and self.used_chars > self.total_budget),
            "sections": [section.to_dict() for section in self.sections],
        }

    def summary(self) -> str:
        """单行摘要，便于日志排查「哪一段吃掉了预算」。"""
        parts = []
        for section in self.sections:
            text = f"{section.name}={section.used_chars}/{section.max_chars}"
            if section.dropped_items:
                text += f"(丢弃{section.dropped_items})"
            if section.truncated:
                text += "(截断)"
            if section.error:
                text += "(失败)"
            parts.append(text)
        return (
            f"总计 {self.used_chars}/{self.total_budget} 字符：" + "，".join(parts)
        )


class SQLAgentPromptBuilder:
    """把四个 Provider 组装成 system prompt 里的动态上下文段。"""

    def __init__(
        self,
        providers: Sequence[PromptSectionProvider],
        *,
        total_budget: int | None = None,
        min_section_chars: int = 200,
    ) -> None:
        self.providers = list(providers)
        self.total_budget = int(
            total_budget if total_budget is not None else settings.prompt_total_budget
        )
        self.min_section_chars = max(0, int(min_section_chars))

    @classmethod
    def from_settings(cls) -> "SQLAgentPromptBuilder":
        """按 ``.env`` / ``core.config`` 的配置装配默认四段。"""
        return cls(
            providers=[
                DataResourceProvider(
                    settings.prompt_metadata_budget,
                    min_tables=settings.prompt_metadata_min_tables,
                    max_columns_per_table=settings.sql_max_columns_per_table,
                    sample_value_tables=settings.prompt_metadata_sample_tables,
                ),
                MetricsProvider(settings.prompt_metrics_budget),
                KnowledgeProvider(settings.prompt_knowledge_budget),
                RagExampleProvider(
                    settings.prompt_rag_budget,
                    top_k=settings.rag_top_k,
                    min_score=settings.rag_min_score,
                ),
            ],
            total_budget=settings.prompt_total_budget,
        )

    def system_template(self) -> str:
        """system prompt 模板（含 ``{dialect}`` / ``{top_k}`` / ``{context_block}``）。"""
        return SQL_AGENT_SYSTEM_TEMPLATE

    def build_context(self, context: PromptContext) -> PromptBuildResult:
        """构建四段上下文并渲染成可注入的 ``context_block``。"""
        sections = [provider.provide(context) for provider in self.providers]

        if self.total_budget and self._total_used(sections) > self.total_budget:
            sections = self._shrink_to_total(context, sections)

        rendered = [section.render() for section in sections]
        body = "\n\n".join(part for part in rendered if part)
        if not body:
            body = f"### 业务上下文\n{EMPTY_CONTEXT_HINT}"

        result = PromptBuildResult(
            context_block=body,
            sections=sections,
            total_budget=self.total_budget,
            used_chars=self._total_used(sections),
        )
        logger.debug("Prompt 段构建完成：%s", result.summary())
        return result

    def _shrink_to_total(
        self, context: PromptContext, sections: list[PromptSection]
    ) -> list[PromptSection]:
        """总预算不足时，按段优先级从低到高压缩各段。"""
        overflow = self._total_used(sections) - self.total_budget
        order = sorted(
            range(len(sections)), key=lambda index: self.providers[index].priority
        )

        for index in order:
            if overflow <= 0:
                break
            current = sections[index]
            target = max(self.min_section_chars, current.used_chars - overflow)
            if target >= current.used_chars:
                continue
            sections[index] = self.providers[index].provide(context, budget=target)
            overflow = self._total_used(sections) - self.total_budget

        return sections

    @staticmethod
    def _total_used(sections: Sequence[PromptSection]) -> int:
        return sum(section.used_chars for section in sections)
