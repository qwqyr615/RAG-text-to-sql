"""切出「换一张表」冒烟评测用的 12 条子集。

为什么不用 ``--limit 12``
-------------------------
``--limit`` 取的是用例文件前 N 条，而 ``cases.jsonl`` 是按类别聚集排列的 ——
前 12 条会全落在「指标聚合 + 分组对比」，完全覆盖不到筛选计数 / TopN /
多条件 / 统计极值 / 派生综合。冒烟的目的正是「用最小成本覆盖所有查询形状」，
所以这里**按类别分层抽样**，并刻意保证：

- 含 5 条**需要单位换算**的用例（c01/c07/c09/c15/c22，缺陷率与设备利用率各占若干），
  这是映射价值最容易观察到的位置；
- 每个类别至少 1 条，TopN 与多条件各 2 条。

用法::

    cd agent
    python scripts/build_smoke_subset.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from evals.dataset import load_cases  # noqa: E402

#: 分层抽样的 12 条：覆盖全部 7 个类别，含 5 条单位换算用例
SMOKE_CASE_IDS: tuple[str, ...] = (
    "c01",  # 指标聚合 + 单位换算（缺陷率 ×100）
    "c07",  # 指标聚合 + 单位换算（设备利用率 ×100）
    "c04",  # 指标聚合（无换算，作为对照）
    "c09",  # 分组对比 + 单位换算
    "c15",  # 分组对比 + 单位换算
    "c12",  # 分组对比（无换算）
    "c19",  # 筛选计数 + 单位换算（阈值 5 是百分数口径）
    "c22",  # 筛选计数 + 单位换算
    "c25",  # TopN
    "c30",  # TopN + 单位换算
    "c32",  # 多条件
    "c40",  # 派生综合（多指标同查 + 单位换算）
)


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover
        pass

    cases = load_cases()
    by_id = {case.case_id: case for case in cases}
    missing = [case_id for case_id in SMOKE_CASE_IDS if case_id not in by_id]
    if missing:
        print(f"用例集里找不到：{missing}")
        return 1

    selected = [by_id[case_id] for case_id in SMOKE_CASE_IDS]
    out_path = _AGENT_ROOT / "evals" / "smoke.cases.jsonl"
    lines = []
    for case in selected:
        lines.append(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "question": case.question,
                    "reference_sql": case.reference_sql,
                    "category": case.category,
                    "seen_in_rag": case.seen_in_rag,
                    "notes": case.notes,
                },
                ensure_ascii=False,
            )
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写出 {len(selected)} 条：{out_path}")

    categories: dict[str, int] = {}
    for case in selected:
        categories[case.category] = categories.get(case.category, 0) + 1
    print(f"类别覆盖：{categories}")
    converted = [
        case.case_id
        for case in selected
        if "defect_rate" in case.reference_sql or "machine_utilization" in case.reference_sql
    ]
    print(f"含单位换算字段的用例：{converted}（{len(converted)} 条）")
    print(f"RAG 示例库中有同型示例：{sum(1 for c in selected if c.seen_in_rag)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
