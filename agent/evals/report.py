"""评测报告：把配置矩阵的结果渲染成 Markdown 对照表。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from core.config import BASE_DIR
from evals.dataset import EvalCase, summarize
from evals.runner import ConfigOutcome

__all__ = ["REPORTS_DIR", "render_markdown", "save_report"]

REPORTS_DIR = BASE_DIR / "evals" / "reports"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _delta(value: float, baseline: float) -> str:
    diff = (value - baseline) * 100
    if abs(diff) < 0.05:
        return "—"
    return f"{diff:+.1f}pp"


def render_markdown(
    outcomes: Sequence[ConfigOutcome],
    *,
    cases: Sequence[EvalCase] | None = None,
    generated_at: str | None = None,
) -> str:
    """渲染评测报告（Markdown）。"""
    timestamp = generated_at or datetime.now(timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    lines: list[str] = [
        "# Text-to-SQL 评测报告：RAG 与指标段的消融对照",
        "",
        f"- 生成时间：{timestamp}",
        f"- 配置数：{len(outcomes)}",
    ]

    if cases:
        info = summarize(cases)
        lines.append(f"- 用例总数：{info['total']}")
        lines.append(
            f"- 其中 RAG 示例库中有同型示例的：{info['seen_in_rag']} 条，"
            f"全新问题：{info['new_questions']} 条"
        )
        categories = ", ".join(
            f"{name} {count}" for name, count in sorted(info["categories"].items())
        )
        lines.append(f"- 类别分布：{categories}")
    lines.append("")

    lines.extend(
        [
            "## 一、配置对照表",
            "",
            "判定标准为**执行结果一致率**：生成的 SQL 与人工参照 SQL 都真正执行，"
            "比较结果集（行数、列数、取值；行顺序无关，数值按 0.1% 相对容差）。",
            "",
            "| 配置 | 可执行率 | 结果一致率 | 相似问题一致率 | 新问题一致率 | 平均耗时 | 平均 Prompt 字符 |",
            "|---|---|---|---|---|---|---|",
        ]
    )

    baseline: ConfigOutcome | None = None
    for outcome in outcomes:
        if baseline is None or (
            not outcome.config.rag and outcome.config.metrics
        ):
            baseline = outcome

    for outcome in outcomes:
        lines.append(
            "| `{name}` | {exec_rate} | {acc} | {seen_acc} | {new_acc} | {latency:.0f} ms | {chars:.0f} |".format(
                name=outcome.config.name,
                exec_rate=_pct(outcome.execution_rate()),
                acc=_pct(outcome.accuracy()),
                seen_acc=_pct(outcome.accuracy(seen=True)),
                new_acc=_pct(outcome.accuracy(seen=False)),
                latency=outcome.avg_latency_ms(),
                chars=outcome.avg_prompt_chars(),
            )
        )

    if baseline is not None:
        lines.extend(["", f"以 `{baseline.config.name}` 为基线的增量：", ""])
        lines.append("| 配置 | 结果一致率增量 | 相似问题 | 新问题 |")
        lines.append("|---|---|---|---|")
        for outcome in outcomes:
            if outcome is baseline:
                continue
            lines.append(
                "| `{name}` | {total} | {seen} | {new} |".format(
                    name=outcome.config.name,
                    total=_delta(outcome.accuracy(), baseline.accuracy()),
                    seen=_delta(
                        outcome.accuracy(seen=True), baseline.accuracy(seen=True)
                    ),
                    new=_delta(
                        outcome.accuracy(seen=False), baseline.accuracy(seen=False)
                    ),
                )
            )

    if outcomes and outcomes[0].outcomes:
        lines.extend(["", "## 二、逐例明细", ""])
        header = "| 用例 | 问题 | 类别 | 在示例库 | " + " | ".join(
            f"`{outcome.config.name}`" for outcome in outcomes
        ) + " |"
        separator = "|---|---|---|---|" + "---|" * len(outcomes)
        lines.extend([header, separator])

        for index, _ in enumerate(outcomes[0].outcomes):
            case_id = outcomes[0].outcomes[index].case_id
            question = outcomes[0].outcomes[index].question
            category = outcomes[0].outcomes[index].category
            seen = "是" if outcomes[0].outcomes[index].seen_in_rag else "否"
            marks = []
            for outcome in outcomes:
                item = outcome.by_case_id.get(case_id)
                if item is None:
                    marks.append("—")
                elif item.result_match:
                    marks.append("✅")
                elif item.sql_ok:
                    marks.append("⚠️ 结果不符")
                else:
                    marks.append("❌ 执行失败")
            lines.append(
                f"| {case_id} | {question} | {category} | {seen} | "
                + " | ".join(marks)
                + " |"
            )

    lines.extend(
        [
            "",
            "## 三、怎么解读这张表",
            "",
            "- 看**相似问题一致率**：RAG 段只有在示例库里存在同型问题时才应该带来提升；"
            "如果这一列没提升，说明检索或示例库有问题。",
            "- 看**新问题一致率**：RAG 不应该显著拖累全新问题（示例是参考而不是答案）；"
            "如果掉了，通常是 ``RAG_MIN_SCORE`` 太低、把不相似的示例也塞进了 Prompt。",
            "- 看**指标段**：对比 `rag_off+metrics_on` 与 `rag_off+metrics_off`，"
            "差异反映业务口径对齐（字段映射、计算口径）的价值。",
            "- 看**平均 Prompt 字符**：增益是否值得付出的上下文成本。",
            "",
            "## 四、口径与局限",
            "",
            "- 参照 SQL 由人工编写，其执行结果即标准答案；结果集比较不考虑行顺序。",
            "- 生成 SQL 与参照 SQL 的表结构一致（单表 `fact_production_record`），"
            "因此本表衡量的是「查询构造正确性」，不涉及多表 Join 消歧。",
            "- 当前数据底座**没有时间列**，因此用例集不含「最近 7 天趋势」这类时间维问题；"
            "要覆盖它们需要先给服务层补 `production_time` 之类的字段。",
            "- 单轮评测：每条用例使用独立会话（``session_max_turns=0``），"
            "多轮追问能力不在这张表里体现。",
            "",
        ]
    )
    return "\n".join(lines)


def save_report(text: str, directory: Path | None = None) -> Path:
    """把报告写入 ``evals/reports/``，返回文件路径。"""
    target_dir = Path(directory) if directory else REPORTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = target_dir / f"eval_{stamp}.md"
    path.write_text(text, encoding="utf-8")
    return path
