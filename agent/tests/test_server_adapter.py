"""HTTP 适配层测试。

覆盖前后端契约的三类风险点，**不依赖大模型调用与真实数据库**：

1. ``to_ask_response`` 必须抹平内核的三种返回形态（Pydantic / dict / str），
   否则 FastAPI 序列化时抛 ``TypeError``；
2. 统一信封 ``{code, msg, data}`` 必须与 Java ``com.sky.result.Result`` 字段一致；
3. 图表与知识图谱在 LLM 不可用时必须有确定性兜底，不能把异常抛给调用方。
"""

from __future__ import annotations

import json

import pytest

from server.deps import JobStore, build_analysis_steps, build_knowledge_graph
from server.schemas import (
    AskRequest,
    ChartConfig,
    Envelope,
    ok,
    to_ask_response,
)
from schemas.agent_io import AgentResult


# ---------------------------------------------------------------------------
# 统一信封
# ---------------------------------------------------------------------------
def test_envelope_matches_java_result_contract():
    """信封字段必须与 Java Result(code/msg/data) 完全一致。"""
    envelope = Envelope(**ok({"a": 1}))
    assert sorted(envelope.model_dump().keys()) == ["code", "data", "msg"]
    assert envelope.code == 1

    from server.schemas import fail

    failed = Envelope(**fail("出错了"))
    assert failed.code == 0
    assert failed.msg == "出错了"
    assert failed.data is None


def test_envelope_is_json_serializable():
    """信封必须能直接 JSON 序列化（Java 侧要原样透传）。"""
    payload = ok({"tables": [{"name": "t"}]})
    encoded = json.dumps(payload, ensure_ascii=False)
    assert json.loads(encoded)["code"] == 1


# ---------------------------------------------------------------------------
# 内核结果 → 契约对象
# ---------------------------------------------------------------------------
def _fake_agent_result(**overrides):
    """构造一个真实的内核 ``AgentResult``（Pydantic 模型）。"""
    defaults = {
        "question": "统计各生产线平均缺陷率",
        "session_id": "s1",
        "turns_used": 0,
        "sql": "SELECT production_line, AVG(defect_rate) FROM fact_production_record GROUP BY production_line",
        "columns": ["production_line", "avg_defect_rate"],
        "rows": [["Line_A", 3.91], ["Line_B", 3.90]],
        "chart_config": None,
        "analysis_text": "各产线缺陷率接近。",
        "rag_context": "示例：统计不同生产线的平均缺陷率",
        "report": None,
        "error": None,
        "sql_error": None,
        "prompt_usage": {"used_chars": 100, "total_budget": 8000, "sections": []},
        "success": True,
    }
    defaults.update(overrides)
    return AgentResult(**defaults)


def test_pydantic_result_is_converted_to_plain_dict():
    """内核返回的 Pydantic 结果必须被转成普通结构，否则无法 JSON 序列化。"""
    model = _fake_agent_result()
    # 这正是适配层存在的理由：FastAPI 无法直接序列化这个对象
    with pytest.raises(TypeError):
        json.dumps(model)

    response = to_ask_response(
        {"type": "agent", "result": model},
        question="q",
        session_id="s1",
        want_chart=False,
    )
    assert response.success is True
    assert response.task_type == "sql_query"
    assert response.columns == ["production_line", "avg_defect_rate"]
    assert response.row_count == 2

    encoded = json.dumps(response.model_dump(), ensure_ascii=False)
    assert "Line_A" in encoded


def test_error_type_returns_readable_message():
    """内核 type="error" 时 result 是 str，必须被转成失败响应。"""
    response = to_ask_response(
        {"type": "error", "result": "数据库连接失败"},
        question="q",
        session_id="s1",
    )
    assert response.success is False
    assert response.error == "数据库连接失败"


def test_dict_result_type_error_is_extracted():
    """错误也可能是 dict，需取出可读 message。"""
    response = to_ask_response(
        {"type": "error", "result": {"message": "元数据为空"}},
        question="q",
        session_id="s1",
    )
    assert response.error == "元数据为空"


def test_report_result_marks_task_type():
    """带 report 的结果应标记为 report 任务。"""
    response = to_ask_response(
        {"type": "agent", "result": _fake_agent_result(report="## 质量分析报告")},
        question="生成一份质量分析报告",
        session_id="s1",
        want_chart=False,
    )
    assert response.task_type == "report"
    assert response.report.startswith("## 质量分析报告")


def test_anomaly_model_result_is_flattened():
    """建模结果不是表格数据，需摊平成可展示的行。"""
    raw = {
        "task_type": "异常检测",
        "algorithm": "IsolationForest",
        "total_records": 10000,
        "anomaly_count": 500,
        "anomaly_ratio": 0.05,
        "feature_columns": ["defect_rate", "downtime_minutes"],
        "records": [],
    }
    response = to_ask_response(
        {"type": "model", "result": raw},
        question="找出异常数据",
        session_id="s1",
        want_chart=False,
    )
    assert response.task_type == "anomaly"
    assert response.success is True
    assert response.row_count == len(response.rows) > 0
    assert "10000" in response.analysis_text


def test_regression_model_result_is_flattened():
    raw = {
        "task_type": "回归预测建模",
        "algorithm": "LinearRegression",
        "target": "defect_rate",
        "r2_score": 0.76,
        "rmse": 0.35,
        "train_size": 8000,
        "test_size": 2000,
        "coefficients": {"quality_score": -0.12},
        "feature_columns": ["quality_score"],
    }
    response = to_ask_response(
        {"type": "model", "result": raw},
        question="用回归预测缺陷率",
        session_id="s1",
        want_chart=False,
    )
    assert response.task_type == "regression"
    assert "0.76" in response.analysis_text


def test_unexpected_shape_does_not_raise():
    """未预期形态必须降级为失败响应，而不是抛异常。"""
    response = to_ask_response(
        {"type": "unknown", "result": object()},
        question="q",
        session_id="s1",
    )
    assert response.success is False
    assert response.error


def test_sql_error_keeps_conclusion():
    """取数失败不应丢掉已生成的分析结论。"""
    response = to_ask_response(
        {
            "type": "agent",
            "result": _fake_agent_result(
                rows=[], columns=[], sql_error="回放超时", analysis_text="结论仍有效"
            ),
        },
        question="q",
        session_id="s1",
        want_chart=False,
    )
    assert response.sql_error == "回放超时"
    assert response.analysis_text == "结论仍有效"


# ---------------------------------------------------------------------------
# 分析过程步骤
# ---------------------------------------------------------------------------
def test_analysis_steps_cover_sql_result_conclusion():
    """分析过程要能展示 SQL 与结果，满足「展示分析过程」的评分点。"""
    response = to_ask_response(
        {"type": "agent", "result": _fake_agent_result()},
        question="q",
        session_id="s1",
        want_chart=False,
    )
    steps = build_analysis_steps(response)
    kinds = [step.kind for step in steps]
    assert "sql" in kinds
    assert "result" in kinds
    assert "report" in kinds


def test_analysis_steps_include_chart_when_present():
    response = to_ask_response(
        {"type": "agent", "result": _fake_agent_result()},
        question="q",
        session_id="s1",
        want_chart=False,
    )
    response.chart_config = ChartConfig(
        chart_type="bar", title="t", option={"xAxis": {}}, reason="r", source="llm"
    )
    kinds = [step.kind for step in build_analysis_steps(response)]
    assert "chart" in kinds


# ---------------------------------------------------------------------------
# 知识图谱
# ---------------------------------------------------------------------------
def test_knowledge_graph_has_no_dangling_edges():
    """图上每条边的两端都必须是已存在的节点，否则前端渲染会报错。"""
    knowledge = {
        "themes": [
            {"code": "quality_analysis", "name": "质量分析", "related_tables": ["fact"]}
        ],
        "objects": [{"name": "设备", "default_table": "fact", "key_field": "machine_id"}],
        "rules": [
            {
                "name": "缺陷率",
                "mapped_table": "fact",
                "mapped_field": "defect_rate",
                "resolved_calculation": "AVG(defect_rate)",
            },
            {
                "name": "不存在的指标",
                "mapped_table": None,
                "mapped_field": None,
                "resolved_calculation": "当前数据源缺少该指标字段",
            },
        ],
    }
    metadata = {
        "tables": [
            {
                "table_name": "fact",
                "columns": [{"name": "defect_rate"}, {"name": "machine_id"}],
            }
        ]
    }
    graph = build_knowledge_graph(knowledge, metadata)

    node_ids = {node["id"] for node in graph["nodes"]}
    assert node_ids, "图谱不应为空"
    for edge in graph["edges"]:
        assert edge["source"] in node_ids, f"悬空起点: {edge}"
        assert edge["target"] in node_ids, f"悬空终点: {edge}"

    # 未解析的指标仍要出现在图上，作为待补数据的缺口
    unresolved = [n for n in graph["nodes"] if n["id"] == "metric:不存在的指标"]
    assert unresolved and unresolved[0]["resolved"] is False


def test_knowledge_graph_categories_present():
    graph = build_knowledge_graph({"themes": [], "objects": [], "rules": []}, {"tables": []})
    names = [category["name"] for category in graph["categories"]]
    assert "主题" in names and "指标口径" in names and "数据表" in names


# ---------------------------------------------------------------------------
# 异步任务
# ---------------------------------------------------------------------------
def test_job_store_lifecycle():
    store = JobStore()
    job = store.create()
    assert job.status == "pending"

    store.update(job.job_id, status="running", progress=40, stage="分析中")
    assert store.get(job.job_id).progress == 40

    store.update(job.job_id, status="succeeded", progress=100)
    assert store.get(job.job_id).status == "succeeded"
    # 已完成的任务不再等待
    assert store.wait_for_change(job.job_id, timeout=0.01) is False


def test_job_store_evicts_finished_jobs():
    """任务表不能无限增长（长跑的服务会内存泄漏）。"""
    store = JobStore(max_jobs=5)
    for _ in range(12):
        job = store.create()
        store.update(job.job_id, status="succeeded")
    assert len(store._jobs) <= 6


def test_job_store_unknown_job():
    store = JobStore()
    assert store.get("nope") is None


# ---------------------------------------------------------------------------
# 图表兜底与解析
# ---------------------------------------------------------------------------
def test_chart_fallback_builds_bar_chart():
    """大模型不可用时必须给出确定性图表。"""
    from agents.chart_agent import ChartGenerator

    chart = ChartGenerator(llm=None)._fallback(
        "统计各生产线平均缺陷率",
        ["production_line", "avg_defect_rate"],
        [["Line_A", 3.91], ["Line_B", 3.90]],
    )
    assert chart is not None
    assert chart["chart_type"] == "bar"
    assert chart["option"]["xAxis"]["data"] == ["Line_A", "Line_B"]
    assert chart["option"]["series"][0]["data"] == [3.91, 3.90]


def test_chart_fallback_picks_line_for_trend_question():
    from agents.chart_agent import ChartGenerator

    chart = ChartGenerator(llm=None)._fallback(
        "统计每条产线最近 7 天的产量趋势",
        ["day", "production_volume"],
        [["D1", 100], ["D2", 120]],
    )
    assert chart["chart_type"] == "line"


def test_chart_fallback_returns_none_without_numeric_column():
    """全是文本列时不该硬画图。"""
    from agents.chart_agent import ChartGenerator

    chart = ChartGenerator(llm=None)._fallback(
        "列出所有设备", ["machine_id", "operation_mode"], [["M01", "Balanced"]]
    )
    assert chart is None


def test_chart_fallback_returns_none_for_single_column():
    from agents.chart_agent import ChartGenerator

    assert ChartGenerator(llm=None)._fallback("q", ["only"], [["a"]]) is None


def test_extract_json_handles_markdown_fence():
    """模型经常把 JSON 包在 ```json 里。"""
    from agents.chart_agent import ChartGenerator

    fenced = '```json\n{"chart_type":"bar","option":{"series":[]}}\n```'
    assert ChartGenerator._extract_json(fenced) == '{"chart_type":"bar","option":{"series":[]}}'


def test_extract_json_handles_nested_braces():
    from agents.chart_agent import ChartGenerator

    text = '前导说明 {"a":{"b":{"c":1}},"d":[1,2]} 后续说明'
    extracted = ChartGenerator._extract_json(text)
    assert json.loads(extracted) == {"a": {"b": {"c": 1}}, "d": [1, 2]}


def test_extract_json_ignores_braces_inside_strings():
    from agents.chart_agent import ChartGenerator

    text = '{"formatter":"{value} 个"}'
    assert json.loads(ChartGenerator._extract_json(text)) == {"formatter": "{value} 个"}


def test_parse_rejects_invalid_chart_type():
    from agents.chart_agent import ChartGenerator

    generator = ChartGenerator(llm=None)
    assert generator._parse('{"chart_type":"radar","option":{"x":1}}') is None
    assert generator._parse("not json at all") is None
    assert generator._parse('{"chart_type":"bar"}') is None  # 缺 option


def test_parse_accepts_table_type():
    from agents.chart_agent import ChartGenerator

    parsed = ChartGenerator(llm=None)._parse(
        '{"chart_type":"table","title":"t","reason":"r"}'
    )
    assert parsed["chart_type"] == "table"
    assert parsed["option"] == {}


def test_chart_generation_survives_llm_failure():
    """LLM 抛异常时必须回退兜底，绝不能让问答失败。"""

    class ExplodingLlm:
        def invoke(self, _messages):
            raise RuntimeError("模拟大模型超时")

    from agents.chart_agent import ChartGenerator

    chart = ChartGenerator(ExplodingLlm()).generate(
        "统计各生产线平均缺陷率",
        ["production_line", "avg_defect_rate"],
        [["Line_A", 3.91]],
    )
    assert chart is not None
    assert chart["source"] == "fallback"


# ---------------------------------------------------------------------------
# 建模字段白名单
# ---------------------------------------------------------------------------
def test_modeling_features_only_returns_modeling_table(monkeypatch):
    """建模字段必须只来自建模模块真正查询的那张表。

    回归测试：数据库里可能存在多个数据源（例如客户原始表 mes_prod_log 用
    ``mot_t``/``def_rate`` 这类缩写列名），而 tools.modeling 只读一张表。
    早期实现把所有表的列都返回给前端，用户选中不属于该表的列就会报
    「特征列不存在」。这里锁定「只返回建模表字段」这一不变量。
    """
    import server.routes as routes
    from tools.modeling import (
        DEFAULT_ANOMALY_FEATURES,
        DEFAULT_REGRESSION_FEATURES,
        TABLE_NAME,
    )

    fact_columns = [
        {"name": "defect_rate", "type": "DOUBLE", "description": "缺陷率"},
        {"name": "downtime_minutes", "type": "DOUBLE", "description": "停机时长"},
        {"name": "machine_id", "type": "VARCHAR(50)", "description": "设备编码"},
    ]
    other_columns = [
        {"name": "brg_t", "type": "DOUBLE", "description": "轴承温度（客户缩写列名）"},
        {"name": "def_rate", "type": "DOUBLE", "description": "缺陷率（客户缩写列名）"},
    ]
    fake_metadata = {
        "tables": [
            {"table_name": TABLE_NAME, "columns": fact_columns},
            {"table_name": "mes_prod_log", "columns": other_columns},
        ]
    }

    class FakeService:
        def metadata(self):
            return fake_metadata

    monkeypatch.setattr(routes, "get_service", lambda: FakeService())

    payload = routes.modeling_features(role="numeric")
    assert payload["code"] == 1
    data = payload["data"]

    assert data["table"] == TABLE_NAME
    names = {field["name"] for field in data["fields"]}

    # 只包含建模表的数值列
    assert "defect_rate" in names
    assert "downtime_minutes" in names
    # 非数值列被 role=numeric 过滤
    assert "machine_id" not in names
    # 其他数据源的列绝不能出现
    assert "brg_t" not in names
    assert "def_rate" not in names

    # 默认特征集必须是「该表真实存在的列」的子集
    defaults = data["default_features"]
    assert set(defaults["anomaly"]) <= names
    assert set(defaults["regression"]) <= names
    assert set(defaults["anomaly"]) == {
        name for name in DEFAULT_ANOMALY_FEATURES if name in names
    }
    assert set(defaults["regression"]) == {
        name for name in DEFAULT_REGRESSION_FEATURES if name in names
    }


def test_modeling_features_all_role_includes_text_columns(monkeypatch):
    """role 省略时应返回全部字段（含文本列），供前端展示字段说明。"""
    import server.routes as routes
    from tools.modeling import TABLE_NAME

    fake_metadata = {
        "tables": [
            {
                "table_name": TABLE_NAME,
                "columns": [
                    {"name": "defect_rate", "type": "DOUBLE", "description": "缺陷率"},
                    {"name": "machine_id", "type": "VARCHAR(50)", "description": "设备编码"},
                ],
            }
        ]
    }

    class FakeService:
        def metadata(self):
            return fake_metadata

    monkeypatch.setattr(routes, "get_service", lambda: FakeService())

    data = routes.modeling_features(role=None)["data"]
    names = {field["name"] for field in data["fields"]}
    assert names == {"defect_rate", "machine_id"}


def test_modeling_features_handles_missing_table(monkeypatch):
    """建模表不存在时返回空列表，而不是抛异常。"""
    import server.routes as routes

    class FakeService:
        def metadata(self):
            return {"tables": [{"table_name": "some_other_table", "columns": []}]}

    monkeypatch.setattr(routes, "get_service", lambda: FakeService())

    data = routes.modeling_features(role="numeric")["data"]
    assert data["fields"] == []
    assert data["total"] == 0
    assert data["default_features"] == {"anomaly": [], "regression": []}
