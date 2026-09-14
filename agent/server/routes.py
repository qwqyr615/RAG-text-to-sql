"""HTTP 路由定义。

按业务域划分四个 router：
- ``/api/v1/metadata``  数据资源理解
- ``/api/v1/knowledge`` 业务知识管理（含知识图谱）
- ``/api/v1/agent``     自然语言智能分析（同步 / 异步 / SSE）
- ``/api/v1/modeling``  机器学习建模

所有返回值统一走 :func:`server.schemas.ok` / :func:`server.schemas.fail` 信封，
与 Java 侧 ``com.sky.result.Result`` 字段级一致，网关可直接透传。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from server.deps import build_analysis_steps, get_job_store, get_service
from server.schemas import (
    AnomalyRequest,
    AskRequest,
    Envelope,
    JobStateResponse,
    JobSubmitResponse,
    RegressionRequest,
    TrainRequest,
    ok,
)

logger = logging.getLogger(__name__)

metadata_router = APIRouter(prefix="/api/v1/metadata", tags=["数据资源理解"])
knowledge_router = APIRouter(prefix="/api/v1/knowledge", tags=["业务知识管理"])
agent_router = APIRouter(prefix="/api/v1/agent", tags=["智能分析"])
modeling_router = APIRouter(prefix="/api/v1/modeling", tags=["机器建模"])
system_router = APIRouter(prefix="/api/v1/system", tags=["系统"])


# ---------------------------------------------------------------------------
# 系统
# ---------------------------------------------------------------------------
@system_router.get("/health", response_model=Envelope, summary="健康检查")
def health() -> dict[str, Any]:
    """返回服务与内核就绪状态；Java 网关与前端可据此做启动探测。"""
    return ok(get_service().health())


# ---------------------------------------------------------------------------
# 数据资源理解
# ---------------------------------------------------------------------------
@metadata_router.get("/tables", response_model=Envelope, summary="表结构 / 字段 / 样例值")
def metadata_tables() -> dict[str, Any]:
    """获取全部业务表、字段、类型、说明、样例值（数据资源页）。"""
    return ok(get_service().metadata())


@metadata_router.get(
    "/relationships", response_model=Envelope, summary="表间关系（供图谱使用）"
)
def metadata_relationships() -> dict[str, Any]:
    """表间关系。

    注意：当前数据模型是**单表宽表**（``dim_*`` 维表已删除），因此
    ``relationships`` 通常为空；知识图谱请改用
    ``GET /api/v1/knowledge/graph``（基于知识库的逻辑图谱）。
    """
    metadata = get_service().metadata()
    return ok(
        {
            "relationships": metadata.get("relationships", []),
            "database_type": metadata.get("database_type", ""),
            "note": "单表宽表模型下无维表关系；请使用 /api/v1/knowledge/graph 获取知识图谱。",
        }
    )


@metadata_router.get("/field-map", response_model=Envelope, summary="标准字段口径映射")
def metadata_field_map() -> dict[str, Any]:
    """标准字段 ↔ 客户列的口径映射，含单位换算与枚举取值。"""
    metadata = get_service().metadata()
    return ok(
        {
            "field_map": metadata.get("field_map", {}),
            "metric_bindings": metadata.get("metric_bindings", []),
            "unmatched_metrics": metadata.get("unmatched_metrics", []),
            "mapping_profile": metadata.get("mapping_profile", ""),
        }
    )


# ---------------------------------------------------------------------------
# 业务知识管理
# ---------------------------------------------------------------------------
@knowledge_router.get("/overview", response_model=Envelope, summary="主题/对象/规则总览")
def knowledge_overview() -> dict[str, Any]:
    """业务知识总览：分析主题、业务对象、指标规则。"""
    return ok(get_service().knowledge())


@knowledge_router.get("/graph", response_model=Envelope, summary="知识图谱节点与边")
def knowledge_graph() -> dict[str, Any]:
    """逻辑知识图谱：主题 → 业务对象 → 指标 → 字段 → 数据表。

    数据全部来自真实的 metadata / knowledge 解析结果，图上每个节点都有依据。
    """
    return ok(get_service().knowledge(include_graph=True)["graph"])


# ---------------------------------------------------------------------------
# 智能分析：同步
# ---------------------------------------------------------------------------
@agent_router.post("/ask", response_model=Envelope, summary="自然语言问答（同步）")
def agent_ask(request: AskRequest) -> dict[str, Any]:
    """同步问答。

    ⚠️ 最长可能阻塞 ``SQL_AGENT_MAX_EXECUTION_TIME``（默认 90 秒）。
    前端/大屏建议改用 ``POST /ask/async`` + 轮询或 SSE，避免请求超时。
    """
    try:
        result = get_service().ask(request)
    except Exception as exc:  # noqa: BLE001
        logger.exception("问答失败")
        return {"code": 0, "msg": str(exc), "data": None}

    result.analysis_steps = build_analysis_steps(result)
    payload = result.model_dump()
    return ok(payload) if result.success else {"code": 0, "msg": result.error or "分析失败", "data": payload}


@agent_router.post("/report", response_model=Envelope, summary="生成 Markdown 报告")
def agent_report(request: AskRequest) -> dict[str, Any]:
    """生成分析报告（内部先执行查询，再生成报告）。"""
    request.want_report = True
    return agent_ask(request)


@agent_router.delete("/session/{session_id}", response_model=Envelope, summary="清空会话历史")
def agent_reset_session(session_id: str) -> dict[str, Any]:
    """清空指定会话的多轮历史，对应 CLI 里的 ``new``。"""
    get_service().reset_session(session_id)
    return ok({"session_id": session_id, "reset": True})


# ---------------------------------------------------------------------------
# 智能分析：异步任务
# ---------------------------------------------------------------------------
def _sse(event: str, data: dict[str, Any]) -> str:
    """把一次事件编码成 SSE 帧。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _run_job(job_id: str, request: AskRequest) -> None:
    """后台线程执行一次问答，并持续推进任务状态。"""
    store = get_job_store()
    started = time.time()
    try:
        service = get_service()

        store.update(job_id, status="running", progress=5, stage="正在准备分析上下文…")

        # 内核是同步阻塞的，这里无法细粒度打断；用「开始 / 完成」两个阶段
        # 配合前端计时器给出进度反馈。
        store.update(job_id, progress=20, stage="正在检索业务知识与相似示例…")
        result = service.ask(request)

        store.update(job_id, progress=85, stage="正在整理分析结论…")
        result.analysis_steps = build_analysis_steps(result)

        finished = time.time()
        if result.success:
            store.update(
                job_id,
                status="succeeded",
                progress=100,
                stage="分析完成",
                result=result,
                finished_at=finished,
                elapsed_ms=int((finished - started) * 1000),
            )
        else:
            store.update(
                job_id,
                status="failed",
                progress=100,
                stage="分析失败",
                result=result,
                error=result.error or "分析失败",
                finished_at=finished,
                elapsed_ms=int((finished - started) * 1000),
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("异步任务执行失败 job_id=%s", job_id)
        finished = time.time()
        store.update(
            job_id,
            status="failed",
            progress=100,
            stage="执行异常",
            error=str(exc),
            finished_at=finished,
            elapsed_ms=int((finished - started) * 1000),
        )


@agent_router.post(
    "/ask/async", response_model=Envelope, summary="自然语言问答（异步提交）"
)
def agent_ask_async(request: AskRequest) -> dict[str, Any]:
    """提交异步任务，立即返回 ``job_id``；随后轮询或订阅 SSE 取结果。"""
    job = get_job_store().create()
    thread = threading.Thread(
        target=_run_job,
        args=(job.job_id, request),
        name=f"ask-job-{job.job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return ok(JobSubmitResponse(job_id=job.job_id, status="pending").model_dump())


@agent_router.get(
    "/jobs/{job_id}", response_model=Envelope, summary="查询异步任务状态与结果"
)
def agent_job(job_id: str) -> dict[str, Any]:
    job = get_job_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{job_id}")
    return ok(job.model_dump())


@agent_router.get("/jobs/{job_id}/stream", summary="SSE 订阅任务进度")
async def agent_job_stream(job_id: str) -> StreamingResponse:
    """以 SSE 推送任务状态变化。

    事件格式（与前端 ``EventSource`` / fetch-stream 解析约定一致）::

        event: progress
        data: {"job_id":"...","status":"running","progress":20,...}

        event: done
        data: {完整结果}

    前端拿到 ``done`` 后即可关闭连接。
    """
    store = get_job_store()
    if store.get(job_id) is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{job_id}")

    async def event_source() -> AsyncIterator[str]:
        last_progress = -1
        # 最多推送 10 分钟，避免连接泄漏
        deadline = time.time() + 600

        while time.time() < deadline:
            job = store.get(job_id)
            if job is None:
                yield _sse("error", {"error": "任务不存在"})
                return

            snapshot = job.model_dump()
            if job.progress != last_progress or job.status in ("succeeded", "failed"):
                last_progress = job.progress
                yield _sse(
                    "done" if job.status in ("succeeded", "failed") else "progress",
                    snapshot,
                )

            if job.status in ("succeeded", "failed"):
                return

            # 用线程条件变量等待状态变化，避免忙轮询；不阻塞事件循环
            await asyncio.to_thread(store.wait_for_change, job_id, 15.0)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关闭 Nginx 缓冲，保证事件即时到达
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# 建模
# ---------------------------------------------------------------------------
#: 算法元信息：驱动前端的算法选择与参数表单。新增算法只需在这里加一条，
#: 前端会自动出现对应入口（见 ModelingPage 的 ALGORITHM_CARDS 渲染逻辑）。
ALGORITHM_CATALOG: list[dict[str, Any]] = [
    {
        "name": "isolation_forest",
        "label": "异常检测 · Isolation Forest",
        "family": "anomaly",
        "supervised": False,
        "task_type": "异常检测",
        "requires_target": False,
        "supports_task_auto": False,
        "params": ["contamination", "features", "limit"],
        "description": "无监督异常检测，适合发现偏离整体分布的设备与批次记录。",
    },
    {
        "name": "linear_regression",
        "label": "线性回归 · LinearRegression",
        "family": "supervised",
        "supervised": True,
        "task_type": "回归",
        "requires_target": True,
        "supports_task_auto": False,
        "params": ["target", "features", "testSize", "limit"],
        "description": "线性拟合，系数方向可直接解释特征对目标的正负影响。",
    },
    {
        "name": "decision_tree",
        "label": "决策树 · DecisionTree",
        "family": "supervised",
        "supervised": True,
        "task_type": "回归 / 分类",
        "requires_target": True,
        "supports_task_auto": True,
        "params": ["target", "features", "maxDepth", "testSize", "taskType"],
        "description": "可解释性最强：给出特征重要性与决策规则，按目标列自动判定分类或回归。",
    },
    {
        "name": "random_forest",
        "label": "随机森林 · RandomForest",
        "family": "supervised",
        "supervised": True,
        "task_type": "回归 / 分类",
        "requires_target": True,
        "supports_task_auto": True,
        "params": ["target", "features", "nEstimators", "maxDepth", "testSize", "taskType"],
        "description": "多棵树集成，通常比单棵决策树更稳、泛化更好，特征重要性也更可靠。",
    },
    {
        "name": "logistic_regression",
        "label": "逻辑回归 · LogisticRegression",
        "family": "supervised",
        "supervised": True,
        "task_type": "二分类",
        "requires_target": True,
        "supports_task_auto": False,
        "params": ["target", "features", "threshold", "testSize"],
        "description": "二分类建模。目标列非天然二分类时按阈值（默认中位数）二分，输出概率与系数。",
    },
    {
        "name": "kmeans",
        "label": "聚类 · KMeans",
        "family": "clustering",
        "supervised": False,
        "task_type": "聚类",
        "requires_target": False,
        "supports_task_auto": False,
        "params": ["features", "nClusters", "maxK", "limit"],
        "description": "按特征相似度分群。不指定 k 时自动在 2..6 之间按轮廓系数择优。",
    },
]


@modeling_router.get("/algorithms", response_model=Envelope, summary="支持的建模算法")
def modeling_algorithms() -> dict[str, Any]:
    """列出全部可用算法及其元信息，供前端渲染选择卡片与参数表单。"""
    return ok(
        {
            "algorithms": ALGORITHM_CATALOG,
            "total": len(ALGORITHM_CATALOG),
            "table": _modeling_table(),
        }
    )


def _modeling_table() -> str:
    """建模模块实际查询的表名（供前端提示，避免字段与算法对不上）。"""
    try:
        from tools.modeling import TABLE_NAME

        return TABLE_NAME
    except Exception:  # noqa: BLE001
        return ""


@modeling_router.post("/train", response_model=Envelope, summary="统一建模入口")
def modeling_train(request: TrainRequest) -> dict[str, Any]:
    """统一建模入口：决策树 / 随机森林 / 逻辑回归 / KMeans。

    与 ``/anomaly``、``/regression`` 的关系：这两个是早期为便于调试而单独开放的
    专用接口，本接口是统一入口。两者最终都调用 ``tools.modeling`` 的同一批函数，
    因此结果结构一致。

    参数校验交给 ``tools.modeling.run_model``：算法不同，必填参数不同
    （例如有监督算法必须有 ``target``），把这类规则集中在建模层，
    避免在 HTTP 层与 Java 网关重复实现导致不一致。
    """
    from tools.modeling import run_model

    payload: dict[str, Any] = {
        "features": request.features,
        "limit": request.limit,
    }
    # 仅传递显式提供的参数：None 会让建模层退回各自的默认值
    if request.target is not None:
        payload["target"] = request.target
    if request.test_size is not None:
        payload["test_size"] = request.test_size
    if request.max_depth is not None:
        payload["max_depth"] = request.max_depth
    if request.n_estimators is not None:
        payload["n_estimators"] = request.n_estimators
    if request.task_type is not None:
        payload["task_type"] = request.task_type
    if request.threshold is not None:
        payload["threshold"] = request.threshold
    if request.n_clusters is not None:
        payload["n_clusters"] = request.n_clusters
    if request.max_k is not None:
        payload["max_k"] = request.max_k

    # 有监督算法缺 target 时给出明确提示，而不是让建模层抛 KeyError 风格的信息
    from tools.modeling import SUPPORTED_ALGORITHMS

    algorithm = (request.algorithm or "").strip().lower()
    if algorithm not in SUPPORTED_ALGORITHMS:
        return {
            "code": 0,
            "msg": f"不支持的算法 {request.algorithm!r}，可选：{', '.join(SUPPORTED_ALGORITHMS)}",
            "data": None,
        }
    entry = next((item for item in ALGORITHM_CATALOG if item["name"] == algorithm), None)
    if entry and entry["requires_target"] and not request.target:
        return {"code": 0, "msg": f"{entry['label']} 必须提供 target（目标列）", "data": None}

    try:
        result = run_model(algorithm, payload)
    except ValueError as exc:
        # 参数或数据不满足算法要求：属于可预期的用户输入问题，返回可读信息
        logger.warning("建模参数/数据校验失败 algorithm=%s：%s", algorithm, exc)
        return {"code": 0, "msg": str(exc), "data": None}
    except Exception as exc:  # noqa: BLE001
        logger.exception("建模失败 algorithm=%s", algorithm)
        return {"code": 0, "msg": f"建模失败：{exc}", "data": None}
    return ok(result)


@modeling_router.post("/anomaly", response_model=Envelope, summary="Isolation Forest 异常检测")
def modeling_anomaly(request: AnomalyRequest) -> dict[str, Any]:
    """异常检测。"""
    try:
        from tools.modeling import run_anomaly_detection

        result = run_anomaly_detection(
            features=request.features,
            contamination=request.contamination,
            limit=request.limit,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("异常检测失败")
        return {"code": 0, "msg": str(exc), "data": None}
    return ok(result)


@modeling_router.post("/regression", response_model=Envelope, summary="线性回归建模")
def modeling_regression(request: RegressionRequest) -> dict[str, Any]:
    """线性回归训练与评估。"""
    try:
        from tools.modeling import run_linear_regression

        result = run_linear_regression(
            target=request.target,
            features=request.features,
            limit=request.limit,
            test_size=request.test_size,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("回归建模失败")
        return {"code": 0, "msg": str(exc), "data": None}
    return ok(result)


@modeling_router.get("/features", response_model=Envelope, summary="可用建模字段")
def modeling_features(
    role: Optional[str] = Query(default=None, description="过滤：numeric / all")
) -> dict[str, Any]:
    """列出可用于建模的字段，供前端下拉选择。

    只返回 ``tools.modeling`` 实际查询的那张表（默认 ``fact_production_record``）的字段。

    这一点很关键：数据库里可能有多个数据源（例如客户原始表 ``mes_prod_log``
    用的是 ``mot_t``/``def_rate`` 这类缩写列名），而建模模块只读一张表。
    如果把所有表的列都列出来，用户选中一个不属于该表的列就会报
    「特征列不存在」。因此这里按建模表过滤，并额外提供该模块的
    ``default_features``，让前端可以合理地预选。
    """
    from tools.modeling import (
        DEFAULT_ANOMALY_FEATURES,
        DEFAULT_REGRESSION_FEATURES,
        TABLE_NAME,
    )

    metadata = get_service().metadata()
    target_table = next(
        (table for table in metadata.get("tables", []) if table["table_name"] == TABLE_NAME),
        None,
    )

    fields: list[dict[str, Any]] = []
    if target_table is not None:
        for column in target_table.get("columns", []):
            type_name = str(column.get("type", "")).upper()
            is_numeric = any(
                token in type_name
                for token in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "NUMERIC", "REAL", "BIGINT")
            )
            if role == "numeric" and not is_numeric:
                continue
            fields.append(
                {
                    "table": target_table["table_name"],
                    "name": column["name"],
                    "type": type_name,
                    "description": column.get("description", ""),
                    "numeric": is_numeric,
                }
            )

    available = {field["name"] for field in fields}
    return ok(
        {
            "fields": fields,
            "total": len(fields),
            "table": TABLE_NAME,
            "default_features": {
                "anomaly": [name for name in DEFAULT_ANOMALY_FEATURES if name in available],
                "regression": [name for name in DEFAULT_REGRESSION_FEATURES if name in available],
            },
            "note": (
                f"建模模块只查询 {TABLE_NAME} 表；如需分析其他数据源，"
                "请先在 mapping.yaml 中完成字段映射。"
            ),
        }
    )
