"""RAG 检索与上下文格式化。

对上层只暴露一个函数：get_rag_context(question)。
Text2SQLAgent 在生成 SQL 前调用它，把相似问题/SQL 示例加入提示词。
"""

from typing import Any

from core.config import settings
from rag.sql_example_store import search_sql_examples


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


def get_rag_context(question: str, k: int | None = None) -> str:
    """检索相似问题-SQL 示例并返回 Prompt 上下文。

    如果 Milvus 未启动或集合为空，则返回空字符串，不影响正常 SQL Agent 流程。
    """
    try:
        examples = search_sql_examples(question, k=k or settings.rag_top_k)
    except Exception:
        return ""
    return format_sql_examples(examples)