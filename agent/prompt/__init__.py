"""Prompt 段 Provider 体系。

对外暴露四段上下文（元数据 / 指标 / 知识 / RAG 示例）的组装能力：

    from prompt import SQLAgentPromptBuilder, PromptContext

    builder = SQLAgentPromptBuilder.from_settings()
    result = builder.build_context(PromptContext(question="各产线缺陷率", metadata_json=...))
    result.context_block   # 注入 system prompt 的 {context_block}
    result.usage()         # 各段预算使用情况
"""

from prompt.base import PromptContext, PromptSection, PromptSectionProvider
from prompt.budget import CharBudget, extract_keywords, pack_blocks, relevance_score
from prompt.builder import PromptBuildResult, SQLAgentPromptBuilder
from prompt.providers import (
    DataResourceProvider,
    KnowledgeProvider,
    MetricsProvider,
    RagExampleProvider,
)

__all__ = [
    "CharBudget",
    "DataResourceProvider",
    "KnowledgeProvider",
    "MetricsProvider",
    "PromptBuildResult",
    "PromptContext",
    "PromptSection",
    "PromptSectionProvider",
    "RagExampleProvider",
    "SQLAgentPromptBuilder",
    "extract_keywords",
    "pack_blocks",
    "relevance_score",
]
