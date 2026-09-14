"""RAG 示例库管理命令：question-SQL 示例的统一入口。

为什么不是 vanna 的 ``train()``
-------------------------------
vanna 的 ``train(ddl=..., sql=..., documentation=...)`` 会把三类文本都灌进向量库。
对本项目而言只有第三类需要：

- **ddl 不训练**：单表宽表模型下表结构直接来自数据库（DDL ``COMMENT`` + inspector），
  再训练一份手写 DDL 只会过期，反而误导模型；
- **documentation 不训练**：业务口径以结构化形式维护在 ``core/metrics.py`` 与
  ``knowledge/knowledge_base.py``，比自由文本可控，而且能校验字段是否真实存在；
- **question-SQL 示例需要管理**：这是本模块做的事。

用法::

    cd agent
    python -m rag.cli stats
    python -m rag.cli validate
    python -m rag.cli search "各产线的平均缺陷率" --top-k 3 --min-score 0.4
    python -m rag.cli ingest                  # 重建：删除集合后全量重灌
    python -m rag.cli ingest --append         # 增量：只追加新示例
    python -m rag.cli clear --yes
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from core.config import settings
from metadata.metadata_service import get_metadata_json
from rag.sql_example_store import (
    collection_row_count,
    drop_sql_examples,
    ingest_sql_examples,
    load_examples,
    search_sql_examples,
    validate_examples,
)


def _known_tables() -> list[str] | None:
    """线上业务表清单（发现模式的结果）；读取失败时返回 None（跳过表名校验）。

    表范围来自 ``metadata_service`` 的发现过程，不再读硬编码白名单 ——
    换客户库时这里要跟着变的只有 ``mapping.yaml``。
    """
    try:
        metadata = get_metadata_json()
    except Exception as exc:  # noqa: BLE001 - 校验是可选的，读不到就跳过
        print(f"[warn] 读取元数据失败，跳过表名校验：{exc}")
        return None
    tables = [table["table_name"] for table in metadata.get("tables") or []]
    if not tables:
        print("[warn] 发现模式下没有可见业务表，跳过表名校验")
        return None
    return tables


def _print_problems(problems: Sequence[str]) -> None:
    for problem in problems:
        print(f"  - {problem}")


def _cmd_validate(args: argparse.Namespace) -> int:
    examples = load_examples()
    if not examples:
        print("示例文件为空或不存在。")
        return 1

    problems = validate_examples(
        examples, known_tables=_known_tables() if args.check_schema else None
    )
    print(f"示例文件：{len(examples)} 条")
    if problems:
        print(f"发现问题 {len(problems)} 处：")
        _print_problems(problems)
        return 1
    print("校验通过：字段完整、SQL 通过只读校验、引用的表都存在。")
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    examples = load_examples()
    count = collection_row_count()
    print(f"Milvus      ：{settings.milvus_uri}/{settings.milvus_db_name}")
    print(f"Collection  ：{settings.milvus_collection_name}")
    print(f"集合内示例  ：{'读取失败' if count is None else count} 条")
    print(f"示例文件    ：{len(examples)} 条")
    print(
        f"检索参数    ：top_k={settings.rag_top_k}，"
        f"min_score={settings.rag_min_score}，"
        f"RAG 段预算={settings.prompt_rag_budget} 字符，"
        f"启用={settings.rag_enabled}"
    )
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    examples = search_sql_examples(args.question, k=args.top_k, min_score=args.min_score)
    if not examples:
        print("没有检索到达到相似度阈值的示例。")
        return 1
    for index, example in enumerate(examples, start=1):
        print(f"{index}. 相似度 {example.get('score')}")
        print(f"   问题：{example.get('question')}")
        print(f"   SQL ：{example.get('sql')}")
        if example.get("tables"):
            print(f"   涉及表：{example.get('tables')}")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    examples = load_examples()
    if not examples:
        print("示例文件为空或不存在，未执行导入。")
        return 1

    problems = validate_examples(examples, known_tables=_known_tables())
    if problems:
        print(f"示例校验未通过（{len(problems)} 处），已中止导入：")
        _print_problems(problems)
        return 1
    print(f"示例校验通过：{len(examples)} 条")

    count = ingest_sql_examples(drop_old=not args.append)
    action = "追加" if args.append else "重建并导入"
    print(f"{action}完成：{count} 条")
    return 0


def _cmd_clear(args: argparse.Namespace) -> int:
    if not args.yes:
        answer = input(f"确认删除集合 {settings.milvus_collection_name}？（yes/no）")
        if answer.strip().lower() not in {"y", "yes"}:
            print("已取消。")
            return 1
    drop_sql_examples()
    print(f"已删除集合 {settings.milvus_collection_name}。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rag.cli",
        description="question-SQL 示例库管理（vanna train 的等价物，只保留示例这一类）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="校验示例文件（不调用 Milvus）")
    validate_parser.add_argument(
        "--check-schema",
        action="store_true",
        help="额外校验示例引用的表在线上库中真实存在",
    )
    validate_parser.set_defaults(func=_cmd_validate)

    stats_parser = subparsers.add_parser("stats", help="查看集合与示例文件概况")
    stats_parser.set_defaults(func=_cmd_stats)

    search_parser = subparsers.add_parser("search", help="检索相似示例")
    search_parser.add_argument("question", help="用户问题")
    search_parser.add_argument("--top-k", type=int, default=None)
    search_parser.add_argument("--min-score", type=float, default=None)
    search_parser.set_defaults(func=_cmd_search)

    ingest_parser = subparsers.add_parser("ingest", help="导入示例到 Milvus")
    ingest_parser.add_argument(
        "--append", action="store_true", help="增量追加，不删除已有数据"
    )
    ingest_parser.set_defaults(func=_cmd_ingest)

    clear_parser = subparsers.add_parser("clear", help="删除整个集合")
    clear_parser.add_argument("--yes", action="store_true", help="跳过确认")
    clear_parser.set_defaults(func=_cmd_clear)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


def _configure_stdout() -> None:
    """Windows 控制台可能是 GBK：遇到无法编码的字符时替换而不是直接崩溃。"""
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover - 非常规 stdout
        pass


if __name__ == "__main__":
    sys.exit(main())
