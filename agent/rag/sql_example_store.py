"""问题-SQL 示例向量库。

负责：
- 从 JSON 加 Example 问答对
- 调用 SiliconFlow 嵌入模型
- 写入/检索 Milvus
"""

import json
from pathlib import Path
from typing import Any

from core.config import BASE_DIR, settings
from rag.embeddings import get_embedding_model
from rag.milvus_store import (
    PK_FIELD,
    TEXT_FIELDS,
    VECTOR_FIELD,
    ensure_collection,
    get_client,
)

EXAMPLE_FILE = BASE_DIR / "rag" / "examples" / "sql_examples.json"


def load_examples() -> list[dict[str, Any]]:
    """加载问题-SQL 示例。"""
    if not EXAMPLE_FILE.exists():
        return []
    return json.loads(EXAMPLE_FILE.read_text(encoding="utf-8"))


def _write_examples(client, examples: list[dict[str, Any]]) -> int:
    """把示例向量化并写入集合。"""
    questions = [example["question"] for example in examples]
    vectors = get_embedding_model().embed_documents(questions)

    data = []
    for example, vector in zip(examples, vectors):
        data.append(
            {
                PK_FIELD: example["id"],
                VECTOR_FIELD: vector,
                "question": example["question"],
                "sql": example["sql"],
                "metrics": "、".join(example.get("metrics", [])),
                "tables": "、".join(example.get("tables", [])),
                "description": example.get("description", ""),
            }
        )

    client.insert(
        collection_name=settings.milvus_collection_name,
        data=data,
    )
    client.flush(settings.milvus_collection_name)
    return len(data)


def ingest_sql_examples(drop_old: bool = True) -> int:
    """把示例问题写入 Milvus。"""
    examples = load_examples()
    if not examples:
        return 0

    client = get_client()
    try:
        ensure_collection(client, drop_old=drop_old)
        return _write_examples(client, examples)
    finally:
        client.close()


def _ensure_ready(client) -> None:
    """确保集合可用；若集合因维度变化被重建，则用当前模型重新灌入示例。"""
    rebuilt = ensure_collection(client, drop_old=False)
    if not rebuilt:
        return

    examples = load_examples()
    if examples:
        _write_examples(client, examples)


def search_sql_examples(
    question: str,
    k: int | None = None,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """检索与用户问题最相似的历史问题/SQL 示例。

    参数:
        question: 用户问题
        k: 最多返回多少条
        min_score: 相似度下限（COSINE，越大越相似）；低于该值的示例直接丢弃，
                   避免不相似的示例污染 Prompt。None 表示不过滤。
    """
    client = get_client()
    try:
        if not client.has_collection(settings.milvus_collection_name):
            return []

        _ensure_ready(client)
        query_vector = get_embedding_model().embed_query(question)
        results = client.search(
            collection_name=settings.milvus_collection_name,
            data=[query_vector],
            limit=k or settings.rag_top_k,
            output_fields=TEXT_FIELDS,
            search_params={"metric_type": "COSINE"},
        )

        examples: list[dict[str, Any]] = []
        for hit in results[0]:
            entity = hit.get("entity", {})
            score = hit.get("distance")
            if min_score is not None and score is not None:
                try:
                    if float(score) < float(min_score):
                        continue
                except (TypeError, ValueError):
                    pass

            example = {field: entity.get(field, "") for field in TEXT_FIELDS}
            example["score"] = score
            examples.append(example)

        examples.sort(key=lambda item: -_score_of(item))
        return examples
    finally:
        client.close()


def _score_of(example: dict[str, Any]) -> float:
    try:
        return float(example.get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0
