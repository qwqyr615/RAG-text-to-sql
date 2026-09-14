"""HTTP 层请求 / 响应模型（前后端契约的唯一事实来源）。

契约要点（与 ``docs/API_DESIGN.md`` 对齐并向前扩展）：

1. **统一信封**：所有接口返回 ``{code, msg, data}``，与 Java 侧
   ``com.sky.result.Result`` 完全一致，网关可原样透传，无需二次包装。
   - ``code = 1`` 成功，``code = 0`` 失败。
2. **AgentResult 必须显式转换为 dict**：内核的 ``EnterpriseAgent.handle()``
   返回的是 Pydantic 对象，``json.dumps`` 会直接抛
   ``TypeError: Object of type AgentResult is not JSON serializable``；
   ``EnterpriseAgent`` 的 ``type="error"`` 分支返回的又是 ``str``。
   本模块的 :func:`to_ask_response` 负责抹平这两种形态。
3. **多个新字段是前端/大屏新增的**（内核原本预留但未实现）：
   ``chart_config``、``analysis_steps``、``metric_bindings``。

约定：请求体用 camelCase 或 snake_case 均接受（``alias``），响应统一 snake_case。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

#: 内核未返回 SQL 时，图表最多参考多少行，避免 Prompt 过长。
_CHART_ROW_LIMIT = 50


# ---------------------------------------------------------------------------
# 统一信封
# ---------------------------------------------------------------------------
class Envelope(BaseModel):
    """统一返回信封，与 Java ``com.sky.result.Result`` 字段级一致。"""

    code: int = Field(default=1, description="1=成功，0=失败")
    msg: str = Field(default="", description="错误信息")
    data: Any = Field(default=None, description="业务数据")


def ok(data: Any = None) -> dict[str, Any]:
    """成功信封。"""
    return {"code": 1, "msg": "", "data": data}


def fail(msg: str) -> dict[str, Any]:
    """失败信封。"""
    return {"code": 0, "msg": msg, "data": None}


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    """服务健康状态。"""

    status: str = Field(description="ok / degraded")
    agent_ready: bool = Field(description="Agent 内核是否已就绪（元数据已加载）")
    database_type: str = Field(default="", description="数据库方言")
    table_count: int = Field(default=0, description="发现到的业务表数量")
    llm_model: str = Field(default="", description="当前大模型名")
    rag_enabled: bool = Field(default=False, description="RAG 段是否启用")
    error: Optional[str] = Field(default=None, description="就绪失败原因")


# ---------------------------------------------------------------------------
# 智能问答
# ---------------------------------------------------------------------------
class AskRequest(BaseModel):
    """自然语言提问请求。"""

    model_config = ConfigDict(populate_by_name=True)

    question: str = Field(..., min_length=1, description="用户自然语言问题")
    session_id: str = Field(default="default", alias="sessionId", description="会话 ID")
    metadata: str = Field(default="", description="外部补充的数据资源说明")
    business_rules: str = Field(default="", alias="businessRules", description="外部补充口径")
    want_chart: bool = Field(default=True, alias="wantChart", description="是否生成图表配置")
    want_report: bool = Field(default=False, alias="wantReport", description="是否生成 Markdown 报告")


class AnalysisStep(BaseModel):
    """分析过程的一个可展示步骤（用于前端「展示分析过程」）。"""

    title: str = Field(description="步骤标题")
    detail: str = Field(default="", description="步骤详情")
    kind: Literal["prompt", "sql", "result", "report", "chart", "model"] = Field(
        default="result", description="步骤类型，前端据此选渲染方式"
    )


class ChartConfig(BaseModel):
    """ECharts 图表配置。"""

    chart_type: str = Field(default="", description="bar / line / pie / scatter / table")
    title: str = Field(default="", description="图表标题")
    option: dict[str, Any] = Field(default_factory=dict, description="ECharts option 原始配置")
    reason: str = Field(default="", description="选择该图表的理由")
    source: str = Field(default="", description="生成来源：llm / fallback")


class AskResponse(BaseModel):
    """问答统一响应，覆盖 SQL 查询 / 报告 / 建模三类任务的返回。"""

    task_type: str = Field(default="sql_query", description="sql_query / report / anomaly / regression")
    success: bool = Field(default=True)
    question: str = Field(default="")
    session_id: str = Field(default="default")
    turns_used: int = Field(default=0, description="本次注入的历史轮数")
    sql: Optional[str] = Field(default=None, description="生成的 SQL，前端可展示")
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, description="返回行数（等于 rows 长度）")
    analysis_text: str = Field(default="", description="文字分析结论")
    report: Optional[str] = Field(default=None, description="Markdown 报告")
    chart_config: Optional[ChartConfig] = Field(default=None, description="图表配置")
    rag_context: Optional[str] = Field(default=None, description="RAG 命中示例")
    prompt_usage: Optional[dict[str, Any]] = Field(default=None, description="Prompt 各段预算用量")
    analysis_steps: list[AnalysisStep] = Field(
        default_factory=list, description="分析过程步骤，供前端展示推理链路"
    )
    metric_bindings: list[dict[str, Any]] = Field(
        default_factory=list, description="命中的业务指标口径"
    )
    error: Optional[str] = Field(default=None, description="错误信息")
    sql_error: Optional[str] = Field(
        default=None, description="结果回放取数失败原因（分析结论仍有效）"
    )


# ---------------------------------------------------------------------------
# 异步任务（长耗时问答）
# ---------------------------------------------------------------------------
JobStatus = Literal["pending", "running", "succeeded", "failed"]


class JobSubmitResponse(BaseModel):
    """异步任务提交结果。"""

    job_id: str = Field(description="任务 ID，用于轮询或订阅 SSE")
    status: JobStatus = Field(default="pending")


class JobStateResponse(BaseModel):
    """异步任务状态 / 结果。"""

    job_id: str
    status: JobStatus
    created_at: float = Field(description="提交时间戳（秒）")
    finished_at: Optional[float] = Field(default=None, description="完成时间戳（秒）")
    elapsed_ms: Optional[int] = Field(default=None, description="耗时毫秒")
    progress: int = Field(default=0, description="进度百分比 0-100")
    stage: str = Field(default="", description="当前阶段描述，用于前端进度提示")
    result: Optional[AskResponse] = Field(default=None, description="成功时的问答结果")
    error: Optional[str] = Field(default=None, description="失败原因")


# ---------------------------------------------------------------------------
# 建模
# ---------------------------------------------------------------------------
class AnomalyRequest(BaseModel):
    """异常检测请求。"""

    model_config = ConfigDict(populate_by_name=True)

    features: Optional[list[str]] = Field(default=None, description="参与检测的特征列")
    contamination: float = Field(default=0.05, ge=0.001, le=0.5, description="预期异常比例")
    limit: Optional[int] = Field(default=10_000, ge=10, description="最多读取行数")


class RegressionRequest(BaseModel):
    """回归建模请求。"""

    target: str = Field(default="defect_rate", description="目标列")
    features: Optional[list[str]] = Field(default=None, description="特征列")
    limit: Optional[int] = Field(default=10_000, ge=30, description="最多读取行数")
    test_size: float = Field(default=0.2, gt=0, lt=1, alias="testSize", description="测试集比例")

    model_config = ConfigDict(populate_by_name=True)


class TrainRequest(BaseModel):
    """统一建模请求（决策树 / 随机森林 / 逻辑回归 / KMeans）。

    刻意用宽松的可选字段而不是每种算法一个模型：算法参数差异大
    （``max_depth`` / ``n_estimators`` / ``threshold`` / ``n_clusters``），
    为每个算法单独建模型会让网关与前端都要跟着新增接口。
    这里统一入口，由 ``tools.modeling.run_model`` 按算法名分派并校验参数。
    """

    model_config = ConfigDict(populate_by_name=True)

    algorithm: str = Field(
        default="random_forest",
        description="算法名：decision_tree / random_forest / logistic_regression / kmeans",
    )
    target: Optional[str] = Field(
        default=None, description="目标列（有监督算法必填；KMeans 不使用）"
    )
    features: Optional[list[str]] = Field(default=None, description="特征列，不传用默认特征集")
    limit: Optional[int] = Field(default=10_000, ge=30, description="最多读取行数")
    test_size: float = Field(default=0.2, gt=0, lt=1, alias="testSize", description="测试集比例")

    # 决策树 / 随机森林
    max_depth: Optional[int] = Field(
        default=None, ge=1, le=50, alias="maxDepth", description="树最大深度"
    )
    n_estimators: Optional[int] = Field(
        default=None, ge=1, le=500, alias="nEstimators", description="随机森林树数量"
    )
    # 强制指定任务类型：不传则由目标列取值个数自动判定
    task_type: Optional[Literal["classification", "regression"]] = Field(
        default=None, alias="taskType", description="强制任务类型，不传则自动判定"
    )

    # 逻辑回归
    threshold: Optional[float] = Field(
        default=None, description="逻辑回归二分阈值；目标列非天然二分类时生效，默认取中位数"
    )

    # KMeans
    n_clusters: Optional[int] = Field(
        default=None, ge=2, le=20, alias="nClusters", description="聚类数；不传则按轮廓系数自动选"
    )
    max_k: Optional[int] = Field(
        default=None, ge=2, le=20, alias="maxK", description="自动选 k 时的上限"
    )


class AlgorithmInfo(BaseModel):
    """单个算法的元信息，供前端渲染选择卡片与参数表单。"""

    name: str = Field(description="算法标识，传给 /modeling/train 的 algorithm")
    label: str = Field(description="中文名称")
    family: Literal["anomaly", "supervised", "clustering"] = Field(description="算法族")
    supervised: bool = Field(description="是否需要目标列")
    task_type: str = Field(description="任务类型：回归 / 分类 / 聚类 / 异常检测")
    requires_target: bool = Field(description="是否必须提供 target")
    supports_task_auto: bool = Field(
        default=False, description="是否支持按目标列自动判定分类/回归"
    )
    params: list[str] = Field(default_factory=list, description="该算法可调参数名列表")
    description: str = Field(default="", description="算法说明")


# ---------------------------------------------------------------------------
# 内核结果 → 契约对象
# ---------------------------------------------------------------------------
def _extract_error(raw: Any) -> str:
    """从内核的多种失败形态中取出可读错误信息。"""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("error", "msg", "message", "detail"):
            if raw.get(key):
                return str(raw[key])
    return str(raw)


def _model_to_ask_response(raw: dict[str, Any], request: AskRequest) -> AskResponse:
    """把建模结果（异常检测 / 回归）适配成统一响应。

    建模结果不是表格数据，因此把指标摊平成单行 DataFrame 形状，
    前端既能用表格展示，也能复用同一套渲染逻辑。
    """
    task_type = "regression" if "r2_score" in raw else "anomaly"

    if task_type == "anomaly":
        columns = ["指标", "数值"]
        rows: list[list[Any]] = [
            ["样本总数", raw.get("total_records")],
            ["异常数量", raw.get("anomaly_count")],
            ["异常占比", raw.get("anomaly_ratio")],
            ["算法", raw.get("algorithm")],
            ["参与特征", "、".join(raw.get("feature_columns") or [])],
        ]
        analysis_text = (
            f"使用 {raw.get('algorithm')} 对 {raw.get('total_records')} 条记录做异常检测，"
            f"识别出 {raw.get('anomaly_count')} 条异常，"
            f"占比约 {raw.get('anomaly_ratio')}。"
        )
    else:
        coefficients = raw.get("coefficients") or {}
        columns = ["指标", "数值"]
        rows = [
            ["目标字段", raw.get("target")],
            ["算法", raw.get("algorithm")],
            ["R² 拟合优度", raw.get("r2_score")],
            ["RMSE 均方根误差", raw.get("rmse")],
            ["训练集样本数", raw.get("train_size")],
            ["测试集样本数", raw.get("test_size")],
        ]
        if coefficients:
            rows.append(["回归系数", json_dumps(coefficients)])
        analysis_text = (
            f"使用 {raw.get('algorithm')} 预测 {raw.get('target')}，"
            f"R² = {raw.get('r2_score')}，RMSE = {raw.get('rmse')}，"
            f"训练 {raw.get('train_size')} 条 / 测试 {raw.get('test_size')} 条。"
        )

    return AskResponse(
        task_type=task_type,
        success=True,
        question=request.question,
        session_id=request.session_id,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        analysis_text=analysis_text,
        metric_bindings=[],
    )


def json_dumps(value: Any) -> str:
    """紧凑 JSON 序列化（用于把系数等结构塞进表格单元格）。"""
    import json

    return json.dumps(value, ensure_ascii=False)


def to_ask_response(
    response: dict[str, Any],
    *,
    question: str,
    session_id: str,
    want_chart: bool = True,
    want_report: bool = False,
    chart_llm: Any = None,
) -> AskResponse:
    """把 ``EnterpriseAgent.handle()`` 的返回值统一成 :class:`AskResponse`。

    这是适配层最关键的一个函数——内核返回的 ``result`` 有三种形态：
    Pydantic 对象、dict、str，前端不应该感知这种差异。
    """
    kind = (response or {}).get("type")
    raw = (response or {}).get("result")

    # ---- 1. 失败 -----------------------------------------------------
    if kind == "error":
        return AskResponse(
            task_type="sql_query",
            success=False,
            question=question,
            session_id=session_id,
            analysis_text="",
            error=_extract_error(raw),
        )

    # ---- 2. 建模类 ---------------------------------------------------
    if kind == "model" and isinstance(raw, dict):
        return _model_to_ask_response(raw, AskRequest(question=question, sessionId=session_id))

    # ---- 3. SQL / 报告类 ---------------------------------------------
    if kind == "agent" and raw is not None:
        # Pydantic 对象：显式转 dict，否则无法 JSON 序列化
        if hasattr(raw, "model_dump"):
            data = raw.model_dump()
        elif isinstance(raw, dict):
            data = raw
        else:
            return AskResponse(
                task_type="sql_query",
                success=False,
                question=question,
                session_id=session_id,
                error=f"无法识别的 Agent 返回类型：{type(raw).__name__}",
            )

        result = AskResponse(
            task_type="report" if data.get("report") else "sql_query",
            success=bool(data.get("success", True)),
            question=data.get("question") or question,
            session_id=data.get("session_id") or session_id,
            turns_used=int(data.get("turns_used") or 0),
            sql=data.get("sql"),
            columns=list(data.get("columns") or []),
            rows=[list(row) for row in (data.get("rows") or [])],
            analysis_text=data.get("analysis_text") or "",
            report=data.get("report"),
            rag_context=data.get("rag_context") or None,
            prompt_usage=data.get("prompt_usage") or None,
            error=data.get("error"),
            sql_error=data.get("sql_error"),
        )
        result.row_count = len(result.rows)

        # 指标口径：从知识库中挑出本次 SQL 真正用到的字段对应的指标
        result.metric_bindings = _match_metric_bindings(result.sql)

        # 图表：内核未实现，由本层补齐（LLM 出 ECharts option，失败则兜底）
        if want_chart and result.success and result.columns and result.rows:
            result.chart_config = _build_chart(result, chart_llm)

        return result

    # ---- 4. 兜底 -----------------------------------------------------
    return AskResponse(
        task_type="sql_query",
        success=False,
        question=question,
        session_id=session_id,
        error=f"未预期的 Agent 响应：type={kind!r}",
    )


def _match_metric_bindings(sql: Optional[str]) -> list[dict[str, Any]]:
    """从元数据的指标绑定里，找出本次 SQL 实际引用的指标。

    匹配策略（按可靠性排序）：
    1. 标准字段口径表达式（``AVG(defect_rate)``）——客户库列名与业务词不一致时
       模型写的是表达式，命中它最准；
    2. 客户真实列名（``defect_rate``）——模型直接引用列时命中；
    3. 中文指标名（``缺陷率``）——少数场景下模型会在注释里写出指标名。

    对短列名（如 ``id``）做长度保护，避免子串误命中；对结果按名称去重。
    """
    if not sql:
        return []
    try:
        from server.deps import get_service

        bindings = get_service().metadata().get("metric_bindings") or []
    except Exception:  # noqa: BLE001 - 指标匹配失败不影响主流程
        return []

    lowered = sql.lower()
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in bindings:
        identity = str(item.get("standard_field") or item.get("column") or "")
        if not identity or identity in seen:
            continue

        candidates = [
            str(item.get("expression") or ""),
            str(item.get("column") or ""),
            str(item.get("label") or ""),
        ]
        hit = False
        for candidate in candidates:
            if not candidate:
                continue
            # 英文标识符做大小写无关匹配，中文按原样匹配
            probe = candidate.lower() if candidate.isascii() else candidate
            if probe.isascii() and len(probe) < 3:
                continue
            if probe in lowered:
                hit = True
                break

        if hit:
            seen.add(identity)
            matched.append(item)

    return matched


def _build_chart(result: AskResponse, chart_llm: Any) -> Optional[ChartConfig]:
    """生成图表配置；任何异常都降级为「无图」，不影响问答结果。"""
    if chart_llm is None:
        return None
    try:
        from agents.chart_agent import ChartGenerator

        generated = ChartGenerator(chart_llm).generate(
            result.question,
            result.columns,
            result.rows[:_CHART_ROW_LIMIT],
            sql=result.sql,
        )
        if not generated:
            return None
        return ChartConfig(
            chart_type=generated.get("chart_type", ""),
            title=generated.get("title", ""),
            option=generated.get("option") or {},
            reason=generated.get("reason", ""),
            source=generated.get("source", ""),
        )
    except Exception as exc:  # noqa: BLE001 - 图表失败绝不影响问答
        import logging

        logging.getLogger(__name__).warning("图表配置生成失败：%s", exc)
        return None

