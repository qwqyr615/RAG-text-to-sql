"""异常检测与简单机器学习建模工具。

当前基于本地 MySQL 表 intelligent_production_iiot 实现：
- Isolation Forest 异常检测
- LinearRegression 简单回归预测

依赖：pandas、scikit-learn、SQLAlchemy
"""

from typing import Any

import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score, root_mean_squared_error
from sklearn.model_selection import train_test_split

from tools.database import get_engine

TABLE_NAME = "intelligent_production_iiot"

DEFAULT_ANOMALY_FEATURES = [
    "defect_rate",
    "quality_score",
    "first_pass_yield",
    "downtime_minutes",
    "fault_event_count",
    "production_volume",
    "machine_utilization",
    "power_consumption",
    "motor_temperature",
    "vibration",
]

DEFAULT_REGRESSION_FEATURES = [
    "quality_score",
    "first_pass_yield",
    "downtime_minutes",
    "fault_event_count",
    "machine_utilization",
    "motor_temperature",
    "vibration",
    "power_consumption",
    "cycle_time",
]

CONTEXT_COLUMNS = ["record_id", "machine_id", "production_line", "batch_id", "shift", "product_type"]


def _load_data(limit: int | None = 10_000) -> pd.DataFrame:
    """从 MySQL 读取数据；默认最多读取 10000 行，避免演示时卡顿。"""
    sql = f"SELECT * FROM {TABLE_NAME}"
    df = pd.read_sql(sql, get_engine())
    if limit and len(df) > limit:
        df = df.sample(n=limit, random_state=42)
    return df


def _convert_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """把看起来是数字的文本列转成数值类型。"""
    numeric_like = [
        "first_pass_yield",
        "fault_event_count",
        "downtime_minutes",
        "maintenance_frequency",
        "production_cost_per_unit",
        "resource_efficiency",
        "production_efficiency",
        "energy_saving_pct",
        "downtime_reduction_pct",
        "cost_reduction_pct",
        "benefit_score",
    ]
    for col in numeric_like:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def run_anomaly_detection(
    features: list[str] | None = None,
    contamination: float = 0.05,
    limit: int | None = 10_000,
) -> dict[str, Any]:
    """使用 Isolation Forest 执行异常检测。

    参数:
        features: 参与异常检测的特征列，默认使用 DEFAULT_ANOMALY_FEATURES
        contamination: 预期异常比例
        limit: 最多读取的数据量
    """
    df = _load_data(limit)
    df = _convert_numeric(df)

    if features is None:
        features = [col for col in DEFAULT_ANOMALY_FEATURES if col in df.columns]

    missing = [col for col in features if col not in df.columns]
    if missing:
        raise ValueError(f"特征列不存在: {missing}")

    sample = df.copy()
    X = sample[features].dropna()
    if len(X) < 10:
        raise ValueError("有效数据太少，无法执行异常检测")

    model = IsolationForest(
        n_estimators=100,
        contamination=contamination,
        random_state=42,
        n_jobs=1,
    )
    model.fit(X)
    pred = model.predict(X)
    scores = model.decision_function(X)

    anomaly_mask = pred == -1
    anomaly_data = X[anomaly_mask].copy()
    anomaly_data["anomaly_score"] = scores[anomaly_mask]

    context = sample.loc[anomaly_data.index, CONTEXT_COLUMNS]
    result_rows = pd.concat([context, anomaly_data], axis=1).head(20)

    return {
        "task_type": "异常检测",
        "algorithm": "IsolationForest",
        "total_records": int(len(X)),
        "anomaly_count": int(anomaly_mask.sum()),
        "anomaly_ratio": round(float(anomaly_mask.mean()), 4),
        "feature_columns": features,
        "records": result_rows.to_dict(orient="records"),
    }


def run_linear_regression(
    target: str = "defect_rate",
    features: list[str] | None = None,
    limit: int | None = 10_000,
    test_size: float = 0.2,
) -> dict[str, Any]:
    """使用 LinearRegression 训练简单回归模型。

    参数:
        target: 目标列，例如 defect_rate / quality_score / downtime_minutes
        features: 特征列，默认使用 DEFAULT_REGRESSION_FEATURES
        limit: 最多读取的数据量
    """
    df = _load_data(limit)
    df = _convert_numeric(df)

    if target not in df.columns:
        raise ValueError(f"目标列不存在: {target}")

    if features is None:
        features = [col for col in DEFAULT_REGRESSION_FEATURES if col in df.columns and col != target]

    missing = [col for col in features if col not in df.columns]
    if missing:
        raise ValueError(f"特征列不存在: {missing}")

    data = df[[target] + features].dropna()
    if len(data) < 30:
        raise ValueError("有效数据太少，无法训练回归模型")

    X = data[features]
    y = data[target].astype(float)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=42
    )

    model = LinearRegression()
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    r2 = r2_score(y_test, y_pred)
    rmse = float(root_mean_squared_error(y_test, y_pred))

    sample_compare = pd.DataFrame({"actual": y_test.head(10).values, "predict": y_pred[:10]})

    return {
        "task_type": "回归预测建模",
        "algorithm": "LinearRegression",
        "target": target,
        "feature_columns": features,
        "train_size": int(len(X_train)),
        "test_size": int(len(X_test)),
        "r2_score": round(float(r2), 4),
        "rmse": round(rmse, 4),
        "coefficients": {
            col: round(float(coef), 4) for col, coef in zip(features, model.coef_)
        },
        "sample_predictions": sample_compare.to_dict(orient="records"),
    }
