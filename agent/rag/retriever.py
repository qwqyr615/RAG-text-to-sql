"""RAG 检索与上下文格式化。

两种使用方式：

1. Agent 主链路：``prompt.providers.RagExampleProvider`` 调用
   :func:`search_sql_examples`，拿到结构化示例后由 Prompt 段自己做裁剪与预算控制。
2. 非 Agent 场景（脚本 / 调试）：直接调用 :func:`get_rag_context` 拿格式化文本。

检索失败（Milvus 未启动、集合为空、嵌入服务不可用）一律返回空结果，不影响 SQL Agent
主流程。
"""

from typing import Any

from core.config import settings
from rag.sql_example_store import search_sql_examples as _search_sql_examples


def search_sql_examples(
    question: str,
    k: int | None = None,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """检索相似问题-SQL 示例（结构化）。

    参数:
        min_score: 相似度下限；不传则用 ``settings.rag_min_score``
    """
    effective_min_score = settings.rag_min_score if min_score is None else min_score
    return _search_sql_examples(
        question,
        k=k or settings.rag_top_k,
        min_score=effective_min_score,
    )


def format_sql_examples(examples: list[dict[str, Any]]) -> str:
    """把检索到的示例格式化成 Prompt 可读文本。"""
    if not examples:
        return ""

    lines = ["以下是数据库中与当前问题相似的历史问题及 SQL 示例："]
    for index, example in enumerate(examples, start=1):
        lines.append(f"\n{index}. 相似问题：{example.get('question', '')}")
        lines.append(f"   参考 SQL：{example.get('sql', '')}")
        metrics = example.get("metrics")
        tables = example.get("tables")
        if metrics:
            lines.append(f"   涉及指标：{metrics}")
        if tables:
            lines.append(f"   涉及表：{tables}")

    lines.append("\n请参考上述示例的查询方式，但必须结合当前真实表结构生成 SQL。")
    return "\n".join(lines)


def get_rag_context(
    question: str,
    k: int | None = None,
    min_score: float | None = None,
) -> str:
    """检索相似问题-SQL 示例并返回 Prompt 上下文文本（非 Agent 场景用）。"""
    try:
        examples = search_sql_examples(question, k=k, min_score=min_score)
    except Exception:  # noqa: BLE001 - 失败开放
        return ""
    return format_sql_examples(examples)
