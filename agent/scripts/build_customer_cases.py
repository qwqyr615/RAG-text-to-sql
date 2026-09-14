"""生成「客户表口径」的评测用例集，并验证映射的语义等价性。

这个脚本是**零成本**的可行性证明：在调用任何大模型之前，先用数据库自己证明
「映射是对的」。逻辑只有一条：

    如果映射完整，那么把标准参照 SQL 按映射翻译后执行，结果集必须与原文完全一致。

于是它对每条用例做三件事：

1. 用 :class:`ColumnRewriter` 把 ``reference_sql`` 从标准字段口径翻译成客户列口径
   （表名替换 + 列名替换 + 单位换算），记录每条实际生效的替换；
2. 在客户表上执行翻译后的 SQL，与标准表上的结果做 ``rows_match`` 比较；
3. 输出 ``evals/<profile>.cases.jsonl``（供评测使用）与一份等价性报告。

任何一条不等价都说明映射有洞（列漏映射、单位换算错、枚举值写错），必须在跑评测
之前修掉 —— 否则测出来的分数到底反映「模型能力」还是「映射 bug」就说不清了。

用法::

    cd agent
    python scripts/build_customer_cases.py --mapping mappings/mes_prod_log.mapping.yaml
    python scripts/build_customer_cases.py --mapping ... --source-table fact_production_record
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 允许用 `python scripts/xxx.py` 直接运行（该方式下 sys.path[0] 是 scripts/）
_AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from evals.dataset import load_cases  # noqa: E402
from evals.runner import rows_match  # noqa: E402
from metadata.mapping import resolve_mapping  # noqa: E402
from tools.sql_executor import execute_sql  # noqa: E402


@dataclass
class CaseCheck:
    """一条用例的翻译与等价性结论。"""

    case_id: str
    question: str
    category: str
    seen_in_rag: bool
    source_sql: str
    target_sql: str
    rewrites: dict[str, str] = field(default_factory=dict)
    source_rows: int = 0
    target_rows: int = 0
    equivalent: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.equivalent and not self.error


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python scripts/build_customer_cases.py",
        description="把标准参照 SQL 翻译到客户表口径，并验证映射的语义等价性",
    )
    parser.add_argument("--mapping", required=True, help="mapping.yaml 路径")
    parser.add_argument(
        "--source-table",
        default="fact_production_record",
        help="标准参照 SQL 使用的表名（默认 fact_production_record）",
    )
    parser.add_argument(
        "--target-table",
        default="",
        help="客户目标表名；默认取映射里第一张 role=fact 的表",
    )
    parser.add_argument(
        "--cases",
        default="",
        help="源用例集路径；默认 evals/cases.jsonl",
    )
    parser.add_argument(
        "--out",
        default="",
        help="输出的客户侧用例集路径；默认 evals/<profile>.cases.jsonl",
    )
    parser.add_argument(
        "--report",
        default="",
        help="等价性报告路径；默认 evals/reports/<profile>_mapping_check.md",
    )
    parser.add_argument("--quiet", action="store_true", help="不打印逐条进度")
    return parser.parse_args(argv)


def pick_target_table(mapping, explicit: str) -> str:
    if explicit:
        return explicit
    for table in mapping.profile.tables:
        if table.role == "fact":
            return table.name
    if not mapping.tables:
        raise SystemExit("映射里没有任何表")
    return mapping.tables[0]


def build_checks(cases, mapping, target_table: str, source_table: str) -> list[CaseCheck]:
    rewriter = mapping.rewriter(target_table)
    checks: list[CaseCheck] = []

    for case in cases:
        rewritten = rewriter.rewrite(case.reference_sql)
        # 表名替换放在列名替换之后：列名里不含表名，顺序其实无所谓，
        # 但放在后面可以让 rewrites 账本更干净（只记列级改写）。
        target_sql = _replace_table(rewritten.sql, source_table, target_table)

        check = CaseCheck(
            case_id=case.case_id,
            question=case.question,
            category=case.category,
            seen_in_rag=case.seen_in_rag,
            source_sql=case.reference_sql,
            target_sql=target_sql,
            rewrites=rewritten.applied,
        )

        # 未映射字段检查：翻译后仍带着标准字段名的 SQL 一定跑不通或算错
        leftovers = mapping.mentions_unmapped(target_table, target_sql)
        if leftovers:
            check.error = f"翻译后仍引用未映射的标准字段：{leftovers}"

        try:
            _cols, source_rows = execute_sql(case.reference_sql)
            check.source_rows = len(source_rows)
        except Exception as exc:  # noqa: BLE001
            check.error = check.error or f"标准 SQL 执行失败：{exc}"
            checks.append(check)
            continue

        try:
            _cols, target_rows = execute_sql(target_sql)
            check.target_rows = len(target_rows)
        except Exception as exc:  # noqa: BLE001
            check.error = check.error or f"客户侧 SQL 执行失败：{exc}"
            checks.append(check)
            continue

        if not check.error:
            check.equivalent = rows_match(
                [list(row) for row in source_rows],
                [list(row) for row in target_rows],
            )
        checks.append(check)

    return checks


def _replace_table(sql: str, source: str, target: str) -> str:
    """替换表名（大小写不敏感，仅整词）。"""
    import re

    return re.sub(
        r"(?<![A-Za-z0-9_$])" + re.escape(source) + r"(?![A-Za-z0-9_$])",
        target,
        sql,
        flags=re.IGNORECASE,
    )


def write_cases(cases, checks: list[CaseCheck], out_path: Path) -> int:
    """写出客户侧用例集（问题不变，参照 SQL 换成客户列口径）。"""
    by_id = {check.case_id: check for check in checks}
    out_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    lines: list[str] = []
    for case in cases:
        check = by_id.get(case.case_id)
        if check is None:
            continue
        notes = case.notes
        if check.rewrites:
            notes = (notes + "；" if notes else "") + "映射改写：" + "，".join(
                f"{key}->{value}" for key, value in check.rewrites.items()
            )
        payload = {
            "case_id": case.case_id,
            "question": case.question,
            "reference_sql": check.target_sql,
            "category": case.category,
            "seen_in_rag": case.seen_in_rag,
            "notes": notes,
        }
        lines.append(json.dumps(payload, ensure_ascii=False))
        written += 1

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return written


def render_report(
    checks: list[CaseCheck], *, profile: str, target_table: str, source_table: str
) -> str:
    total = len(checks)
    ok = sum(1 for check in checks if check.ok)
    rewritten = [check for check in checks if check.rewrites]
    lines = [
        f"# 映射语义等价性检查：`{profile}`",
        "",
        f"- 标准表：`{source_table}` → 客户表：`{target_table}`",
        f"- 用例数：{total}",
        f"- **结果集等价：{ok}/{total}（{ok / total * 100:.1f}%）**" if total else "",
        f"- 触发列改写的用例：{len(rewritten)} 条",
        "",
        "判定方式：把标准参照 SQL 按映射翻译后执行，与标准表上的结果集比较"
        "（行数、列数、取值；行顺序无关，数值按 0.1% 相对容差）。",
        "全部等价即说明**映射语义完整** —— 评测里再出现的偏差都只能归因于模型，"
        "而不是映射本身。",
        "",
        "## 一、逐条结果",
        "",
        "| 用例 | 类别 | 结果 | 行数(标准/客户) | 改写 |",
        "|---|---|---|---|---|",
    ]
    for check in checks:
        mark = "等价" if check.ok else ("失败" if check.error else "**不等价**")
        rewrites = "，".join(f"{k}→{v}" for k, v in check.rewrites.items()) or "—"
        lines.append(
            f"| {check.case_id} | {check.category} | {mark} | "
            f"{check.source_rows}/{check.target_rows} | {rewrites} |"
        )

    problems = [check for check in checks if not check.ok]
    if problems:
        lines.extend(["", "## 二、问题明细", ""])
        for check in problems:
            lines.append(f"### {check.case_id} {check.question}")
            lines.append("")
            if check.error:
                lines.append(f"- 错误：{check.error}")
            lines.append(f"- 标准 SQL：`{check.source_sql}`")
            lines.append(f"- 客户 SQL：`{check.target_sql}`")
            lines.append("")

    lines.extend(
        [
            "",
            "## 三、口径说明",
            "",
            "- 本检查只验证**映射**（列名、单位换算、表名），不涉及大模型。",
            "- 复用评测的比较函数 `evals.runner.rows_match`，"
            "因此这里的「等价」与评测里的「结果一致」是同一个判定标准。",
            "- 单位换算字段（如 `def_rate * 100`）若写错，会在这里就被拦下，"
            "不会污染后续的模型评测数字。",
            "",
        ]
    )
    return "\n".join(line for line in lines if line is not None)


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = parse_args(argv)

    mapping = resolve_mapping(args.mapping, use_cache=False)
    if mapping is None:
        print(f"无法加载映射：{args.mapping}")
        return 1

    target_table = pick_target_table(mapping, args.target_table)
    cases_path = Path(args.cases) if args.cases else None
    cases = load_cases(cases_path)
    if not cases:
        print("没有加载到任何用例")
        return 1

    print("=" * 72)
    print("映射语义等价性检查")
    print("=" * 72)
    print(f"映射      ：{args.mapping}（profile={mapping.profile.profile}）")
    print(f"标准表    ：{args.source_table}")
    print(f"客户表    ：{target_table}")
    print(f"标准字段  ：{len(mapping.table(target_table))} 个已映射")
    converted = [item for item in mapping.table(target_table) if item.needs_conversion]
    print(
        "单位换算  ："
        + (
            "，".join(f"{item.standard_field}<->{item.expression}" for item in converted)
            or "无"
        )
    )
    print(f"用例      ：{len(cases)} 条")
    print("-" * 72)

    checks = build_checks(cases, mapping, target_table, args.source_table)

    ok = sum(1 for check in checks if check.ok)
    for check in checks:
        if args.quiet:
            continue
        mark = "OK  " if check.ok else "FAIL"
        rewrites = "，".join(f"{k}->{v}" for k, v in check.rewrites.items()) or "-"
        print(
            f"  [{mark}] {check.case_id} 行数 {check.source_rows}/{check.target_rows} "
            f"{check.question}  [{rewrites}]"
        )
        if check.error:
            print(f"          {check.error}")

    print("-" * 72)
    print(f"结果集等价：{ok}/{len(checks)}（{ok / len(checks) * 100:.1f}%）")

    profile_name = mapping.profile.profile or target_table
    out_path = (
        Path(args.out)
        if args.out
        else (_AGENT_ROOT / "evals" / f"{profile_name}.cases.jsonl")
    )
    written = write_cases(cases, checks, out_path)
    print(f"客户侧用例集：{out_path}（{written} 条）")

    report = render_report(
        checks,
        profile=profile_name,
        target_table=target_table,
        source_table=args.source_table,
    )
    report_path = (
        Path(args.report)
        if args.report
        else (_AGENT_ROOT / "evals" / "reports" / f"{profile_name}_mapping_check.md")
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"等价性报告  ：{report_path}")

    return 0 if ok == len(checks) else 1


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover
        pass


if __name__ == "__main__":
    sys.exit(main())
