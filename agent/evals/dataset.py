"""评测数据集：加载与校验。

用例格式（``evals/cases.jsonl``，一行一条 JSON）::

    {"case_id": "c01", "question": "...", "reference_sql": "...",
     "category": "指标聚合", "seen_in_rag": false, "notes": ""}

- ``reference_sql`` 是人工编写的参照 SQL，它的**执行结果**就是标准答案；
- ``seen_in_rag`` 标记该问题在 RAG 示例库中是否有同型示例，
  用于把结果拆成「相似问题」与「新问题」两组——这是判断 RAG 是否真的有效、
  而不是靠过拟合刷分的关键。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from core.config import BASE_DIR
from tools.sql_guard import extract_table_refs, validate_readonly_sql

__all__ = [
    "CASES_FILE",
    "EvalCase",
    "load_cases",
    "resolve_allowed_tables",
    "validate_cases",
]

CASES_FILE = BASE_DIR / "evals" / "cases.jsonl"

#: 没有映射可用时的兜底表清单（标准服务层）。
#: 正常情况下允许的表由 :func:`resolve_allowed_tables` 从**发现模式**取得 ——
#: 客户库的表名不该写在这份代码里。
DEFAULT_ALLOWED_TABLES = ("fact_production_record",)


@dataclass(frozen=True)
class EvalCase:
    """一条评测用例。"""

    case_id: str
    question: str
    reference_sql: str
    category: str = "未分类"
    seen_in_rag: bool = False
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "EvalCase":
        return cls(
            case_id=str(data.get("case_id") or ""),
            question=str(data.get("question") or ""),
            reference_sql=str(data.get("reference_sql") or ""),
            category=str(data.get("category") or "未分类"),
            seen_in_rag=bool(data.get("seen_in_rag", False)),
            notes=str(data.get("notes") or ""),
        )


def load_cases(path: Path | None = None) -> list[EvalCase]:
    """加载评测用例（JSONL）。"""
    cases_file = path or CASES_FILE
    if not cases_file.is_file():
        return []

    cases: list[EvalCase] = []
    for line_number, raw in enumerate(
        cases_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        try:
            payload = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{cases_file.name} 第 {line_number} 行不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{cases_file.name} 第 {line_number} 行应为 JSON 对象")
        cases.append(EvalCase.from_dict(payload))
    return cases


def resolve_allowed_tables(mapping_path: str | Path | None = None) -> tuple[str, ...]:
    """允许出现在参照 SQL 里的表：来自发现模式（扫描 + 排除 + 映射）。

    为什么不让调用方传一个常量列表：参照 SQL 的表名取决于当前接的是哪个数据源，
    写死在代码里就意味着「换一张表」时必须改代码 —— 正是本次要消除的东西。
    发现过程失败时退回 :data:`DEFAULT_ALLOWED_TABLES`，保证离线跑校验不会崩。
    """
    try:
        from metadata.mapping import resolve_mapping
        from metadata.metadata_service import get_metadata_json
    except Exception:  # pragma: no cover - 依赖缺失时走兜底
        return DEFAULT_ALLOWED_TABLES

    try:
        mapping = resolve_mapping(mapping_path, use_cache=False)
        metadata = get_metadata_json(mapping=mapping, use_mapping_cache=False)
    except Exception:  # noqa: BLE001 - 连不上库时不该让用例校验失败
        return DEFAULT_ALLOWED_TABLES

    tables = tuple(
        str(table.get("table_name"))
        for table in metadata.get("tables") or []
        if table.get("table_name")
    )
    return tables or DEFAULT_ALLOWED_TABLES


def validate_cases(
    cases: Sequence[EvalCase], *, allowed_tables: Iterable[str] | None = None
) -> list[str]:
    """校验用例集，返回问题列表（空表示通过）。

    ``allowed_tables`` 显式传 ``None`` 时会**现场解析**（发现模式），
    这样客户侧用例集（表名 ``mes_prod_log``）也能通过校验，不需要改代码。
    """
    problems: list[str] = []
    seen_ids: set[str] = set()
    allowed = {
        name.lower()
        for name in (
            allowed_tables if allowed_tables is not None else resolve_allowed_tables()
        )
    }

    for index, case in enumerate(cases, start=1):
        label = case.case_id or f"#{index}"
        if not case.case_id:
            problems.append(f"{label}: 缺少 case_id")
        if case.case_id in seen_ids:
            problems.append(f"{label}: case_id 重复")
        seen_ids.add(case.case_id)

        if not case.question.strip():
            problems.append(f"{label}: 缺少 question")
        if not case.reference_sql.strip():
            problems.append(f"{label}: 缺少 reference_sql")
            continue

        try:
            validate_readonly_sql(case.reference_sql)
        except Exception as exc:  # noqa: BLE001 - ReadOnlyViolation
            problems.append(f"{label}: 参照 SQL 未通过只读校验（{exc}）")

        unknown = sorted(extract_table_refs(case.reference_sql) - allowed)
        if unknown:
            problems.append(f"{label}: 参照 SQL 引用了数据底座中不存在的表 {unknown}")

    return problems


def summarize(cases: Sequence[EvalCase]) -> dict[str, object]:
    """用例集概况，用于报告抬头。"""
    seen = [case for case in cases if case.seen_in_rag]
    categories: dict[str, int] = {}
    for case in cases:
        categories[case.category] = categories.get(case.category, 0) + 1
    return {
        "total": len(cases),
        "seen_in_rag": len(seen),
        "new_questions": len(cases) - len(seen),
        "categories": categories,
    }
