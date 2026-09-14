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


def _render_sql_comparison(
    outcomes: Sequence[ConfigOutcome],
    reference_by_case: dict[str, str],
) -> list[str]:
    """渲染「不一致用例」的生成 SQL 与参照 SQL 对照。

    报告只给三态（一致 / 结果不符 / 执行失败）是不够的：同一批用例在**所有配置下
    都失败**时，光看三态无法区分

    - 模型真的算错了（语义错），还是
    - 评测口径的假阴性 —— 例如参照 SQL 是 ``ORDER BY x DESC LIMIT 5``，
      模型写了 ``LIMIT 10``，行数不同即判错，但语义并没有错。

    把两侧 SQL 直接摆出来，一眼就能分辨。这是 ``scripts/diagnose_cases.py``
    的静态版本：不必为了看 SQL 再花一次大模型调用。
    """
    if not outcomes or not outcomes[0].outcomes:
        return []

    # 找出「在所有配置下都没一致」的用例 —— 这批最可疑，优先展示
    always_failing: list[str] = []
    for item in outcomes[0].outcomes:
        marks = [
            (outcome.by_case_id.get(item.case_id) or None) for outcome in outcomes
        ]
        if all(mark is None or not mark.result_match for mark in marks):
            always_failing.append(item.case_id)

    if not always_failing:
        return []

    lines = [
        "",
        "## 三、不一致用例的 SQL 对照",
        "",
        f"下面 {len(always_failing)} 条在**所有配置下都没有一致**，最值得先看。",
        "若生成 SQL 与参照 SQL 语义等价、只是取数范围不同（典型是 ``LIMIT`` 大小差异），",
        "那更可能是评测口径的假阴性，而不是模型算错。",
        "",
    ]

    for case_id in always_failing:
        lines.append(f"### {case_id}")
        lines.append("")
        reference = reference_by_case.get(case_id, "")
        if reference:
            lines.append(f"- 参照 SQL：`{reference}`")
            lines.append("")
        for outcome in outcomes:
            item = outcome.by_case_id.get(case_id)
            if item is None:
                continue
            verdict = (
                "一致" if item.result_match else ("结果不符" if item.sql_ok else "执行失败")
            )
            lines.append(f"- **`{outcome.config.name}`**：{verdict}，生成 {item.row_count} 行")
            if item.error:
                lines.append(f"  - 错误：{item.error}")
            lines.append(f"  - 生成 SQL：`{item.generated_sql or '（未提取到 SQL）'}`")
        lines.append("")

    return lines


def render_markdown(
    outcomes: Sequence[ConfigOutcome],
    *,
    cases: Sequence[EvalCase] | None = None,
    generated_at: str | None = None,
    dataset: str = "",
    mapping: str = "",
) -> str:
    """渲染评测报告（Markdown）。

    参数:
        outcomes: 各配置的结果
        cases: 用例集（用于抬头统计）
        generated_at: 生成时间，便于测试固定输出
        dataset: 用例集文件名（换表对照时用于说明数据源）
        mapping: 本次生效的 ``mapping.yaml`` 路径
    """
    timestamp = generated_at or datetime.now(timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    has_mapping_axis = any(outcome.config.mapping is not None for outcome in outcomes)

    lines: list[str] = [
        "# Text-to-SQL 评测报告：RAG / 指标段 / 字段映射的消融对照",
        "",
        f"- 生成时间：{timestamp}",
        f"- 配置数：{len(outcomes)}",
    ]
    if dataset:
        lines.append(f"- 用例集：`{dataset}`")
    if mapping:
        lines.append(f"- 字段映射：`{mapping}`")
    elif has_mapping_axis:
        lines.append("- 字段映射：未配置（``mapping_off`` 只使用数据库列名与 COMMENT）")
    else:
        lines.append("- 字段映射：沿用环境配置 ``ANALYSIS_MAPPING``")

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
            "| 配置 | 可执行率 | 结果一致率 | 列容错一致率 | 相似问题一致率 | 新问题一致率 | 平均耗时 | 平均 Prompt 字符 |",
            "|---|---|---|---|---|---|---|---|",
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
            "| `{name}` | {exec_rate} | {acc} | {proj} | {seen_acc} | {new_acc} | {latency:.0f} ms | {chars:.0f} |".format(
                name=outcome.config.name,
                exec_rate=_pct(outcome.execution_rate()),
                acc=_pct(outcome.accuracy()),
                proj=_pct(outcome.accuracy_projection()),
                seen_acc=_pct(outcome.accuracy(seen=True)),
                new_acc=_pct(outcome.accuracy(seen=False)),
                latency=outcome.avg_latency_ms(),
                chars=outcome.avg_prompt_chars(),
            )
        )

    # 「只是多带了几列」的用例清单一并列出：这是严格口径与列容错口径的差值来源
    extra_only: dict[str, list[str]] = {
        outcome.config.name: outcome.extra_column_only() for outcome in outcomes
    }
    if any(extra_only.values()):
        lines.extend(["", "严格口径判错、但列容错口径判对的用例（即「答案对、多带了几列上下文」）：", ""])
        for name, case_ids in extra_only.items():
            lines.append(f"- `{name}`：{('、'.join(case_ids)) or '无'}")
        lines.append("")

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
                    marks.append("一致")
                elif item.sql_ok:
                    marks.append("结果不符")
                else:
                    marks.append("执行失败")
            lines.append(
                f"| {case_id} | {question} | {category} | {seen} | "
                + " | ".join(marks)
                + " |"
            )

        # 不一致用例的 SQL 对照。没有这一节，每次想问「模型到底写了什么」都得重跑
        # 一遍评测（几十到上百次大模型调用），而重跑的结果还可能因温度而不同 ——
        # 那样就永远分不清「模型语义错」与「评测口径假阴性」。
        lines.extend(
            _render_sql_comparison(
                outcomes,
                {case.case_id: case.reference_sql for case in (cases or [])},
            )
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
            "- 看**字段映射**（`mapping_on` vs `mapping_off`，同表同用例）："
            "差值就是「标准字段口径 + 单位换算 + 示例改写」这一层的净增益。"
            "客户库列名与业务词差异越大、单位越不统一，这一层的增益应该越明显。",
            "- 看**可执行率**：它衡量字段名是否写对。`mapping_off` 若可执行率尚可但"
            "结果一致率很低，说明模型猜对了列名却算错了口径（典型是漏掉单位换算）。",
            "- 看**平均 Prompt 字符**：增益是否值得付出的上下文成本。",
            "",
            "## 四、口径与局限",
            "",
            "- 参照 SQL 由人工编写，其执行结果即标准答案；结果集比较不考虑行顺序。",
            "- 换表对照（客户侧用例集）中的参照 SQL 由**映射自动翻译**生成，"
            "翻译前已用 `scripts/build_customer_cases.py` 逐条验证「翻译后结果集与"
            "标准表完全一致」（见 `evals/reports/*_mapping_check.md`）。"
            "因此该对照里出现的偏差只能归因于模型，而不是映射本身。",
            "- 客户表 45 列**没有任何数据库注释**，这正是真实 MES 视图的常见形态；"
            "`mapping_off` 配置因此退化为「只给模型列名让它猜」，是映射价值的合理下界。",
            "- 当前数据底座**没有时间列**，因此用例集不含「最近 7 天趋势」这类时间维问题；"
            "要覆盖它们需要先给服务层补 `production_time` 之类的字段。",
            "- 单轮评测：每条用例使用独立会话（``session_max_turns=0``），"
            "多轮追问能力不在这张表里体现。",
            "",
        ]
    )
    return "\n".join(lines)


def save_report(
    text: str, directory: Path | None = None, *, tag: str = ""
) -> Path:
    """把报告写入 ``evals/reports/``，返回文件路径。

    ``tag`` 会加进文件名，便于把「小样本冒烟」与「全量矩阵」的报告区分开，
    避免多次实验互相覆盖。
    """
    target_dir = Path(directory) if directory else REPORTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{tag}" if tag else ""
    path = target_dir / f"eval_{stamp}{suffix}.md"
    path.write_text(text, encoding="utf-8")
    return path
