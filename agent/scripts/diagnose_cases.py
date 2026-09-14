"""诊断评测里「结果不符」的用例：把生成 SQL 与参照 SQL 摆在一起看。

为什么需要这个脚本
------------------
评测报告只给「一致 / 结果不符 / 执行失败」三态。当同一批用例在**所有配置下都失败**时，
光看三态没法判断是「模型真的算错」还是「评测口径的假阴性」—— 必须看到模型实际写了什么。

本脚本按配置逐条输出：

- 模型生成的 SQL 与人工参照 SQL；
- 两侧的行数 / 列数；
- 差异定位：行数不同？列数不同？取值不同？还是**排序/取数范围**不同
  （典型如 ``LIMIT 5`` vs ``LIMIT 10``，或漏了 ``ORDER BY``）。

最后一类差异最容易被误读成模型错误：``rows_match`` 要求行数完全相同，
所以「良率最高的 5 条」写成 ``LIMIT 100`` 会判错，但语义上并没有错。
看清生成 SQL 才能区分这两种情况。

用法::

    cd agent
    python scripts/diagnose_cases.py --dataset evals/mes_prod_log.cases.jsonl \\
        --mapping mappings/mes_prod_log.mapping.yaml \\
        --configs rag_on+metrics_on+mapping_on \\
        --cases c23,c26,c28,c29,c30,c32,c37
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

_AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from evals.dataset import EvalCase, load_cases  # noqa: E402
from evals.runner import (  # noqa: E402
    EvalConfig,
    default_agent_factory,
    rows_match,
)
from schemas.agent_io import AgentQuestion  # noqa: E402
from tools.sql_executor import execute_sql  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python scripts/diagnose_cases.py",
        description="诊断「结果不符」用例：对照生成 SQL 与参照 SQL",
    )
    parser.add_argument("--dataset", required=True, help="用例集路径")
    parser.add_argument("--mapping", default="", help="mapping.yaml 路径")
    parser.add_argument(
        "--configs",
        default="rag_on+metrics_on+mapping_on",
        help="逗号分隔的配置",
    )
    parser.add_argument(
        "--cases",
        default="",
        help="逗号分隔的 case_id；留空表示诊断数据集里全部用例",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="连同「一致」的用例一起输出（默认只看不一致的）",
    )
    return parser.parse_args(argv)


def _diff_kind(
    expected: Sequence[Sequence[Any]],
    actual: Sequence[Sequence[Any]],
) -> str:
    """给差异分类，便于快速定位是哪种错。"""
    if len(expected) != len(actual):
        return f"行数不同（参照 {len(expected)} / 生成 {len(actual)}）"
    if expected and actual:
        exp_cols = {len(row) for row in expected}
        act_cols = {len(row) for row in actual}
        if exp_cols != act_cols:
            return f"列数不同（参照 {sorted(exp_cols)} / 生成 {sorted(act_cols)}）"
    if rows_match([list(r) for r in expected], [list(r) for r in actual]):
        return "一致"
    return "取值不同（行数、列数相同）"


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover
        pass

    args = parse_args(argv)
    if args.mapping:
        import os

        os.environ["ANALYSIS_MAPPING"] = args.mapping

    cases: list[EvalCase] = load_cases(Path(args.dataset))
    if args.cases:
        wanted = {item.strip() for item in args.cases.split(",") if item.strip()}
        cases = [case for case in cases if case.case_id in wanted]
    if not cases:
        print("没有选中任何用例")
        return 1

    configs = [
        EvalConfig.parse(item) for item in args.configs.split(",") if item.strip()
    ]

    # 先把参照结果算出来（零成本，且失败要立刻暴露）
    references: dict[str, list[list[Any]]] = {}
    for case in cases:
        try:
            _cols, rows = execute_sql(case.reference_sql)
            references[case.case_id] = [list(row) for row in rows]
        except Exception as exc:  # noqa: BLE001
            print(f"[参照 SQL 失败] {case.case_id}: {exc}")
            return 1

    exit_code = 0
    for config in configs:
        print("=" * 78)
        print(f"配置：{config.name}")
        print("=" * 78)
        agent = default_agent_factory(config)

        for case in cases:
            expected = references[case.case_id]
            try:
                result = agent.ask(
                    AgentQuestion(
                        question=case.question, session_id=f"diag-{case.case_id}"
                    )
                )
            except Exception as exc:  # noqa: BLE001
                print(f"\n--- {case.case_id} {case.question}\n    Agent 异常：{exc}")
                exit_code = 1
                continue

            generated = result.sql or ""
            try:
                _cols, actual_rows = execute_sql(generated) if generated else ([], [])
                actual = [list(row) for row in actual_rows]
            except Exception as exc:  # noqa: BLE001
                actual = []
                print(f"\n--- {case.case_id} {case.question}")
                print(f"    生成 SQL 执行失败：{exc}")
                print(f"    生成：{generated}")
                exit_code = 1
                continue

            kind = _diff_kind(expected, actual)
            if kind == "一致" and not args.all:
                continue
            if kind != "一致":
                exit_code = 1

            print(f"\n--- {case.case_id} [{case.category}] {case.question}")
            print(f"    判定：{kind}")
            print(f"    参照：{case.reference_sql}")
            print(f"    生成：{generated}")
            print(f"    参照行数/列数：{len(expected)}/{len(expected[0]) if expected else 0}"
                  f"    生成行数/列数：{len(actual)}/{len(actual[0]) if actual else 0}")
            if result.sql_error:
                print(f"    回放错误：{result.sql_error}")

    print()
    print("=" * 78)
    print("诊断完成。若差异是「行数不同」且生成 SQL 缺少/放大了 LIMIT，")
    print("说明它可能是评测口径的假阴性，而不是模型语义错误。")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
