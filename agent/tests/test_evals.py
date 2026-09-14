"""评测模块测试：数据集校验、结果比较、报告渲染、矩阵运行。

用假 Agent 与预置参照结果，因此**不需要大模型、Milvus 与数据库**。
"""

from __future__ import annotations

from typing import Any

import pytest

from core.config import BASE_DIR
from evals.dataset import load_cases, summarize, validate_cases
from evals.report import render_markdown, save_report
from evals.runner import (
    EvalConfig,
    build_prompt_builder,
    rows_match,
    run_matrix,
)
from schemas.agent_io import AgentResult
from tools.sql_guard import extract_table_refs

SCRATCH_DIR = BASE_DIR / "sessions"

EXPECTED_CASE_COUNT = 40


# ----------------------------------------------------------------------
# 数据集
# ----------------------------------------------------------------------
def test_dataset_loads_and_validates() -> None:
    cases = load_cases()

    assert len(cases) == EXPECTED_CASE_COUNT
    assert validate_cases(cases) == []

    info = summarize(cases)
    # 两组都要够大，否则「相似问题 / 新问题」的对比没有统计意义
    assert info["seen_in_rag"] >= 10
    assert info["new_questions"] >= 10
    assert info["total"] == len(cases)


def test_reference_sql_only_touches_service_table() -> None:
    for case in load_cases():
        assert extract_table_refs(case.reference_sql) <= {"fact_production_record"}, (
            case.case_id
        )


# ----------------------------------------------------------------------
# 结果比较
# ----------------------------------------------------------------------
def test_rows_match_is_order_insensitive() -> None:
    assert rows_match([[1, "a"], [2, "b"]], [[2, "b"], [1, "a"]])


def test_rows_match_tolerates_float_noise() -> None:
    assert rows_match([[2.1246667]], [[2.1247]])


def test_rows_match_rejects_different_row_count() -> None:
    assert not rows_match([[1, 2]], [[1, 2], [3, 4]])


def test_rows_match_rejects_different_column_count() -> None:
    assert not rows_match([[1, 2]], [[1]])


def test_rows_match_rejects_different_values() -> None:
    assert not rows_match([[1.0]], [[1.5]])
    assert not rows_match([["Line_A"]], [["Line_B"]])


def test_rows_match_handles_empty_results() -> None:
    assert rows_match([], [])


# ----------------------------------------------------------------------
# 配置与 Prompt 段开关
# ----------------------------------------------------------------------
def test_eval_config_parse() -> None:
    config = EvalConfig.parse("rag_off + metrics_on")
    assert config == EvalConfig(name="rag_off+metrics_on", rag=False, metrics=True)

    assert EvalConfig.parse("rag_on+metrics_off").rag is True
    assert EvalConfig.parse("no_rag").rag is False


def test_eval_config_parse_rejects_unknown_token() -> None:
    with pytest.raises(ValueError):
        EvalConfig.parse("rag_off+whatever")


def test_build_prompt_builder_toggles_sections() -> None:
    builder = build_prompt_builder(
        EvalConfig(name="rag_off+metrics_off", rag=False, metrics=False)
    )
    flags = {provider.name: provider.enabled for provider in builder.providers}

    assert flags == {
        "metadata": True,
        "metrics": False,
        "knowledge": True,
        "rag": False,
    }


# ----------------------------------------------------------------------
# 矩阵运行（假 Agent）
# ----------------------------------------------------------------------
REFERENCE_ROWS = [[1, 2.0]]


class _FakeAgent:
    """按问题返回固定结果行的假 Agent。"""

    def __init__(self, rows_by_question: dict[str, list[list[Any]]]) -> None:
        self._rows = rows_by_question
        self.questions: list[str] = []

    def ask(self, question: Any) -> AgentResult:
        self.questions.append(question.question)
        return AgentResult(
            question=question.question,
            sql="SELECT 1",
            analysis_text="ok",
            rows=self._rows.get(question.question, []),
            prompt_usage={"used_chars": 123},
        )


def _cases_for_split():
    cases = load_cases()
    seen = next(case for case in cases if case.seen_in_rag)
    new = next(case for case in cases if not case.seen_in_rag)
    return [seen, new], seen.case_id, new.case_id


def test_run_matrix_all_correct() -> None:
    cases, _, _ = _cases_for_split()
    references = {case.case_id: REFERENCE_ROWS for case in cases}
    factory = lambda config: _FakeAgent(  # noqa: E731
        {case.question: REFERENCE_ROWS for case in cases}
    )
    configs = [
        EvalConfig.parse("rag_off+metrics_on"),
        EvalConfig.parse("rag_on+metrics_on"),
    ]

    outcomes = run_matrix(
        cases, configs, agent_factory=factory, reference_results=references
    )

    assert [outcome.accuracy() for outcome in outcomes] == [1.0, 1.0]
    assert [outcome.execution_rate() for outcome in outcomes] == [1.0, 1.0]
    assert outcomes[0].avg_prompt_chars() == 123.0


def test_run_matrix_all_wrong() -> None:
    cases, _, _ = _cases_for_split()
    references = {case.case_id: REFERENCE_ROWS for case in cases}
    factory = lambda config: _FakeAgent(  # noqa: E731
        {case.question: [[9, 9.0]] for case in cases}
    )

    outcomes = run_matrix(
        cases,
        [EvalConfig.parse("rag_off+metrics_on")],
        agent_factory=factory,
        reference_results=references,
    )

    assert outcomes[0].accuracy() == 0.0
    assert outcomes[0].execution_rate() == 1.0  # SQL 能跑，只是结果不符


def test_run_matrix_splits_seen_and_new_questions() -> None:
    cases, seen_id, _new_id = _cases_for_split()
    references = {case.case_id: REFERENCE_ROWS for case in cases}
    only_seen_correct = {
        case.question: (REFERENCE_ROWS if case.case_id == seen_id else [[9]])
        for case in cases
    }
    factory = lambda config: _FakeAgent(only_seen_correct)  # noqa: E731

    outcome = run_matrix(
        cases,
        [EvalConfig.parse("rag_on+metrics_on")],
        agent_factory=factory,
        reference_results=references,
    )[0]

    assert outcome.accuracy(seen=True) == 1.0
    assert outcome.accuracy(seen=False) == 0.0


# ----------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------
def test_render_markdown_contains_comparison_table() -> None:
    cases, _, _ = _cases_for_split()
    references = {case.case_id: REFERENCE_ROWS for case in cases}
    factory = lambda config: _FakeAgent(  # noqa: E731
        {case.question: REFERENCE_ROWS for case in cases}
    )
    outcomes = run_matrix(
        cases,
        [
            EvalConfig.parse("rag_off+metrics_on"),
            EvalConfig.parse("rag_on+metrics_off"),
        ],
        agent_factory=factory,
        reference_results=references,
    )

    text = render_markdown(outcomes, cases=cases)

    assert "结果一致率" in text
    assert "相似问题一致率" in text
    assert "新问题一致率" in text
    assert "rag_off+metrics_on" in text
    assert "rag_on+metrics_off" in text
    assert "以 `rag_off+metrics_on` 为基线的增量" in text
    assert "没有时间列" in text


def test_save_report_writes_file() -> None:
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    path = save_report("# 评测报告", directory=SCRATCH_DIR)
    try:
        assert path.is_file()
        assert path.read_text(encoding="utf-8") == "# 评测报告"
    finally:
        path.unlink(missing_ok=True)
