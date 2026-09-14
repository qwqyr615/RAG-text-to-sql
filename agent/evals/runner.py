"""消融实验运行器：RAG 开/关 × 指标段 开/关。

判定标准与常见 text-to-SQL 评测一致——**执行结果一致率（execution accuracy）**：
把模型生成的 SQL 与人工参照 SQL 都真正执行一遍，比较结果集。

四个配置：

===============  ========  ==========
配置               RAG 段    指标段
===============  ========  ==========
rag_off+metrics_on   关        开        ← 基线
rag_on+metrics_on    开        开
rag_off+metrics_off  关        关
rag_on+metrics_off   开        关
===============  ========  ==========
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence

from core.config import settings
from evals.dataset import EvalCase
from prompt.builder import SQLAgentPromptBuilder
from prompt.providers import (
    DataResourceProvider,
    KnowledgeProvider,
    MetricsProvider,
    RagExampleProvider,
)
from schemas.agent_io import AgentQuestion
from tools.sql_executor import execute_sql

__all__ = [
    "DEFAULT_CONFIG_NAMES",
    "CaseOutcome",
    "ConfigOutcome",
    "EvalConfig",
    "build_prompt_builder",
    "collect_reference_results",
    "default_agent_factory",
    "rows_match",
    "run_case",
    "run_matrix",
]

DEFAULT_CONFIG_NAMES: tuple[str, ...] = (
    "rag_off+metrics_on",
    "rag_on+metrics_on",
    "rag_off+metrics_off",
    "rag_on+metrics_off",
)

#: 数值比较容差（相对值）。聚合函数的浮点尾差不应被算作错误。
TOLERANCE = 1e-3


class AnsweringAgent(Protocol):
    """评测只依赖这一个方法，便于注入假 Agent 做单测。"""

    def ask(self, question: Any) -> Any: ...


# ----------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class EvalConfig:
    """一个消融配置。"""

    name: str
    rag: bool
    metrics: bool

    @classmethod
    def parse(cls, text: str) -> "EvalConfig":
        """从 ``rag_off+metrics_on`` 这类写法解析配置。"""
        normalized = text.strip().lower().replace(" ", "")
        rag = True
        metrics = True
        for token in normalized.split("+"):
            if token in {"rag_on", "rag", "rag=true"}:
                rag = True
            elif token in {"rag_off", "no_rag", "rag=false"}:
                rag = False
            elif token in {"metrics_on", "metrics", "metrics=true"}:
                metrics = True
            elif token in {"metrics_off", "no_metrics", "metrics=false"}:
                metrics = False
            else:
                raise ValueError(f"无法识别的配置项：{token}")
        return cls(
            name=f"rag_{'on' if rag else 'off'}+metrics_{'on' if metrics else 'off'}",
            rag=rag,
            metrics=metrics,
        )


def build_prompt_builder(config: EvalConfig) -> SQLAgentPromptBuilder:
    """按配置装配四段 Provider（关掉哪一段就传 enabled=False）。"""
    return SQLAgentPromptBuilder(
        [
            DataResourceProvider(
                settings.prompt_metadata_budget,
                min_tables=settings.prompt_metadata_min_tables,
                max_columns_per_table=settings.sql_max_columns_per_table,
                sample_value_tables=settings.prompt_metadata_sample_tables,
            ),
            MetricsProvider(settings.prompt_metrics_budget, enabled=config.metrics),
            KnowledgeProvider(settings.prompt_knowledge_budget),
            RagExampleProvider(
                settings.prompt_rag_budget,
                top_k=settings.rag_top_k,
                min_score=settings.rag_min_score,
                enabled=config.rag,
            ),
        ],
        total_budget=settings.prompt_total_budget,
    )


def default_agent_factory(config: EvalConfig) -> Any:
    """默认 Agent 工厂：真实 DeepSeek + 真实库，逐例独立会话。

    ``verbose=False``：评测要的是干净的进度输出，不需要 LangChain 的中间步骤。
    """
    from agents.text2sql_agent import Text2SQLAgent
    from sessions import InMemorySessionStore

    return Text2SQLAgent(
        prompt_builder=build_prompt_builder(config),
        session_store=InMemorySessionStore(max_turns=0),
        verbose=False,
    )


# ----------------------------------------------------------------------
# 结果比较
# ----------------------------------------------------------------------
def _normalize_value(value: Any) -> Any:
    """把值归一成可比较形式：数字统一成 float，其余转字符串。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        return text


def _canonical(value: Any) -> str:
    """排序用的规范化字符串（保证两侧排序顺序一致）。"""
    normalized = _normalize_value(value)
    if isinstance(normalized, float):
        return f"{normalized:.6f}"
    return "" if normalized is None else str(normalized)


def _sort_key(row: Sequence[Any]) -> tuple[str, ...]:
    return tuple(_canonical(value) for value in row)


def _values_equal(left: Any, right: Any, tolerance: float) -> bool:
    left_value = _normalize_value(left)
    right_value = _normalize_value(right)
    if isinstance(left_value, float) and isinstance(right_value, float):
        return abs(left_value - right_value) <= max(
            tolerance, abs(left_value) * tolerance
        )
    return left_value == right_value


def rows_match(
    expected: Sequence[Sequence[Any]],
    actual: Sequence[Sequence[Any]],
    *,
    tolerance: float = TOLERANCE,
) -> bool:
    """比较少行结果集是否一致。

    规则（与常见 text-to-SQL 评测口径一致）：

    - **行数必须相同**：多返回或少返回都算错（能抓住漏写 LIMIT 之类的问题）；
    - **列数必须相同**：多选/少选列都算错；
    - **行顺序无关**：两侧按规范化后的值排序再逐行比较；
    - **数值按相对容差比较**：``AVG`` 之类的浮点尾差不算错。
    """
    if len(expected) != len(actual):
        return False
    if not expected:
        return True

    expected_sorted = sorted(
        (tuple(row) for row in expected), key=_sort_key
    )
    actual_sorted = sorted((tuple(row) for row in actual), key=_sort_key)

    for expected_row, actual_row in zip(expected_sorted, actual_sorted):
        if len(expected_row) != len(actual_row):
            return False
        if not all(
            _values_equal(left, right, tolerance)
            for left, right in zip(expected_row, actual_row)
        ):
            return False
    return True


# ----------------------------------------------------------------------
# 参照结果与逐例执行
# ----------------------------------------------------------------------
def collect_reference_results(
    cases: Sequence[EvalCase],
    *,
    executor: Callable[[str], tuple[list[str], list[list[Any]]]] = execute_sql,
) -> tuple[dict[str, list[list[Any]]], list[str]]:
    """执行所有参照 SQL，返回 ``(case_id -> 结果行, 问题列表)``。"""
    results: dict[str, list[list[Any]]] = {}
    problems: list[str] = []

    for case in cases:
        try:
            _columns, rows = executor(case.reference_sql)
        except Exception as exc:  # noqa: BLE001 - 参照 SQL 本身有问题要报出来
            problems.append(f"{case.case_id}: 参照 SQL 执行失败（{exc}）")
            continue
        results[case.case_id] = [list(row) for row in rows]
    return results, problems


@dataclass
class CaseOutcome:
    """一条用例在某个配置下的结果。"""

    case_id: str
    question: str
    category: str
    seen_in_rag: bool
    generated_sql: str = ""
    sql_ok: bool = False
    result_match: bool = False
    error: str | None = None
    latency_ms: float = 0.0
    prompt_chars: int = 0
    row_count: int = 0


def run_case(
    agent: AnsweringAgent,
    case: EvalCase,
    expected_rows: Sequence[Sequence[Any]],
    *,
    session_id: str | None = None,
) -> CaseOutcome:
    """跑一条用例并判定结果是否与参照一致。"""
    outcome = CaseOutcome(
        case_id=case.case_id,
        question=case.question,
        category=case.category,
        seen_in_rag=case.seen_in_rag,
    )

    started = time.perf_counter()
    try:
        result = agent.ask(
            AgentQuestion(
                question=case.question,
                session_id=session_id or f"eval-{case.case_id}",
            )
        )
    except Exception as exc:  # noqa: BLE001 - Agent 异常算用例失败，不应中断整轮评测
        outcome.latency_ms = (time.perf_counter() - started) * 1000
        outcome.error = str(exc)
        return outcome
    outcome.latency_ms = (time.perf_counter() - started) * 1000

    outcome.generated_sql = result.sql or ""
    outcome.row_count = len(result.rows or [])
    usage = result.prompt_usage or {}
    outcome.prompt_chars = int(usage.get("used_chars") or 0)

    if not result.success:
        outcome.error = result.error or "Agent 执行失败"
    elif result.sql_error:
        outcome.error = result.sql_error

    outcome.sql_ok = bool(result.sql) and not result.sql_error
    outcome.result_match = outcome.sql_ok and rows_match(
        expected_rows, list(result.rows or [])
    )
    return outcome


# ----------------------------------------------------------------------
# 配置结果与矩阵
# ----------------------------------------------------------------------
@dataclass
class ConfigOutcome:
    """一个配置下的全部用例结果。"""

    config: EvalConfig
    outcomes: list[CaseOutcome] = field(default_factory=list)

    @property
    def by_case_id(self) -> dict[str, CaseOutcome]:
        return {outcome.case_id: outcome for outcome in self.outcomes}

    def subset(self, *, seen: bool | None = None) -> list[CaseOutcome]:
        if seen is None:
            return list(self.outcomes)
        return [outcome for outcome in self.outcomes if outcome.seen_in_rag is seen]

    @staticmethod
    def _rate(items: Sequence[CaseOutcome], attribute: str) -> float:
        if not items:
            return 0.0
        hits = sum(1 for item in items if getattr(item, attribute))
        return hits / len(items)

    def execution_rate(self, *, seen: bool | None = None) -> float:
        """生成 SQL 能跑通的比例。"""
        return self._rate(self.subset(seen=seen), "sql_ok")

    def accuracy(self, *, seen: bool | None = None) -> float:
        """结果与参照一致的比例（核心指标）。"""
        return self._rate(self.subset(seen=seen), "result_match")

    def avg_latency_ms(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(outcome.latency_ms for outcome in self.outcomes) / len(
            self.outcomes
        )

    def avg_prompt_chars(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(outcome.prompt_chars for outcome in self.outcomes) / len(
            self.outcomes
        )


def run_matrix(
    cases: Sequence[EvalCase],
    configs: Sequence[EvalConfig],
    *,
    agent_factory: Callable[[EvalConfig], AnsweringAgent] | None = None,
    reference_results: dict[str, list[list[Any]]] | None = None,
    progress: Callable[[EvalConfig, CaseOutcome], None] | None = None,
) -> list[ConfigOutcome]:
    """按配置矩阵逐条评测。

    参数:
        agent_factory: 注入 Agent 的工厂（单测用假 Agent，正式跑用 DeepSeek）
        reference_results: 预先算好的参照结果；不传则现场执行参照 SQL
    """
    results = reference_results
    if results is None:
        results, problems = collect_reference_results(cases)
        if problems:
            raise RuntimeError("参照 SQL 执行失败：\n" + "\n".join(problems))

    factory = agent_factory or default_agent_factory
    config_outcomes: list[ConfigOutcome] = []

    for config in configs:
        agent = factory(config)
        config_outcome = ConfigOutcome(config=config)
        for case in cases:
            outcome = run_case(agent, case, results.get(case.case_id, []))
            config_outcome.outcomes.append(outcome)
            if progress is not None:
                progress(config, outcome)
        config_outcomes.append(config_outcome)

    return config_outcomes
