"""评测入口：跑「RAG 开/关 × 指标段 开/关」的对照实验。

用法::

    cd agent

    # 1) 先校验用例集与参照 SQL（不调用大模型，零成本）
    python scripts/run_eval.py --validate

    # 2) 跑完整对照矩阵（会真实调用 DeepSeek 与 Milvus）
    python scripts/run_eval.py

    # 3) 只跑 8 条用例、只对比两个配置，快速验证
    python scripts/run_eval.py --limit 8 --configs rag_off+metrics_on,rag_on+metrics_on

报告会打印到控制台，并保存到 evals/reports/eval_YYYYmmdd_HHMMSS.md。

注意：跑真实矩阵前请先 ``python -m rag.cli ingest`` 把示例灌进 Milvus，
否则 rag_on 与 rag_off 没有区别。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许用 `python scripts/run_eval.py` 直接运行（该方式下 sys.path[0] 是 scripts/）
_AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from evals.dataset import load_cases, summarize, validate_cases  # noqa: E402
from evals.report import render_markdown, save_report  # noqa: E402
from evals.runner import (  # noqa: E402
    DEFAULT_CONFIG_NAMES,
    EvalConfig,
    collect_reference_results,
    run_matrix,
)


def _configure_stdout() -> None:
    """Windows 控制台可能是 GBK：遇到无法编码的字符时替换而不是直接崩溃。"""
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover - 非常规 stdout
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/run_eval.py",
        description="Text-to-SQL 消融评测：RAG 开/关 × 指标段 开/关",
    )
    parser.add_argument(
        "--configs",
        default=",".join(DEFAULT_CONFIG_NAMES),
        help="逗号分隔的配置，例如 rag_off+metrics_on,rag_on+metrics_on",
    )
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条用例（0 表示全部）")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="只校验用例集并试跑参照 SQL，不调用大模型",
    )
    parser.add_argument("--no-save", action="store_true", help="不写报告文件")
    return parser


def _cmd_validate(cases) -> int:
    results, problems = collect_reference_results(cases)
    print(f"用例 {len(cases)} 条，参照 SQL 执行成功 {len(results)} 条")
    for case in cases:
        rows = results.get(case.case_id)
        status = "OK" if rows is not None else "FAIL"
        print(f"  [{status}] {case.case_id} 行数={len(rows) if rows else 0} {case.question}")

    if problems:
        print("\n参照 SQL 存在问题：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\n校验通过：用例集与参照 SQL 都可用。")
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = build_parser().parse_args(argv)

    cases = load_cases()
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        print("没有加载到任何用例，请检查 evals/cases.jsonl")
        return 1

    problems = validate_cases(cases)
    if problems:
        print(f"用例集校验未通过（{len(problems)} 处）：")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    info = summarize(cases)
    print(
        f"用例 {info['total']} 条（示例库中有同型示例 {info['seen_in_rag']} 条，"
        f"全新问题 {info['new_questions']} 条）"
    )
    print(f"类别分布：{info['categories']}")

    if args.validate:
        return _cmd_validate(cases)

    configs = [
        EvalConfig.parse(item) for item in args.configs.split(",") if item.strip()
    ]
    print("配置：" + "，".join(config.name for config in configs))

    reference, ref_problems = collect_reference_results(cases)
    if ref_problems:
        print("参照 SQL 执行失败，已中止：")
        for problem in ref_problems:
            print(f"  - {problem}")
        return 1

    def progress(config: EvalConfig, outcome) -> None:
        if outcome.result_match:
            mark = "OK"
        elif outcome.sql_ok:
            mark = "MISMATCH"
        else:
            mark = "FAIL"
        print(
            f"  [{config.name}] {outcome.case_id} {mark} "
            f"{outcome.latency_ms / 1000:.1f}s {outcome.question}"
        )

    outcomes = run_matrix(
        cases, configs, reference_results=reference, progress=progress
    )

    report = render_markdown(outcomes, cases=cases)
    print("\n" + report)

    if not args.no_save:
        path = save_report(report)
        print(f"\n报告已保存：{path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
