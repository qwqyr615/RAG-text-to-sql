"""问题-SQL 示例向量库。

负责：
- 从 JSON 加载问题-SQL 示例
- 校验示例（字段完整性、SQL 只读、引用的表真实存在）
- 调用 SiliconFlow 嵌入模型并写入 / 检索 Milvus

对外命令见 ``rag/cli.py``（ingest / search / validate / stats / clear）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from core.config import BASE_DIR, settings
from rag.embeddings import get_embedding_model
from rag.milvus_store import (
    PK_FIELD,
    TEXT_FIELDS,
    VECTOR_FIELD,
    ensure_collection,
    get_client,
)
from tools.sql_guard import extract_table_refs, validate_readonly_sql

EXAMPLE_FILE = BASE_DIR / "rag" / "examples" / "sql_examples.json"


def load_examples(path: Path | None = None) -> list[dict[str, Any]]:
    """加载问题-SQL 示例。"""
    example_file = path or EXAMPLE_FILE
    if not example_file.exists():
        return []
    return json.loads(example_file.read_text(encoding="utf-8"))


def validate_examples(
    examples: Sequence[dict[str, Any]],
    *,
    known_tables: Iterable[str] | None = None,
) -> list[str]:
    """校验示例文件，返回问题描述列表（空列表表示通过）。

    这是最容易被忽略、但影响最大的一类问题：示例 SQL 引用了已经删除的表
    （例如路线 B 删除 dim_* 之后仍写 ``JOIN dim_line``），被注入 Prompt 后
    会直接把模型带偏。健康检查与 ``rag.cli ingest`` 都会调用它。
    """
    problems: list[str] = []
    seen_ids: set[str] = set()
    allowed = {str(name).lower() for name in known_tables} if known_tables else None

    for index, example in enumerate(examples, start=1):
        label = str(example.get("id") or f"#{index}")

        for field in ("id", "question", "sql"):
            if not str(example.get(field) or "").strip():
                problems.append(f"{label}: 缺少必填字段 {field}")

        example_id = str(example.get("id") or "")
        if example_id and example_id in seen_ids:
            problems.append(f"{label}: id 重复")
        seen_ids.add(example_id)

        sql = str(example.get("sql") or "").strip()
        if not sql:
            continue

        try:
            validate_readonly_sql(sql)
        except Exception as exc:  # noqa: BLE001 - ReadOnlyViolation
            problems.append(f"{label}: SQL 未通过只读校验（{exc}）")

        refs = extract_table_refs(sql)
        declared = {str(name).lower() for name in (example.get("tables") or [])}
        if allowed is not None:
            unknown = sorted(refs - allowed)
            if unknown:
                problems.append(f"{label}: SQL 引用了数据底座中不存在的表 {unknown}")
        undeclared = sorted(refs - declared)
        if undeclared:
            problems.append(f"{label}: SQL 用到的表 {undeclared} 未在 tables 字段声明")

    return problems


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
    """把示例问题写入 Milvus。

    参数:
        drop_old: True 为重建（删除集合后全量重灌）；False 为增量追加。
    """
    examples = load_examples()
    if not examples:
        return 0

    client = get_client()
    try:
        ensure_collection(client, drop_old=drop_old)
        return _write_examples(client, examples)
    finally:
        client.close()


def collection_row_count() -> int | None:
    """集合内示例条数；未建集合返回 0，Milvus 不可用返回 None。"""
    try:
        client = get_client()
    except Exception:  # noqa: BLE001 - Milvus 未启动
        return None

    try:
        if not client.has_collection(settings.milvus_collection_name):
            return 0
        stats = client.get_collection_stats(settings.milvus_collection_name) or {}
        return int(stats.get("row_count", 0))
    except Exception:  # noqa: BLE001
        return None
    finally:
        client.close()


def drop_sql_examples() -> None:
    """删除整个示例集合。"""
    client = get_client()
    try:
        if client.has_collection(settings.milvus_collection_name):
            client.drop_collection(settings.milvus_collection_name)
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
