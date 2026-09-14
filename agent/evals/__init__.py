"""Text-to-SQL 评测：数据集、消融实验运行器与报告。

设计目标只有一个：**回答「RAG 与指标段到底有没有用」这个问题，并留下可复现的证据**。

- ``dataset.py``：评测用例（question + 参照 SQL + 是否在 RAG 库中有相似示例）
- ``runner.py``：按配置矩阵跑用例，比较「生成 SQL 的执行结果」与「参照 SQL 的执行结果」
- ``report.py``：输出 Markdown 对照表，可直接放进答辩材料

命令行入口见 ``scripts/run_eval.py``，评测方案说明见 ``docs/EVALUATION.md``。
"""

from evals.dataset import EvalCase, load_cases, validate_cases
from evals.report import render_markdown, save_report
from evals.runner import (
    CaseOutcome,
    ConfigOutcome,
    EvalConfig,
    build_prompt_builder,
    collect_reference_results,
    rows_match,
    run_matrix,
)

__all__ = [
    "CaseOutcome",
    "ConfigOutcome",
    "EvalCase",
    "EvalConfig",
    "build_prompt_builder",
    "collect_reference_results",
    "load_cases",
    "render_markdown",
    "rows_match",
    "run_matrix",
    "save_report",
    "validate_cases",
]
