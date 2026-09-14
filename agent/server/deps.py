"""适配层核心：内核单例 + 结果转换 + 异步任务编排。

这一层解决三个内核与 HTTP 之间的实际落差：

1. **序列化落差**：``EnterpriseAgent.handle()`` 返回 ``{"type", "result"}``，
   其中 ``result`` 在 ``type="agent"`` 时是 Pydantic 的 ``AgentResult``
   （直接 ``json.dumps`` 会抛 ``TypeError``），在 ``type="model"`` 时是 dict，
   在 ``type="error"`` 时是 ``str``。:func:`to_ask_response` 负责统一。
2. **耗时落差**：一次提问最长可能阻塞 ``SQL_AGENT_MAX_EXECUTION_TIME``（默认 90s）。
   直接同步返回会让 React/Java 网关超时，因此提供**后台任务 + 轮询 + SSE**。
3. **生命周期落差**：内核初始化要连数据库、读元数据，属于重操作。
   ``AgentService`` 用「懒加载 + 线程锁」保证只初始化一次，且初始化失败时
   接口返回可读错误而不是 500 堆栈。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable, Optional

from server.schemas import (
    AnalysisStep,
    AskRequest,
    AskResponse,
    ChartConfig,
    JobStateResponse,
    to_ask_response,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 内核单例
# ---------------------------------------------------------------------------
class AgentService:
    """持有 Agent 内核与建模能力的单例服务。"""

    def __init__(self) -> None:
        self._agent: Any = None
        self._lock = threading.Lock()
        self._init_error: Optional[str] = None
        self._chart_llm_bound = False

    # -- 初始化 --------------------------------------------------------
    def _ensure_agent(self) -> Any:
        """双重检查锁定，保证内核只初始化一次。"""
        if self._agent is not None:
            return self._agent
        with self._lock:
            if self._agent is not None:
                return self._agent
            if self._init_error is not None:
                raise RuntimeError(self._init_error)
            try:
                from agents.enterprise_agent import EnterpriseAgent

                logger.info("正在初始化 Agent 内核（读取元数据 / 连接数据库）...")
                self._agent = EnterpriseAgent()
                logger.info(
                    "Agent 内核就绪：%s 张业务表",
                    len(self._agent.get_metadata().get("tables", [])),
                )
            except Exception as exc:  # noqa: BLE001 - 转成可读错误
                self._init_error = f"Agent 内核初始化失败：{exc}"
                logger.exception(self._init_error)
                raise RuntimeError(self._init_error) from exc
            return self._agent

    @property
    def ready(self) -> bool:
        return self._agent is not None

    @property
    def init_error(self) -> Optional[str]:
        return self._init_error

    def warmup(self) -> None:
        """预热内核；失败只记录日志，不阻断服务启动（/health 会体现状态）。"""
        try:
            self._ensure_agent()
        except Exception as exc:  # noqa: BLE001
            logger.warning("预热失败，服务以 degraded 状态启动：%s", exc)

    # -- 数据资源 / 业务知识 -------------------------------------------
    def metadata(self) -> dict[str, Any]:
        return self._ensure_agent().get_metadata()

    def knowledge(self, include_graph: bool = False) -> dict[str, Any]:
        data = dict(self._ensure_agent().get_knowledge())
        if include_graph:
            data["graph"] = build_knowledge_graph(data, self.metadata())
        return data

    # -- 问答 ----------------------------------------------------------
    def ask(self, request: AskRequest) -> AskResponse:
        """执行一次问答（同步阻塞，可能长达 90s）。"""
        agent = self._ensure_agent()
        response = agent.handle(request.question, session_id=request.session_id)
        return to_ask_response(
            response,
            question=request.question,
            session_id=request.session_id,
            want_chart=request.want_chart,
            want_report=request.want_report,
            chart_llm=self._chart_llm() if request.want_chart else None,
        )

    def _chart_llm(self) -> Any:
        from core.llm import get_chat_llm

        return get_chat_llm()

    # -- 会话 ----------------------------------------------------------
    def reset_session(self, session_id: str) -> None:
        self._ensure_agent().reset_session(session_id)

    # -- 健康 ----------------------------------------------------------
    def health(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "status": "ok" if self._agent is not None else "degraded",
            "agent_ready": self._agent is not None,
            "database_type": "",
            "table_count": 0,
            "llm_model": "",
            "rag_enabled": False,
            "error": self._init_error,
        }
        try:
            from core.config import settings

            info["llm_model"] = settings.llm_model
            info["rag_enabled"] = settings.rag_enabled
        except Exception:  # noqa: BLE001
            pass

        if self._agent is not None:
            metadata = self._agent.get_metadata()
            info["database_type"] = metadata.get("database_type", "")
            info["table_count"] = len(metadata.get("tables", []))
        return info


# ---------------------------------------------------------------------------
# 异步任务存储
# ---------------------------------------------------------------------------
class JobStore:
    """进程内任务表（演示规模足够；生产可换 Redis）。

    ``condition`` 用于 SSE：任务状态变化时唤醒所有等待的订阅者，
    避免前端空转轮询。
    """

    def __init__(self, max_jobs: int = 200) -> None:
        self._jobs: dict[str, JobStateResponse] = {}
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._max_jobs = max_jobs

    def create(self) -> JobStateResponse:
        job = JobStateResponse(
            job_id=uuid.uuid4().hex,
            status="pending",
            created_at=time.time(),
            stage="排队中",
        )
        with self._condition:
            self._jobs[job.job_id] = job
            self._evict_locked()
            self._condition.notify_all()
        return job

    def get(self, job_id: str) -> Optional[JobStateResponse]:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **changes: Any) -> Optional[JobStateResponse]:
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for key, value in changes.items():
                setattr(job, key, value)
            self._condition.notify_all()
            return job

    def wait_for_change(self, job_id: str, timeout: float = 15.0) -> bool:
        """等待任务状态变化（SSE 用）。返回 False 表示超时。"""
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.status in ("succeeded", "failed"):
                return False
            self._condition.wait(timeout=timeout)
            return True

    def _evict_locked(self) -> None:
        """超出上限时淘汰最旧的已完成任务。"""
        if len(self._jobs) <= self._max_jobs:
            return
        finished = [
            job
            for job in self._jobs.values()
            if job.status in ("succeeded", "failed")
        ]
        finished.sort(key=lambda item: item.created_at)
        for job in finished[: max(1, len(self._jobs) - self._max_jobs)]:
            self._jobs.pop(job.job_id, None)


# ---------------------------------------------------------------------------
# 结果转换
# ---------------------------------------------------------------------------
def build_analysis_steps(result: AskResponse) -> list[AnalysisStep]:
    """把一次问答拆成可展示的「分析过程」步骤。

    题目要求「展示分析过程」，前端需要看到：检索到什么、生成了什么 SQL、
    拿到多少行、结论是什么。这里按固定顺序组装。
    """
    steps: list[AnalysisStep] = []

    if result.rag_context:
        steps.append(
            AnalysisStep(
                title="检索相似示例（RAG）",
                detail=result.rag_context,
                kind="prompt",
            )
        )

    if result.metric_bindings:
        names = "、".join(
            str(item.get("standard_field") or item.get("name") or "")
            for item in result.metric_bindings
        )
        steps.append(
            AnalysisStep(title="对齐业务指标口径", detail=names, kind="prompt")
        )

    if result.sql:
        steps.append(
            AnalysisStep(title="生成并执行只读 SQL", detail=result.sql, kind="sql")
        )

    if result.columns:
        steps.append(
            AnalysisStep(
                title="获取查询结果",
                detail=f"返回 {result.row_count} 行，列：{'、'.join(result.columns)}",
                kind="result",
            )
        )

    if result.sql_error:
        steps.append(
            AnalysisStep(
                title="结果回放失败（结论仍有效）",
                detail=result.sql_error,
                kind="result",
            )
        )

    if result.analysis_text:
        steps.append(
            AnalysisStep(title="生成分析结论", detail=result.analysis_text, kind="report")
        )

    if result.chart_config is not None:
        steps.append(
            AnalysisStep(
                title="生成图表配置",
                detail=f"{result.chart_config.chart_type} · {result.chart_config.reason}",
                kind="chart",
            )
        )

    if result.report:
        steps.append(
            AnalysisStep(title="生成分析报告", detail="已生成 Markdown 报告", kind="report")
        )

    return steps


def build_knowledge_graph(
    knowledge: dict[str, Any], metadata: dict[str, Any]
) -> dict[str, Any]:
    """由知识 + 元数据组合出知识图谱的节点与边。

    重要：``dim_*`` 维表已被删除（当前是单表宽表模型），因此**表间实体关系图
    没有数据支撑**。这里改为「主题 → 业务对象 → 指标 → 字段 → 表」的
    **逻辑知识图谱**，数据全部来自 ``knowledge_service`` 与 ``metadata_service``
    的真实输出，保证图上每个节点都有实际依据。
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_node(node_id: str, name: str, category: str, **extra: Any) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        nodes.append({"id": node_id, "name": name, "category": category, **extra})

    table_names = {t["table_name"] for t in metadata.get("tables", [])}
    field_index: dict[tuple[str, str], bool] = {}
    for table in metadata.get("tables", []):
        for column in table.get("columns", []):
            field_index[(table["table_name"], column["name"])] = True

    # 主题节点 + 主题到对象/表的边
    for theme in knowledge.get("themes", []):
        theme_id = f"theme:{theme.get('code')}"
        add_node(theme_id, str(theme.get("name") or theme.get("code")), "主题",
                 description=theme.get("description", ""))
        for table in theme.get("related_tables", []):
            if table in table_names:
                table_id = f"table:{table}"
                add_node(table_id, table, "数据表", role="业务表")
                edges.append(
                    {"source": theme_id, "target": table_id, "label": "涉及数据表"}
                )

    # 对象节点 + 对象到默认表的边
    for obj in knowledge.get("objects", []):
        object_id = f"object:{obj.get('name')}"
        add_node(object_id, str(obj.get("name")), "业务对象",
                 key_field=obj.get("key_field", ""))
        default_table = obj.get("default_table")
        if default_table in table_names:
            table_id = f"table:{default_table}"
            add_node(table_id, default_table, "数据表", role="业务表")
            edges.append(
                {"source": object_id, "target": table_id, "label": "映射数据表"}
            )

    # 指标节点 + 指标到字段/表的边
    for rule in knowledge.get("rules", []):
        rule_id = f"metric:{rule.get('name')}"
        mapped_field = rule.get("mapped_field")
        resolved = bool(mapped_field)
        add_node(
            rule_id,
            str(rule.get("name")),
            "指标口径",
            expression=rule.get("resolved_calculation", ""),
            resolved=resolved,
        )
        if resolved:
            table_name = rule.get("mapped_table")
            field_id = f"field:{table_name}.{mapped_field}"
            add_node(
                field_id,
                str(mapped_field),
                "字段",
                table=table_name,
            )
            edges.append({"source": rule_id, "target": field_id, "label": "映射字段"})
            if table_name in table_names:
                table_id = f"table:{table_name}"
                edges.append(
                    {"source": field_id, "target": table_id, "label": "属于"}
                )
        else:
            # 未解析到字段的指标：在图上标记为缺口，便于业务侧补齐数据
            edges.append({"source": rule_id, "target": "", "label": "未映射"})

    # 过滤掉 target 为空的无效边
    edges = [edge for edge in edges if edge.get("target")]

    categories = ["主题", "业务对象", "指标口径", "字段", "数据表"]
    return {
        "nodes": nodes,
        "edges": edges,
        "categories": [{"name": name} for name in categories],
    }


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------
_service: Optional[AgentService] = None
_jobs: Optional[JobStore] = None
_singleton_lock = threading.Lock()


def get_service() -> AgentService:
    global _service
    if _service is None:
        with _singleton_lock:
            if _service is None:
                _service = AgentService()
    return _service


def get_job_store() -> JobStore:
    global _jobs
    if _jobs is None:
        with _singleton_lock:
            if _jobs is None:
                _jobs = JobStore()
    return _jobs
