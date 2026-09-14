"""消融实验运行器。

两条实验轴
----------

**轴 1：RAG 段 × 指标段**（原有）

===============  ========  ==========
配置               RAG 段    指标段
===============  ========  ==========
rag_off+metrics_on   关        开        ← 基线
rag_on+metrics_on    开        开
rag_off+metrics_off  关        关
rag_on+metrics_off   开        关
===============  ========  ==========

**轴 2：字段映射**（新增，「换一张表」对照）

``+mapping_on`` / ``+mapping_off`` 控制是否加载 ``mapping.yaml``：

- ``mapping_off``：只用数据库里能读到的列名与 ``COMMENT``（本次客户表 45 列
  **注释全空**，所以模型只能靠缩写列名硬猜）；
- ``mapping_on``：注入人审过的「标准字段 ↔ 客户列」口径，含单位换算指令，
  并把 RAG 示例 SQL 改写成客户列口径。

两个配置跑同一张客户表、同一套用例，差值就是**映射这一层的净增益**。

判定标准与常见 text-to-SQL 评测一致——**执行结果一致率（execution accuracy）**：
把模型生成的 SQL 与人工参照 SQL 都真正执行一遍，比较结果集。
"""

from __future__ import annotations

import logging
import os
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

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_CONFIG_NAMES",
    "CaseOutcome",
    "ConfigOutcome",
    "EvalConfig",
    "build_agent_metadata",
    "build_prompt_builder",
    "collect_reference_results",
    "default_agent_factory",
    "rows_match",
    "rows_match_projection",
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

#: 环境变量名，由 ``eval_spec`` 在装配 Agent 前设置，使 ``Text2SQLAgent``
#: 内部调用 ``get_metadata_json()`` 时读到同一份映射。
MAPPING_ENV = "ANALYSIS_MAPPING"


class AnsweringAgent(Protocol):
    """评测只依赖这一个方法，便于注入假 Agent 做单测。"""

    def ask(self, question: Any) -> Any: ...


# ----------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class EvalConfig:
    """一个消融配置。

    ``mapping`` 为三态：

    - ``True``：显式启用字段映射（名字里带 ``+mapping_on``）；
    - ``False``：显式关闭（名字里带 ``+mapping_off``）；
    - ``None``：不参与映射维度，沿用环境里 ``ANALYSIS_MAPPING`` 的配置。

    三态是必要的：原本的四配置矩阵不应该因为新增了映射维度就改名，
    否则既有报告与历史数字无法对照。
    """

    name: str
    rag: bool
    metrics: bool
    mapping: bool | None = None

    @classmethod
    def parse(cls, text: str) -> "EvalConfig":
        """从 ``rag_off+metrics_on+mapping_on`` 这类写法解析配置。"""
        normalized = text.strip().lower().replace(" ", "")
        rag = True
        metrics = True
        mapping: bool | None = None
        for token in normalized.split("+"):
            if token in {"rag_on", "rag", "rag=true"}:
                rag = True
            elif token in {"rag_off", "no_rag", "rag=false"}:
                rag = False
            elif token in {"metrics_on", "metrics", "metrics=true"}:
                metrics = True
            elif token in {"metrics_off", "no_metrics", "metrics=false"}:
                metrics = False
            elif token in {"mapping_on", "mapping", "mapping=true"}:
                mapping = True
            elif token in {"mapping_off", "no_mapping", "mapping=false"}:
                mapping = False
            else:
                raise ValueError(f"无法识别的配置项：{token}")

        name = f"rag_{'on' if rag else 'off'}+metrics_{'on' if metrics else 'off'}"
        if mapping is not None:
            name += f"+mapping_{'on' if mapping else 'off'}"
        return cls(name=name, rag=rag, metrics=metrics, mapping=mapping)


def build_agent_metadata(config: EvalConfig) -> dict[str, Any]:
    """按配置装配元数据 JSON（发现 + 映射）。

    ``mapping`` 为 ``None`` 时沿用环境配置；否则显式传/不传映射文件，
    保证配置名与实际生效的映射一致 —— 否则报告里的 ``mapping_on`` 可能名不副实。
    """
    from metadata.mapping import default_mapping_path, resolve_mapping
    from metadata.metadata_service import get_metadata_json

    # 三态到「映射文件路径」的映射
    if config.mapping is None:
        explicit_path: str | os.PathLike[str] | None = default_mapping_path()
    elif config.mapping:
        explicit_path = _default_profile_path()
    else:
        explicit_path = None

    mapping = resolve_mapping(explicit_path, use_cache=False) if explicit_path else None
    return get_metadata_json(mapping=mapping)


def _default_profile_path() -> str | None:
    """``mapping_on`` 在没显式指定时用哪个映射文件。

    优先 ``ANALYSIS_MAPPING``；没配就用仓库里的第一个 ``mappings/*.mapping.yaml``。
    单表数据集下这是安全的（只有一份映射可选）。
    """
    configured = os.environ.get(MAPPING_ENV) or getattr(settings, "analysis_mapping", "")
    if str(configured).strip() and str(configured).strip().lower() not in {
        "",
        "none",
        "off",
        "false",
        "0",
    }:
        return str(configured)

    from core.config import BASE_DIR

    candidates = sorted((BASE_DIR / "mappings").glob("*.mapping.yaml"))
    return str(candidates[0]) if candidates else None


def build_prompt_builder(config: EvalConfig) -> SQLAgentPromptBuilder:
    """按配置装配四段 Provider（关掉哪一段就传 enabled=False）。"""
    return SQLAgentPromptBuilder(
        [
            DataResourceProvider(
                settings.prompt_metadata_budget,
                min_tables=settings.prompt_metadata_min_tables,
                max_columns_per_table=settings.sql_max_columns_per_table,
                sample_value_tables=settings.prompt_metadata_sample_tables,
                field_map_budget=settings.prompt_field_map_budget,
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

    元数据**由本工厂显式注入**，而不是让 Agent 自己去读 —— 这样 ``mapping_on``
    与 ``mapping_off`` 两个配置的差异严格等于映射本身，不会混进缓存或环境变量
    的时序问题。
    """
    from agents.text2sql_agent import Text2SQLAgent
    from sessions import InMemorySessionStore

    return Text2SQLAgent(
        metadata_json=build_agent_metadata(config),
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
    """比较结果集是否一致（严格口径，主指标）。

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


def rows_match_projection(
    expected: Sequence[Sequence[Any]],
    actual: Sequence[Sequence[Any]],
    *,
    tolerance: float = TOLERANCE,
    max_extra_columns: int = 6,
) -> bool:
    """列容错口径：要求参照的每一列都能在生成结果里找到对应列。

    **为什么需要第二套口径**
    ------------------------
    ``rows_match`` 要求列数完全相同，这对「开放式 text-to-SQL 基准」是合理的
    （答案是唯一确定的结果集），但对**对话式问析系统**过严：模型给出分析时常常
    顺带返回几个有解释力的附加列，例如

    - 问「良率最高的 5 条记录」-> 返回这 5 条记录的 ``rec_no, mach_no, line_cd,
      lot_no, sft, prod_tp, fpy``（而参照只要 ``rec_no, fpy``）；
    - 问「各班组平均质量得分」-> 多带一个 ``COUNT(*)`` 说明样本量。

    这些答案在业务上**更完整**，却被判为「结果不符」。实测（见
    ``docs/EVALUATION.md``）这会让 40 条里的近两成变成假阴性 —— 很容易被误读成
    「换成客户表以后模型不行了」。

    规则：

    1. 行数必须完全相同（仍然守住 ``LIMIT`` / ``WHERE`` 这类真实错误）；
    2. 参照的每一列都必须能在生成结果里找到一列与之**逐行取值一致**；
    3. 生成结果最多比参照多 ``max_extra_columns`` 列 —— 多了就不是「顺带补充」，
       而是答非所问，退回严格判定；
    4. 列匹配用**稳定匹配**（按行取值排序后比较），因此不依赖列顺序。

    注意这是**诊断口径**，不替换主指标：报告同时给出两者，读者可以自己判断。
    严格口径的分数仍然保留，避免「把口径改松来刷分」。
    """
    if len(expected) != len(actual):
        return False
    if not expected:
        return True

    expected_rows = [tuple(row) for row in expected]
    actual_rows = [tuple(row) for row in actual]
    expected_width = len(expected_rows[0])
    actual_width = len(actual_rows[0])

    if actual_width < expected_width:
        return False
    if actual_width - expected_width > max_extra_columns:
        return False
    if actual_width == expected_width:
        return rows_match(expected, actual, tolerance=tolerance)

    # 参照列的规范化列向量，用于与生成结果的每一列做匹配
    expected_columns = [
        _column_vector(expected_rows, index) for index in range(expected_width)
    ]
    actual_columns = [
        _column_vector(actual_rows, index) for index in range(actual_width)
    ]

    matched_actual: set[int] = set()
    for vector in expected_columns:
        for position, candidate in enumerate(actual_columns):
            if position in matched_actual:
                continue
            if _vectors_equal(vector, candidate, tolerance):
                matched_actual.add(position)
                break
        else:
            # 参照的某一列在生成结果里找不到对应 —— 缺列，判错
            return False
    return True


def _column_vector(
    rows: Sequence[tuple[Any, ...]], index: int
) -> list[Any]:
    """取第 ``index`` 列的全部取值，按规范化后的值排序。

    排序保证与行顺序无关；用取值而不是列名比较，因为两边别名可能不同
    （``total_downtime_minutes`` vs ``total_stop_min`` 指的是同一个量）。
    """
    return sorted(
        (row[index] for row in rows if index < len(row)), key=_canonical
    )


def _vectors_equal(
    left: Sequence[Any], right: Sequence[Any], tolerance: float
) -> bool:
    if len(left) != len(right):
        return False
    return all(
        _values_equal(a, b, tolerance) for a, b in zip(left, right)
    )


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
    #: 列容错口径下是否一致（诊断用，见 ``rows_match_projection``）。
    #: 严格口径 ``result_match`` 仍是主指标；两者都记录，报告同时展示，
    #: 这样「模型多带了几列上下文」与「模型真的算错」可以分开看。
    result_match_projection: bool = False
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
    rows = list(result.rows or [])
    outcome.result_match = outcome.sql_ok and rows_match(expected_rows, rows)
    outcome.result_match_projection = outcome.sql_ok and rows_match_projection(
        expected_rows, rows
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
        """结果与参照一致的比例（核心指标，严格口径）。"""
        return self._rate(self.subset(seen=seen), "result_match")

    def accuracy_projection(self, *, seen: bool | None = None) -> float:
        """列容错口径下的一致率（诊断指标）。

        与 :meth:`accuracy` 的差值 = 「答案其实对，只是多带了几列上下文」的比例。
        这个差值大，说明严格口径在惩罚模型的多余善意，而不是模型算错。
        """
        return self._rate(self.subset(seen=seen), "result_match_projection")

    def extra_column_only(self) -> list[str]:
        """严格口径判错、但列容错口径判对的用例 ID（即「只是多带了列」）。"""
        return [
            outcome.case_id
            for outcome in self.outcomes
            if outcome.result_match_projection and not outcome.result_match
        ]

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
