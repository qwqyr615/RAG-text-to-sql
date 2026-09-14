"""四个 Prompt 段 Provider 的裁剪与预算行为测试。"""

from typing import Any, Callable, Dict, List

import pytest

from core.config import settings
from prompt.base import PromptContext
from prompt.budget import extract_keywords, pack_blocks, relevance_score
from prompt.builder import SQLAgentPromptBuilder
from prompt.providers import (
    DataResourceProvider,
    KnowledgeProvider,
    MetricsProvider,
    RagExampleProvider,
)


def _empty_search(question: str, *, k: int, min_score: float | None) -> List[Dict[str, Any]]:
    return []


def _make_builder(
    *, total_budget: int = 8000, min_section_chars: int = 200, search_fn: Any = None
) -> SQLAgentPromptBuilder:
    return SQLAgentPromptBuilder(
        [
            DataResourceProvider(3000),
            MetricsProvider(1500),
            KnowledgeProvider(1800),
            RagExampleProvider(
                1500,
                top_k=3,
                min_score=0.45,
                search_fn=search_fn or _empty_search,
            ),
        ],
        total_budget=total_budget,
        min_section_chars=min_section_chars,
    )


# ----------------------------------------------------------------------
# 预算与相关性原语
# ----------------------------------------------------------------------
def test_extract_keywords_and_relevance() -> None:
    keywords = extract_keywords("各产线缺陷率是多少")
    assert "缺陷" in keywords
    assert "缺陷率" in keywords
    assert "多少" not in keywords  # 停用词
    assert relevance_score("缺陷率 defect_rate", keywords) > relevance_score(
        "库存周转", keywords
    )


def test_pack_blocks_drops_from_the_end() -> None:
    content, dropped, truncated = pack_blocks(
        ["a" * 10, "b" * 10, "c" * 10], 25, drop_note="…丢弃 {dropped} 项"
    )
    assert dropped >= 1
    assert truncated
    assert "a" * 10 in content
    assert content.count("丢弃") == 1


# ----------------------------------------------------------------------
# 元数据段
# ----------------------------------------------------------------------
def test_metadata_provider_puts_relevant_table_first(
    make_context: Callable[..., PromptContext],
) -> None:
    provider = DataResourceProvider(3000)
    section = provider.provide(make_context("各产线缺陷率是多少"))

    assert section.used_chars <= 3000
    first_table = next(
        line for line in section.content.splitlines() if line.startswith("- ")
    )
    assert "fact_production_record" in first_table
    assert "表间关系" in section.content
    assert "样例:" in section.content


def test_metadata_budget_is_respected(
    make_context: Callable[..., PromptContext],
) -> None:
    provider = DataResourceProvider(400, min_tables=1)
    section = provider.provide(make_context())

    assert section.used_chars <= 400
    assert section.dropped_items > 0 or section.truncated


def test_metadata_provider_without_metadata_gives_hint() -> None:
    provider = DataResourceProvider(1000)
    section = provider.provide(PromptContext(question="有什么表"))
    assert "sql_db_list_tables" in section.content


def test_metadata_provider_keeps_relationships_even_when_tables_are_dropped(
    make_context: Callable[..., PromptContext],
) -> None:
    provider = DataResourceProvider(300, min_tables=1)
    section = provider.provide(make_context())
    assert section.used_chars <= 300
    assert "表间关系" in section.content


# ----------------------------------------------------------------------
# 指标段
# ----------------------------------------------------------------------
def test_metrics_provider_only_lists_resolvable_metrics(
    make_context: Callable[..., PromptContext],
) -> None:
    provider = MetricsProvider(1500)
    section = provider.provide(make_context())

    assert "defect_rate" in section.content
    assert "fact_production_record.defect_rate" in section.content
    # 质量得分 / 故障次数 / 产量在样例数据源里没有对应字段，不应出现
    assert "quality_score" not in section.content
    assert "fault_event_count" not in section.content


def test_metrics_provider_orders_by_question_relevance(
    make_context: Callable[..., PromptContext],
) -> None:
    provider = MetricsProvider(1500)
    section = provider.provide(make_context("良率怎么样"))
    assert section.content.index("良率") < section.content.index("停机时长")


def test_metrics_provider_without_matching_columns() -> None:
    provider = MetricsProvider(1500)
    context = PromptContext(question="随便问问", available_columns=["foo", "bar"])
    section = provider.provide(context)
    assert "未匹配到预置业务指标字段" in section.content


# ----------------------------------------------------------------------
# 知识段
# ----------------------------------------------------------------------
def test_knowledge_provider_marks_unmapped_rule(
    make_context: Callable[..., PromptContext],
) -> None:
    provider = KnowledgeProvider(1800)
    section = provider.provide(make_context())

    assert "[分析主题]" in section.content
    assert "[业务对象]" in section.content
    assert "不要臆造该指标" in section.content


def test_knowledge_provider_respects_budget(
    make_context: Callable[..., PromptContext],
) -> None:
    provider = KnowledgeProvider(120)
    section = provider.provide(make_context())
    assert section.used_chars <= 120


# ----------------------------------------------------------------------
# RAG 段
# ----------------------------------------------------------------------
def _fake_examples() -> List[Dict[str, Any]]:
    return [
        {
            "question": "各产线缺陷率",
            "sql": "SELECT AVG(defect_rate) FROM fact_production_record",
            "score": 0.91,
            "tables": "fact_production_record",
            "metrics": "缺陷率",
        },
        {
            "question": "设备停机时长",
            "sql": "SELECT SUM(downtime_minutes) FROM fact_production_record",
            "score": 0.62,
            "tables": "fact_production_record",
        },
        {
            "question": "库存周转率",
            "sql": "SELECT * FROM dim_product",
            "score": 0.10,
            "tables": "dim_product",
        },
    ]


def test_rag_provider_filters_by_min_score(
    make_context: Callable[..., PromptContext],
) -> None:
    provider = RagExampleProvider(
        1500,
        top_k=3,
        min_score=0.45,
        search_fn=lambda question, *, k, min_score: _fake_examples(),
    )
    section = provider.provide(make_context())

    assert len(provider.last_examples) == 2
    assert "0.10" not in section.content
    assert "仅供参考" in section.content


def test_rag_provider_fails_open(make_context: Callable[..., PromptContext]) -> None:
    def broken_search(question: str, *, k: int, min_score: float | None) -> Any:
        raise RuntimeError("Milvus 不可用")

    provider = RagExampleProvider(1000, search_fn=broken_search)
    section = provider.provide(make_context())

    assert section.is_empty
    # 段内已失败开放，不算 provider 异常
    assert section.error is None


def test_rag_provider_drops_lowest_score_when_budget_tight(
    make_context: Callable[..., PromptContext],
) -> None:
    def many_examples(question: str, *, k: int, min_score: float | None) -> List[Dict[str, Any]]:
        return [
            {
                "question": f"问题{index}",
                "sql": "SELECT " + "x" * 60,
                "score": 0.9 - index * 0.01,
            }
            for index in range(5)
        ]

    provider = RagExampleProvider(
        300, top_k=5, min_score=0.0, search_fn=many_examples
    )
    section = provider.provide(make_context())

    assert section.used_chars <= 300
    assert section.dropped_items > 0
    assert "问题0" in section.content
    assert "问题4" not in section.content


def test_rag_provider_caches_search_per_question(
    make_context: Callable[..., PromptContext],
) -> None:
    calls: List[str] = []

    def counting_search(question: str, *, k: int, min_score: float | None) -> List[Dict[str, Any]]:
        calls.append(question)
        return _fake_examples()

    provider = RagExampleProvider(1500, min_score=0.0, search_fn=counting_search)
    context = make_context("各产线缺陷率是多少")
    provider.provide(context)
    provider.provide(context)  # 总预算回收阶段可能重复构建
    assert len(calls) == 1


# ----------------------------------------------------------------------
# 组装器
# ----------------------------------------------------------------------
def test_from_settings_wires_four_providers() -> None:
    builder = SQLAgentPromptBuilder.from_settings()
    assert [provider.name for provider in builder.providers] == [
        "metadata",
        "metrics",
        "knowledge",
        "rag",
    ]
    assert builder.total_budget == settings.prompt_total_budget


def test_builder_assembles_sections_in_fixed_order(
    make_context: Callable[..., PromptContext],
) -> None:
    builder = _make_builder(
        search_fn=lambda question, *, k, min_score: _fake_examples()
    )
    result = builder.build_context(make_context("各产线缺陷率是多少"))

    assert [section.name for section in result.sections] == [
        "metadata",
        "metrics",
        "knowledge",
        "rag",
    ]
    assert "### 数据资源" in result.context_block
    assert "### 业务指标口径" in result.context_block
    assert result.context_block.index("数据资源") < result.context_block.index(
        "业务指标口径"
    )
    assert result.usage()["sections"][0]["name"] == "metadata"


def test_builder_shrinks_lowest_priority_section_first(
    make_context: Callable[..., PromptContext],
) -> None:
    def bulky_search(question: str, *, k: int, min_score: float | None) -> List[Dict[str, Any]]:
        return [
            {
                "question": "各产线缺陷率示例" + "补充说明" * 30,
                "sql": "SELECT 1",
                "score": 0.95,
            }
        ]

    builder = _make_builder(
        total_budget=700, min_section_chars=100, search_fn=bulky_search
    )
    result = builder.build_context(make_context("各产线缺陷率是多少"))

    assert result.used_chars <= 700
    rag_section = result.section("rag")
    assert rag_section is not None
    assert rag_section.max_chars < 1500  # RAG 优先级最低，最先被压缩
    assert rag_section.used_chars <= rag_section.max_chars


def test_builder_falls_back_when_every_section_is_empty() -> None:
    builder = _make_builder()
    result = builder.build_context(PromptContext(question="什么都没有"))
    assert "sql_db_list_tables" in result.context_block


def test_system_template_renders_context_block_with_braces() -> None:
    """回归测试：业务文本里出现大括号不会再破坏 Prompt 组装。

    旧实现用 ``str.format()`` 拼 prefix，只要业务说明里出现 ``{`` 就会 KeyError。
    现在动态上下文通过 ``{context_block}`` 注入，属于运行时取值，不会参与模板解析。
    """
    from langchain_core.prompts import ChatPromptTemplate

    from core.prompts import SQL_AGENT_SYSTEM_TEMPLATE

    prompt = ChatPromptTemplate.from_messages(
        [("system", SQL_AGENT_SYSTEM_TEMPLATE)]
    ).partial(dialect="mysql", top_k="50")
    text_with_braces = "字段说明：status 取值 {1: 正常, 0: 停用}"

    messages = prompt.format_messages(context_block=text_with_braces)

    assert text_with_braces in messages[0].content
    assert "mysql" in messages[0].content
    assert "工作流约束" in messages[0].content
