"""业务指标口径定义。"""

from typing import Iterable

BUSINESS_METRICS = [
    {
        "name": "缺陷率",
        "aliases": ["不良率", "缺陷比例", "defect rate", "defect ratio"],
        "candidate_fields": ["defect_rate", "defective_rate", "defect_ratio", "bad_rate", "defect_percent"],
        "description": "反映生产质量缺陷水平，数值越高表示质量越差。",
        "calculation": "统计平均值可用 AVG({field})，按产线/产品/班次/设备分组时配合 GROUP BY 使用。",
    },
    {
        "name": "良率 / 直通率",
        "aliases": ["良率", "直通率", "first pass yield", "fpy"],
        "candidate_fields": ["first_pass_yield", "yield_rate", "pass_rate", "fpy"],
        "description": "反映产品一次性通过生产/检验的比例，数值越高表示良率越好。",
        "calculation": "统计平均值可用 AVG({field})，数值越高越好。",
    },
    {
        "name": "质量得分",
        "aliases": ["质量分", "quality score"],
        "candidate_fields": ["quality_score", "quality_index", "quality_grade_score"],
        "description": "反映综合质量水平，数值越高表示质量越好。",
        "calculation": "可直接使用 {field}，例如 AVG({field}) 或 MIN({field})。",
    },
    {
        "name": "停机时长",
        "aliases": ["停机时间", "设备停机", "downtime"],
        "candidate_fields": ["downtime_minutes", "downtime", "down_time", "equipment_downtime", "stop_minutes"],
        "description": "反映设备停机时间，单位通常是分钟；数值越高表示停机越严重。",
        "calculation": "统计总停机时长可用 SUM({field})，平均可用 AVG({field})。",
    },
    {
        "name": "故障次数",
        "aliases": ["故障事件数", "异常次数", "fault count"],
        "candidate_fields": ["fault_event_count", "fault_count", "alarm_count", "failure_count", "error_count"],
        "description": "反映设备或批次发生的故障/异常事件次数。",
        "calculation": "统计总和可用 SUM({field})。",
    },
    {
        "name": "产量",
        "aliases": ["生产量", "产出量", "production volume"],
        "candidate_fields": ["production_volume", "output_quantity", "quantity", "product_qty", "yield_qty"],
        "description": "反映生产数量，数值越高表示产出越多。",
        "calculation": "统计趋势可用 SUM({field}) 并按时间/产线/班次分组。",
    },
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

