"""异常检测与机器学习建模工具。

统一建模模块：按算法名分派到对应的训练与评估逻辑。

已实现（对应题目「建模能力」的要求）：
- ``isolation_forest`` 异常检测
- ``linear_regression`` 线性回归
- ``decision_tree``     决策树（按目标列自动判定分类/回归）
- ``random_forest``     随机森林（按目标列自动判定分类/回归）
- ``logistic_regression`` 逻辑回归（二分类，可指定阈值派生标签）
- ``kmeans``            聚类（自动选 k，含轮廓系数评估）

依赖：pandas、scikit-learn、SQLAlchemy
"""

from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest, RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    calinski_harabasz_score,
    f1_score,
    precision_score,
    r2_score,
    recall_score,
    root_mean_squared_error,
    silhouette_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

from tools.database import get_engine

TABLE_NAME = "fact_production_record"

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

#: 支持建模任务的全部算法名（供前端下拉与网关校验使用）
SUPPORTED_ALGORITHMS = (
    "isolation_forest",
    "linear_regression",
    "decision_tree",
    "random_forest",
    "logistic_regression",
    "kmeans",
)

#: 目标列取值唯一数不超过该阈值时，判定为分类任务，否则为回归任务
CLASSIFICATION_MAX_UNIQUE = 15

#: 分类任务要求每个类别至少有这么多样本
MIN_SAMPLES_PER_CLASS = 5



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


# ---------------------------------------------------------------------------
# 共享准备逻辑
# ---------------------------------------------------------------------------
def _context_from(full_df: pd.DataFrame, index: Any) -> pd.DataFrame:
    """从已加载的完整数据帧中取回设备/产线/班次等上下文字段。

    训练时只用了目标列与特征列，但业务上需要知道「这条预测属于哪台设备、
    哪条产线、哪个班次」才能定位问题。

    刻意**复用已加载的数据帧**而不是二次读库：重新读库会重新抽样，
    行索引可能与训练集错位，导致上下文张冠李戴。取不到时返回空帧，
    调用方会自行降级（不阻断建模）。
    """
    if full_df is None or full_df.empty:
        return pd.DataFrame()
    available = [col for col in CONTEXT_COLUMNS if col in full_df.columns]
    if not available:
        return pd.DataFrame()
    return full_df.loc[full_df.index.intersection(index), available]


def _prepare_supervised(
    target: str,
    features: list[str] | None,
    limit: int | None,
    test_size: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series, list[str], pd.DataFrame, pd.DataFrame]:
    """加载数据、校验目标与特征、切分训练/测试集。

    返回 ``(X_train, X_test, y_train, y_test, features, data, full_df)``。
    ``full_df`` 是**未裁剪列**的原始行（带设备/产线等上下文字段），
    供调用方按索引回填上下文，避免二次读库导致索引错位。
    """
    df = _convert_numeric(_load_data(limit))

    if target not in df.columns:
        raise ValueError(f"目标列不存在: {target}")

    if features is None:
        features = [
            col
            for col in DEFAULT_REGRESSION_FEATURES
            if col in df.columns and col != target
        ]
    if not features:
        raise ValueError("未指定任何特征列，且默认特征集为空")

    # 目标列不能同时作为特征，否则会信息泄漏，指标虚高到 1.0
    if target in features:
        features = [col for col in features if col != target]
        if not features:
            raise ValueError(f"特征列在排除目标列 {target} 后为空")

    missing = [col for col in features if col not in df.columns]
    if missing:
        raise ValueError(f"特征列不存在: {missing}")

    data = df[[target] + features].dropna()
    if len(data) < 30:
        raise ValueError("有效数据太少，无法训练模型（至少需要 30 条有效样本）")

    X = data[features]
    y = data[target]
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=42
    )
    return X_train, X_test, y_train, y_test, features, data, df


def _detect_task_type(series: pd.Series, target: str) -> str:
    """判定目标是分类还是回归任务。

    规则：去重后的取值个数 <= ``CLASSIFICATION_MAX_UNIQUE`` 视为分类。
    例如 ``fault_event_count`` 取值 0/1/2/3（少量离散值）会被判为分类，
    而 ``defect_rate`` 这类连续量会判为回归。
    """
    unique = series.dropna().unique()
    if len(unique) <= 1:
        raise ValueError(f"目标列 {target} 只有一个取值，无法训练模型")
    if len(unique) <= CLASSIFICATION_MAX_UNIQUE:
        return "classification"
    return "regression"


def _tree_feature_importance(features: list[str], importances: np.ndarray) -> dict[str, float]:
    """特征重要性，按降序返回（决策树/随机森林的输出解释核心）。"""
    pairs = sorted(
        zip(features, importances), key=lambda item: abs(float(item[1])), reverse=True
    )
    return {name: round(float(value), 4) for name, value in pairs}


def _classification_metrics(y_test: pd.Series, y_pred: np.ndarray) -> dict[str, Any]:
    """分类任务的评估指标。"""
    return {
        "accuracy": round(float(accuracy_score(y_test, y_pred)), 4),
        # macro：类别不均衡时比 weighted 更能暴露短板
        "precision_macro": round(float(precision_score(y_test, y_pred, average="macro", zero_division=0)), 4),
        "recall_macro": round(float(recall_score(y_test, y_pred, average="macro", zero_division=0)), 4),
        "f1_macro": round(float(f1_score(y_test, y_pred, average="macro", zero_division=0)), 4),
    }


def _tree_result(
    *,
    algorithm: str,
    model: Any,
    task_type: str,
    target: str,
    features: list[str],
    X_train: pd.DataFrame,
    X_test: pd.DataFrame,
    y_train: pd.Series,
    y_test: pd.Series,
    context: pd.DataFrame,
    max_depth: int | None,
    n_estimators: int | None,
) -> dict[str, Any]:
    """组装决策树 / 随机森林的统一返回结构。"""
    y_pred = model.predict(X_test)

    result: dict[str, Any] = {
        "algorithm": algorithm,
        "task_type": "分类建模" if task_type == "classification" else "回归预测建模",
        "model_task": task_type,
        "target": target,
        "feature_columns": features,
        "train_size": int(len(X_train)),
        "test_size": int(len(X_test)),
        "params": {
            "max_depth": max_depth,
            "n_estimators": n_estimators,
            "random_state": 42,
        },
        "feature_importance": _tree_feature_importance(features, model.feature_importances_),
    }

    if task_type == "classification":
        result.update(_classification_metrics(y_test, y_pred))
        result["classes"] = [str(item) for item in model.classes_]
        result["class_distribution"] = {
            str(label): int(count) for label, count in y_train.value_counts().items()
        }
    else:
        result["r2_score"] = round(float(r2_score(y_test, y_pred)), 4)
        result["rmse"] = round(float(root_mean_squared_error(y_test, y_pred)), 4)

    # 预测样例：带上设备/产线等上下文，便于业务定位
    sample = pd.DataFrame(
        {
            "actual": y_test.head(10).values,
            "predict": y_pred[:10],
        },
        index=y_test.head(10).index,
    )
    available_context = [col for col in CONTEXT_COLUMNS if col in context.columns]
    if available_context:
        sample = pd.concat([context.loc[sample.index, available_context], sample], axis=1)
    result["sample_predictions"] = sample.to_dict(orient="records")

    # 特征重要性排序后给出可读解释，避免前端自行解读
    top = list(result["feature_importance"].items())[:5]
    result["importance_summary"] = "、".join(
        f"{name}({value:.4f})" for name, value in top
    )
    return result


# ---------------------------------------------------------------------------
# 决策树
# ---------------------------------------------------------------------------
def run_decision_tree(
    target: str = "defect_rate",
    features: list[str] | None = None,
    limit: int | None = 10_000,
    test_size: float = 0.2,
    max_depth: int | None = 5,
    task_type: str | None = None,
) -> dict[str, Any]:
    """决策树建模（按目标列自动判定分类或回归）。

    参数:
        target: 目标列
        features: 特征列，默认用 DEFAULT_REGRESSION_FEATURES
        max_depth: 树最大深度，默认 5（限制深度便于解释，也避免过拟合）
        task_type: 强制指定 ``classification`` / ``regression``；不传则自动判定
    """
    X_train, X_test, y_train, y_test, features, data, full_df = _prepare_supervised(
        target, features, limit, test_size
    )

    resolved = task_type or _detect_task_type(y_train, target)
    if resolved == "classification":
        model: Any = DecisionTreeClassifier(max_depth=max_depth, random_state=42)
    else:
        model = DecisionTreeRegressor(max_depth=max_depth, random_state=42)
    model.fit(X_train, y_train)

    context = _context_from(full_df, data.index)
    return _tree_result(
        algorithm="DecisionTree",
        model=model,
        task_type=resolved,
        target=target,
        features=features,
        X_train=X_train,
        X_test=X_test,
        y_train=y_train,
        y_test=y_test,
        context=context,
        max_depth=max_depth,
        n_estimators=None,
    )


# ---------------------------------------------------------------------------
# 随机森林
# ---------------------------------------------------------------------------
def run_random_forest(
    target: str = "defect_rate",
    features: list[str] | None = None,
    limit: int | None = 10_000,
    test_size: float = 0.2,
    n_estimators: int = 100,
    max_depth: int | None = None,
    task_type: str | None = None,
) -> dict[str, Any]:
    """随机森林建模（按目标列自动判定分类或回归）。

    参数:
        n_estimators: 树的数量，默认 100
        max_depth: 树最大深度，默认不限制（由随机森林自行生长）
    """
    X_train, X_test, y_train, y_test, features, data, full_df = _prepare_supervised(
        target, features, limit, test_size
    )

    resolved = task_type or _detect_task_type(y_train, target)
    # n_jobs=1：与项目其他模型保持一致，避免演示环境线程争用
    if resolved == "classification":
        model: Any = RandomForestClassifier(
            n_estimators=n_estimators, max_depth=max_depth, random_state=42, n_jobs=1
        )
    else:
        model = RandomForestRegressor(
            n_estimators=n_estimators, max_depth=max_depth, random_state=42, n_jobs=1
        )
    model.fit(X_train, y_train)

    context = _context_from(full_df, data.index)
    return _tree_result(
        algorithm="RandomForest",
        model=model,
        task_type=resolved,
        target=target,
        features=features,
        X_train=X_train,
        X_test=X_test,
        y_train=y_train,
        y_test=y_test,
        context=context,
        max_depth=max_depth,
        n_estimators=n_estimators,
    )


# ---------------------------------------------------------------------------
# 逻辑回归（二分类）
# ---------------------------------------------------------------------------
def run_logistic_regression(
    target: str = "defect_rate",
    features: list[str] | None = None,
    limit: int | None = 10_000,
    test_size: float = 0.2,
    threshold: float | None = None,
    task_type: str | None = None,
) -> dict[str, Any]:
    """逻辑回归二分类建模。

    目标列处理：
    - 若目标列本身恰好有两个取值 → 直接作为标签；
    - 否则（连续量或取值多于两个）用 ``threshold`` 二分：大于阈值为 1，否则 0。
      不传 ``threshold`` 时用中位数，保证两类样本均衡。

    参数:
        threshold: 二分阈值，仅当目标列不是天然二分类时生效
    """
    X_train, X_test, y_train, y_test, features, data, full_df = _prepare_supervised(
        target, features, limit, test_size
    )

    unique = np.sort(y_train.dropna().unique())
    derived = False
    threshold_used: float | None = None

    if len(unique) == 2:
        # 天然二分类：映射成 0/1，保证逻辑回归可直接使用
        mapping = {unique[0]: 0, unique[1]: 1}
        y_train_bin = y_train.map(mapping).astype(int)
        y_test_bin = y_test.map(mapping).astype(int)
        classes = [str(unique[0]), str(unique[1])]
    else:
        derived = True
        threshold_used = float(threshold) if threshold is not None else float(y_train.median())
        y_train_bin = (y_train.astype(float) > threshold_used).astype(int)
        y_test_bin = (y_test.astype(float) > threshold_used).astype(int)
        classes = [f"<= {threshold_used:.4f}", f"> {threshold_used:.4f}"]

    if y_train_bin.nunique() < 2:
        raise ValueError(
            f"目标列 {target} 在阈值 {threshold_used} 下只有一个类别，无法做二分类"
        )
    smallest = int(y_train_bin.value_counts().min())
    if smallest < MIN_SAMPLES_PER_CLASS:
        raise ValueError(
            f"少数类样本过少（{smallest} 条），逻辑回归结果不可靠，请调整阈值或更换目标列"
        )

    # 逻辑回归对特征尺度敏感，必须标准化
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    model = LogisticRegression(max_iter=1000, random_state=42)
    model.fit(X_train_scaled, y_train_bin)

    y_pred = model.predict(X_test_scaled)
    y_proba = model.predict_proba(X_test_scaled)[:, 1]

    # 标准化后的系数可直接按绝对值比较重要性
    coefficients = sorted(
        zip(features, model.coef_[0]), key=lambda item: abs(float(item[1])), reverse=True
    )

    sample = pd.DataFrame(
        {
            "actual": y_test_bin.head(10).values,
            "predict": y_pred[:10],
            "probability": np.round(y_proba[:10], 4),
        },
        index=y_test_bin.head(10).index,
    )
    context = _context_from(full_df, sample.index)
    available_context = [col for col in CONTEXT_COLUMNS if col in context.columns]
    if available_context:
        sample = pd.concat([context.loc[sample.index, available_context], sample], axis=1)

    return {
        "algorithm": "LogisticRegression",
        "task_type": "二分类建模",
        "model_task": "classification",
        "target": target,
        "label_definition": classes,
        "label_derived": derived,
        "threshold": threshold_used,
        "feature_columns": features,
        "train_size": int(len(X_train)),
        "test_size": int(len(X_test)),
        "params": {"max_iter": 1000, "random_state": 42, "class_weight": None},
        "class_distribution": {
            str(label): int(count) for label, count in y_train_bin.value_counts().items()
        },
        "intercept": round(float(model.intercept_[0]), 4),
        "coefficients": {name: round(float(value), 4) for name, value in coefficients},
        "scaled": True,
        "sample_predictions": sample.to_dict(orient="records"),
        **_classification_metrics(y_test_bin, y_pred),
    }


# ---------------------------------------------------------------------------
# KMeans 聚类
# ---------------------------------------------------------------------------
def run_kmeans(
    features: list[str] | None = None,
    limit: int | None = 10_000,
    n_clusters: int | None = None,
    max_k: int = 6,
) -> dict[str, Any]:
    """KMeans 聚类。

    不传 ``n_clusters`` 时自动在 2..``max_k`` 之间按轮廓系数（silhouette）
    选最优 k，并把每个候选 k 的评分一并返回，便于前端展示「如何选 k」。

    参数:
        n_clusters: 指定聚类数；不传则自动选择
        max_k: 自动选择时的最大 k
    """
    df = _convert_numeric(_load_data(limit))

    if features is None:
        features = [col for col in DEFAULT_ANOMALY_FEATURES if col in df.columns]
    if not features:
        raise ValueError("未指定任何聚类特征，且默认特征集为空")

    missing = [col for col in features if col not in df.columns]
    if missing:
        raise ValueError(f"特征列不存在: {missing}")

    data = df[features + [col for col in CONTEXT_COLUMNS if col in df.columns]].dropna(
        subset=features
    )
    if len(data) < 30:
        raise ValueError("有效数据太少，无法执行聚类（至少需要 30 条有效样本）")

    X = data[features]
    # KMeans 基于欧氏距离，必须先标准化，否则量纲大的列会主导距离
    X_scaled = StandardScaler().fit_transform(X)

    candidates: list[dict[str, Any]] = []
    if n_clusters is None:
        evaluated = range(2, max(3, min(max_k, 6) + 1))
    else:
        evaluated = [int(n_clusters)]

    best: Any = None
    best_score = -1.0
    best_k = None
    for k in evaluated:
        if k >= len(X_scaled):
            continue
        candidate = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = candidate.fit_predict(X_scaled)
        score = float(silhouette_score(X_scaled, labels))
        candidates.append(
            {
                "k": int(k),
                "silhouette": round(score, 4),
                "inertia": round(float(candidate.inertia_), 2),
                "calinski_harabasz": round(float(calinski_harabasz_score(X_scaled, labels)), 2),
            }
        )
        if score > best_score:
            best_score, best, best_k = score, candidate, k

    if best is None:
        raise ValueError("无法完成聚类，请检查数据量或降低聚类数")

    labels = best.labels_
    result_df = data.copy()
    result_df["cluster"] = labels

    # 每个簇的质心（还原到原始量纲，业务上可读）
    centers_scaled = best.cluster_centers_
    centers_original = StandardScaler().fit(X).inverse_transform(centers_scaled)

    clusters = []
    for index in range(best_k):
        members = result_df[result_df["cluster"] == index]
        entry: dict[str, Any] = {
            "cluster": index,
            "size": int(len(members)),
            "ratio": round(float(len(members) / len(result_df)), 4),
            "centroid": {
                name: round(float(value), 4)
                for name, value in zip(features, centers_original[index])
            },
        }
        # 上下文字段给出该簇最常见的取值，让业务能描述「这是一个什么簇」
        for column in CONTEXT_COLUMNS:
            if column in members.columns and len(members):
                top = members[column].value_counts()
                if len(top):
                    entry[f"dominant_{column}"] = str(top.index[0])
        clusters.append(entry)

    # 特征均值对比：帮助解释簇间差异
    overall_mean = X.mean()
    cluster_profile = []
    for index in range(best_k):
        members = result_df[result_df["cluster"] == index]
        profile: dict[str, Any] = {"cluster": index}
        for name in features:
            mean_value = float(members[name].mean())
            profile[name] = round(mean_value, 4)
        cluster_profile.append(profile)

    return {
        "algorithm": "KMeans",
        "task_type": "聚类分析",
        "model_task": "clustering",
        "feature_columns": features,
        "total_records": int(len(result_df)),
        "n_clusters": int(best_k),
        "auto_selected": n_clusters is None,
        "silhouette": round(best_score, 4),
        "candidates": candidates,
        "clusters": clusters,
        "cluster_profile": cluster_profile,
        "overall_mean": {name: round(float(value), 4) for name, value in overall_mean.items()},
        "sample_records": result_df.head(20).to_dict(orient="records"),
    }


# ---------------------------------------------------------------------------
# 统一分派
# ---------------------------------------------------------------------------
def run_model(algorithm: str, request: dict[str, Any]) -> dict[str, Any]:
    """按算法名分派到对应的建模函数。

    这是适配层与网关的统一入口，避免每新增一个模型就要在多层各加一个接口。
    未知算法直接抛 ``ValueError``，由上层转成可读错误。
    """
    name = (algorithm or "").strip().lower()
    if name not in SUPPORTED_ALGORITHMS:
        raise ValueError(
            f"不支持的算法 {algorithm!r}，可选：{', '.join(SUPPORTED_ALGORITHMS)}"
        )

    if name == "isolation_forest":
        return run_anomaly_detection(
            features=request.get("features"),
            contamination=request.get("contamination", 0.05),
            limit=request.get("limit", 10_000),
        )
    if name == "linear_regression":
        return run_linear_regression(
            target=request.get("target", "defect_rate"),
            features=request.get("features"),
            limit=request.get("limit", 10_000),
            test_size=request.get("test_size", 0.2),
        )
    if name == "decision_tree":
        return run_decision_tree(
            target=request.get("target", "defect_rate"),
            features=request.get("features"),
            limit=request.get("limit", 10_000),
            test_size=request.get("test_size", 0.2),
            max_depth=request.get("max_depth", 5),
            task_type=request.get("task_type"),
        )
    if name == "random_forest":
        return run_random_forest(
            target=request.get("target", "defect_rate"),
            features=request.get("features"),
            limit=request.get("limit", 10_000),
            test_size=request.get("test_size", 0.2),
            n_estimators=request.get("n_estimators", 100),
            max_depth=request.get("max_depth"),
            task_type=request.get("task_type"),
        )
    if name == "logistic_regression":
        return run_logistic_regression(
            target=request.get("target", "defect_rate"),
            features=request.get("features"),
            limit=request.get("limit", 10_000),
            test_size=request.get("test_size", 0.2),
            threshold=request.get("threshold"),
            task_type=request.get("task_type"),
        )
    return run_kmeans(
        features=request.get("features"),
        limit=request.get("limit", 10_000),
        n_clusters=request.get("n_clusters"),
        max_k=request.get("max_k", 6),
    )

