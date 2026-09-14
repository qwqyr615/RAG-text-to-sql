"""建模模块测试：六个算法 + 参数校验 + 分派逻辑。

全部使用临时 SQLite 库与合成数据，**不依赖 MySQL、不打大模型**，
保证能在 CI / 受限环境下快速跑完。

合成数据的构造刻意保留了真实业务关系（缺陷率随质量得分上升而下降、
故障次数随停机时长上升），这样「模型能学到东西」本身就是一条有效断言——
若某个算法实现写错（例如特征与目标张冠李戴），R² / 准确率会明显塌掉。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine

import tools.modeling as modeling
from core.config import BASE_DIR
from tools.modeling import (
    SUPPORTED_ALGORITHMS,
    run_decision_tree,
    run_kmeans,
    run_logistic_regression,
    run_model,
    run_random_forest,
)

#: 合成数据的行数：足够训练，又不至于让测试变慢
ROW_COUNT = 300


@pytest.fixture()
def synthetic_table(monkeypatch):
    """建一张合成事实表，并把建模模块的取数指向它。

    返回 ``(engine, table_name)``。测试全程不碰真实 MySQL。

    刻意不沿用 conftest 的 ``sqlite_path`` fixture：SQLite 文件在引擎未 dispose 时
    句柄仍被占用，Windows 上会导致其 teardown 删除文件失败（WinError 32）。
    这里自己管理路径并在 finally 中先 dispose 再删除。
    """
    directory = BASE_DIR / "data"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"test_modeling_{uuid.uuid4().hex[:8]}.db"

    engine = create_engine(f"sqlite:///{path.as_posix()}")
    rng = np.random.default_rng(42)

    quality = rng.normal(88, 4, ROW_COUNT).round(3)
    yield_rate = rng.normal(93, 2, ROW_COUNT).round(3)
    downtime = rng.gamma(2.0, 8.0, ROW_COUNT).round(3)
    # 刻意构造线性可学的目标：质量分与良率拉低缺陷率，停机时长抬高缺陷率
    defect = (8.0 - 0.05 * quality - 0.02 * yield_rate + 0.02 * downtime).round(4)
    fault = rng.poisson(np.clip(downtime / 8.0, 0.2, None))
    frame = pd.DataFrame(
        {
            "record_id": np.arange(1, ROW_COUNT + 1),
            "machine_id": [f"M{index % 20:02d}" for index in range(ROW_COUNT)],
            "production_line": [f"Line_{chr(65 + index % 4)}" for index in range(ROW_COUNT)],
            "batch_id": [f"B{index:04d}" for index in range(ROW_COUNT)],
            "shift": [["Morning", "Afternoon", "Night"][index % 3] for index in range(ROW_COUNT)],
            "product_type": [f"Product_{chr(65 + index % 3)}" for index in range(ROW_COUNT)],
            "defect_rate": defect,
            "quality_score": quality,
            "first_pass_yield": yield_rate,
            "downtime_minutes": downtime,
            "fault_event_count": fault,
            "production_volume": rng.integers(80, 150, ROW_COUNT),
            "machine_utilization": rng.normal(70, 6, ROW_COUNT).round(3),
            "power_consumption": rng.normal(20, 3, ROW_COUNT).round(3),
            "motor_temperature": rng.normal(55, 4, ROW_COUNT).round(3),
            "vibration": rng.normal(1.3, 0.3, ROW_COUNT).round(4),
            "cycle_time": rng.normal(30, 4, ROW_COUNT).round(3),
        }
    )
    table_name = "fact_production_record"
    frame.to_sql(table_name, engine, index=False)

    # 建模模块内部用 get_engine() 取数，这里改指向临时库
    monkeypatch.setattr(modeling, "get_engine", lambda: engine)
    monkeypatch.setattr(modeling, "TABLE_NAME", table_name)

    try:
        yield engine, table_name
    finally:
        engine.dispose()
        for suffix in ("", "-journal", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                try:
                    candidate.unlink()
                except OSError:
                    # 受限环境下删不掉不应让测试失败（文件已在 .gitignore 内）
                    pass


# ---------------------------------------------------------------------------
# 基础：数据加载与分派
# ---------------------------------------------------------------------------
def test_load_data_reads_synthetic_table(synthetic_table):
    frame = modeling._load_data(limit=None)
    assert len(frame) == ROW_COUNT
    assert "defect_rate" in frame.columns


def test_supported_algorithms_covers_required_models():
    """题目点名的建模能力都必须可用。"""
    for name in (
        "linear_regression",
        "decision_tree",
        "random_forest",
        "logistic_regression",
        "kmeans",
        "isolation_forest",
    ):
        assert name in SUPPORTED_ALGORITHMS


def test_run_model_rejects_unknown_algorithm(synthetic_table):
    with pytest.raises(ValueError, match="不支持的算法"):
        run_model("xgboost", {})


def test_run_model_dispatches_by_name(synthetic_table):
    """同一入口按算法名分派到不同实现。"""
    tree = run_model("decision_tree", {"target": "defect_rate", "limit": 200})
    forest = run_model("random_forest", {"target": "defect_rate", "n_estimators": 10, "limit": 200})
    km = run_model("kmeans", {"n_clusters": 3, "limit": 200})

    assert tree["algorithm"] == "DecisionTree"
    assert forest["algorithm"] == "RandomForest"
    assert km["algorithm"] == "KMeans"


# ---------------------------------------------------------------------------
# 任务类型自动判定
# ---------------------------------------------------------------------------
def test_detect_task_type_classification_for_few_unique_values(synthetic_table):
    frame = modeling._convert_numeric(modeling._load_data(limit=None))
    # fault_event_count 是小整数，取值很少 → 分类
    assert modeling._detect_task_type(frame["fault_event_count"], "fault_event_count") == "classification"
    # defect_rate 是连续量 → 回归
    assert modeling._detect_task_type(frame["defect_rate"], "defect_rate") == "regression"


def test_detect_task_type_rejects_constant_target(synthetic_table):
    frame = modeling._convert_numeric(modeling._load_data(limit=None))
    constant = pd.Series([1.0] * len(frame))
    with pytest.raises(ValueError, match="只有一个取值"):
        modeling._detect_task_type(constant, "constant_col")


# ---------------------------------------------------------------------------
# 决策树 / 随机森林
# ---------------------------------------------------------------------------
def test_decision_tree_regression_learns_and_explains(synthetic_table):
    result = run_decision_tree(target="defect_rate", max_depth=5, limit=300)

    assert result["algorithm"] == "DecisionTree"
    assert result["model_task"] == "regression"
    assert result["task_type"] == "回归预测建模"
    # 合成数据是线性可学的，树至少应该比「取均值」好很多
    assert result["r2_score"] > 0.5
    assert result["rmse"] > 0

    # 特征重要性必须覆盖全部特征且归一化到 1
    importance = result["feature_importance"]
    assert set(importance) == set(result["feature_columns"])
    assert abs(sum(importance.values()) - 1.0) < 0.01
    # 重要性按降序返回
    values = list(importance.values())
    assert values == sorted(values, reverse=True)
    assert result["importance_summary"]

    # 样例里必须带设备上下文（业务定位用）
    sample = result["sample_predictions"][0]
    for column in ("actual", "predict", "machine_id", "production_line", "shift"):
        assert column in sample


def test_decision_tree_classification_metrics(synthetic_table):
    result = run_decision_tree(target="fault_event_count", max_depth=4, limit=300)

    assert result["model_task"] == "classification"
    assert result["task_type"] == "分类建模"
    for key in ("accuracy", "precision_macro", "recall_macro", "f1_macro"):
        assert 0.0 <= result[key] <= 1.0
    assert result["classes"]
    assert result["class_distribution"]


def test_random_forest_regression(synthetic_table):
    result = run_random_forest(target="defect_rate", n_estimators=20, limit=300)

    assert result["algorithm"] == "RandomForest"
    assert result["r2_score"] > 0.5
    assert result["params"]["n_estimators"] == 20
    assert set(result["feature_importance"]) == set(result["feature_columns"])


def test_random_forest_classification(synthetic_table):
    result = run_random_forest(target="fault_event_count", n_estimators=20, limit=300)
    assert result["model_task"] == "classification"
    assert 0.0 <= result["accuracy"] <= 1.0


def test_task_type_can_be_forced(synthetic_table):
    """强制 task_type 时应覆盖自动判定。"""
    result = run_decision_tree(
        target="fault_event_count", max_depth=4, limit=300, task_type="regression"
    )
    assert result["model_task"] == "regression"
    assert "r2_score" in result
    assert "accuracy" not in result


# ---------------------------------------------------------------------------
# 逻辑回归
# ---------------------------------------------------------------------------
def test_logistic_regression_derives_binary_label_from_median(synthetic_table):
    result = run_logistic_regression(target="defect_rate", limit=300)

    assert result["algorithm"] == "LogisticRegression"
    assert result["model_task"] == "classification"
    # defect_rate 是连续量 → 必须走「按阈值派生标签」这条路
    assert result["label_derived"] is True
    assert result["threshold"] is not None
    assert len(result["label_definition"]) == 2
    # 合成数据里缺陷率与质量分强相关 → 应该能学到
    assert result["accuracy"] > 0.7
    # 标准化标记必须为真（逻辑回归对尺度敏感）
    assert result["scaled"] is True
    # 概率字段要出现在样例里
    assert "probability" in result["sample_predictions"][0]


def test_logistic_regression_honours_explicit_threshold(synthetic_table):
    """显式阈值必须生效（回归测试：阈值曾因跨层命名不一致被静默丢弃）。

    阈值取自数据的中位数，保证两个类别样本都足够；关键是断言
    ``result["threshold"]`` 等于传入值，而不是被悄悄换成默认值。
    """
    frame = modeling._convert_numeric(modeling._load_data(limit=None))
    explicit = round(float(frame["defect_rate"].median()), 4)

    result = run_logistic_regression(target="defect_rate", threshold=explicit, limit=300)

    assert result["threshold"] == explicit
    assert f"{explicit:.4f}" in result["label_definition"][1]
    # 阈值真的改变了标签划分：两个类别都应存在
    assert len(result["class_distribution"]) == 2


def test_logistic_regression_rejects_degenerate_threshold(synthetic_table):
    """阈值把样本切得只剩一个类别时必须明确报错，而不是给出无意义结果。"""
    with pytest.raises(ValueError, match="只有一个类别|少数类样本过少"):
        run_logistic_regression(target="defect_rate", threshold=999.0, limit=300)


def test_logistic_regression_coefficients_sorted_by_abs(synthetic_table):
    result = run_logistic_regression(target="defect_rate", limit=300)
    values = [abs(value) for value in result["coefficients"].values()]
    assert values == sorted(values, reverse=True)
    assert set(result["coefficients"]) == set(result["feature_columns"])
    assert "intercept" in result


# ---------------------------------------------------------------------------
# KMeans
# ---------------------------------------------------------------------------
def test_kmeans_with_explicit_cluster_count(synthetic_table):
    result = run_kmeans(n_clusters=3, limit=300)

    assert result["algorithm"] == "KMeans"
    assert result["model_task"] == "clustering"
    assert result["n_clusters"] == 3
    assert result["auto_selected"] is False
    assert -1.0 <= result["silhouette"] <= 1.0
    assert len(result["clusters"]) == 3
    # 各簇规模之和必须等于总样本数
    assert sum(cluster["size"] for cluster in result["clusters"]) == result["total_records"]
    # 每个簇都要有质心与主导属性
    for cluster in result["clusters"]:
        assert set(cluster["centroid"]) == set(result["feature_columns"])
        assert any(key.startswith("dominant_") for key in cluster)


def test_kmeans_auto_selects_k_and_reports_candidates(synthetic_table):
    result = run_kmeans(limit=300)

    assert result["auto_selected"] is True
    assert result["n_clusters"] >= 2
    # 候选评分必须被记录，前端要展示「怎么选 k」
    assert len(result["candidates"]) >= 2
    for candidate in result["candidates"]:
        assert candidate["k"] >= 2
        assert "silhouette" in candidate and "inertia" in candidate
    # 选中的 k 必须是候选里轮廓系数最高的
    best = max(result["candidates"], key=lambda item: item["silhouette"])
    assert result["n_clusters"] == best["k"]


def test_kmeans_cluster_profile_matches_feature_count(synthetic_table):
    result = run_kmeans(n_clusters=2, limit=300)
    assert len(result["cluster_profile"]) == 2
    for profile in result["cluster_profile"]:
        for name in result["feature_columns"]:
            assert name in profile
    assert set(result["overall_mean"]) == set(result["feature_columns"])


# ---------------------------------------------------------------------------
# 参数与数据校验
# ---------------------------------------------------------------------------
def test_missing_target_column_raises(synthetic_table):
    with pytest.raises(ValueError, match="目标列不存在"):
        run_decision_tree(target="no_such_column", limit=300)


def test_missing_feature_column_raises(synthetic_table):
    with pytest.raises(ValueError, match="特征列不存在"):
        run_decision_tree(target="defect_rate", features=["no_such_column"], limit=300)


def test_target_is_excluded_from_features(synthetic_table):
    """目标列混进特征会造成信息泄漏、指标虚高，必须被剔除。"""
    result = run_decision_tree(
        target="defect_rate",
        features=["defect_rate", "quality_score", "first_pass_yield"],
        limit=300,
    )
    assert "defect_rate" not in result["feature_columns"]
    assert result["r2_score"] < 0.999


def test_too_few_rows_raises(synthetic_table, monkeypatch):
    """有效样本不足时必须明确报错，而不是训练出一个不可信的模型。"""
    original = modeling._load_data
    monkeypatch.setattr(modeling, "_load_data", lambda limit=None: original(limit).head(5))
    with pytest.raises(ValueError, match="有效数据太少"):
        run_decision_tree(target="defect_rate", limit=None)


def test_context_helper_handles_missing_table(synthetic_table, monkeypatch):
    """取不到上下文字段时应降级为空帧，而不是抛异常。"""
    monkeypatch.setattr(modeling, "CONTEXT_COLUMNS", ["not_a_real_column"])
    frame = modeling._load_data(limit=None)
    context = modeling._context_from(frame, frame.index[:5])
    assert context.empty


def test_all_algorithms_return_json_serializable_payload(synthetic_table):
    """FastAPI 会序列化返回值，任何不可序列化对象都会在接口层炸掉。"""
    import json

    cases = [
        ("isolation_forest", {}),
        ("linear_regression", {"target": "defect_rate"}),
        ("decision_tree", {"target": "defect_rate", "max_depth": 4}),
        ("random_forest", {"target": "defect_rate", "n_estimators": 10}),
        ("logistic_regression", {"target": "defect_rate"}),
        ("kmeans", {"n_clusters": 3}),
    ]
    for algorithm, params in cases:
        result = run_model(algorithm, {**params, "limit": 200})
        json.dumps(result, ensure_ascii=False)
