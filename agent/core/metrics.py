"""业务指标口径定义。

口径的**唯一来源**是 :mod:`metadata.standard_fields` 的 ``STANDARD_METRICS``，
本模块是它在 Prompt 层的适配器：

- :func:`resolve_metrics` —— 无映射时的启发式匹配（按候选字段名撞实际列名）；
- :func:`resolve_metrics_from_field_map` —— 有映射时按人审过的字段口径解析，
  并带上单位换算表达式。

两条路径返回同构的 dict，``MetricsProvider`` 因此不需要关心数据源是标准模型
还是客户映射。
"""

from typing import Iterable

from metadata.standard_fields import STANDARD_METRICS

#: 业务指标口径。历史上是手写列表，现在从标准字段词典派生，避免两处维护漂移。
BUSINESS_METRICS = [
    {
        "name": metric.name,
        "aliases": list(metric.aliases),
        "candidate_fields": [metric.standard_field],
        "description": metric.description,
        "calculation": metric.calculation,
    }
    for metric in STANDARD_METRICS
]


def _normalize(value: str) -> str:
    """统一小写并去掉常见分隔符，便于字段名模糊匹配。"""
    return (
        value.strip()
        .lower()
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
    )


def resolve_metrics(columns: Iterable[str]) -> list[dict]:
    """根据当前数据源的实际字段解析出可用的业务指标。

    参数:
        columns: 当前数据库所有可用字段名

    返回:
        只包含能匹配到实际字段的业务指标；找不到字段的指标会被过滤。
    """
    column_map = {_normalize(col): col for col in columns}
    resolved = []

    for metric in BUSINESS_METRICS:
        for candidate in metric["candidate_fields"]:
            actual_field = column_map.get(_normalize(candidate))
            if actual_field is not None:
                resolved.append({**metric, "field": actual_field})
                break

    return resolved


def resolve_metrics_from_field_map(
    field_map: Iterable[dict] | None,
) -> list[dict]:
    """从 ``mapping.yaml`` 编译出的字段口径解析业务指标。

    与 :func:`resolve_metrics` 的区别：后者是「按标准候选字段名去撞客户列名」的
    启发式匹配，本函数用的是**人审过的映射**，因此还能带上单位换算表达式。

    返回的每条指标都带 ``expression``（客户侧真实写法，可能是 ``def_rate * 100``）
    与 ``needs_conversion`` 标记，供 Prompt 直接展示「要算这个指标就写这个表达式」。
    """
    if not field_map:
        return []

    by_standard: dict[str, dict] = {}
    for item in field_map:
        standard = str(item.get("standard_field") or "")
        if standard:
            by_standard.setdefault(standard, item)

    resolved: list[dict] = []
    for metric in BUSINESS_METRICS:
        for candidate in metric["candidate_fields"]:
            item = by_standard.get(candidate)
            if item is None:
                continue
            expression = str(item.get("expression") or item.get("column") or "")
            resolved.append(
                {
                    **metric,
                    "field": str(item.get("column") or expression),
                    "expression": expression,
                    "needs_conversion": bool(item.get("needs_conversion")),
                }
            )
            break
    return resolved


def get_metrics_text(columns: Iterable[str] | None = None) -> str:
    """生成供 LLM 使用的业务指标口径文本。

    参数:
        columns: 当前数据源实际字段名列表；传入后会动态匹配字段。
                 如果不传，则按候选字段中的第一个字段输出（用于演示默认数据源）。
    """
    if columns is not None:
        metrics = resolve_metrics(columns)
        if not metrics:
            return "当前数据源未匹配到预置业务指标字段，请严格根据实际表结构进行分析。"
    else:
        metrics = BUSINESS_METRICS

    lines = []
    for metric in metrics:
        field = metric.get("field") or metric["candidate_fields"][0]
        aliases = "、".join(metric["aliases"])

        lines.append(f"- {metric['name']}")
        lines.append(f"  业务别名: {aliases}")
        lines.append(f"  当前数据源字段: {field}")
        lines.append(f"  含义: {metric['description']}")
        lines.append(f"  计算口径: {metric['calculation'].format(field=field)}")

    return "\n".join(lines)

