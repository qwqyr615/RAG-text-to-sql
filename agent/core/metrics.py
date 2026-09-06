"""业务指标口径定义。

用于统一描述企业数据底座中的核心业务指标，帮助大模型生成更准确的 SQL。
当前数据表：intelligent_production_iiot
"""

BUSINESS_METRICS = [
    {
        "name": "缺陷率",
        "aliases": ["不良率", "defect rate", "defect_rate"],
        "field": "defect_rate",
        "description": "反映生产质量缺陷水平，数值越高表示质量越差。",
        "calculation": "可直接使用 defect_rate 字段；统计平均值可用 AVG(defect_rate)，按组统计则配合 GROUP BY。",
    },
    {
        "name": "良率 / 直通率",
        "aliases": ["良率", "直通率", "first pass yield", "first_pass_yield"],
        "field": "first_pass_yield",
        "description": "反映产品一次性通过生产/检验的比例，数值越高表示良率越好。",
        "calculation": "可直接使用 first_pass_yield 字段；统计平均值可用 AVG(first_pass_yield)。",
    },
    {
        "name": "质量得分",
        "aliases": ["质量分", "quality score", "quality_score"],
        "field": "quality_score",
        "description": "反映综合质量水平，数值越高表示质量越好。",
        "calculation": "可直接使用 quality_score 字段，例如 AVG(quality_score) 或 MIN(quality_score)。",
    },
    {
        "name": "停机时长",
        "aliases": ["停机时间", "downtime", "downtime_minutes"],
        "field": "downtime_minutes",
        "description": "反映设备停机时间，单位是分钟；数值越高表示停机越严重。",
        "calculation": "可直接使用 downtime_minutes 字段；统计总停机时长可用 SUM(downtime_minutes)，平均可用 AVG(downtime_minutes)。",
    },
    {
        "name": "故障次数",
        "aliases": ["故障事件数", "fault count", "fault_event_count"],
        "field": "fault_event_count",
        "description": "反映设备或批次发生的故障/异常事件次数。",
        "calculation": "可直接使用 fault_event_count 字段，统计总和可用 SUM(fault_event_count)。",
    },
    {
        "name": "产量",
        "aliases": ["生产量", "production volume", "production_volume"],
        "field": "production_volume",
        "description": "反映生产数量，数值越高表示产出越多。",
        "calculation": "可直接使用 production_volume 字段，统计趋势可使用 SUM(production_volume) 并按时间/产线/班次分组。",
    },
]


def get_metrics_text() -> str:
    """生成供 LLM 使用的业务指标口径文本。"""
    lines = []
    for metric in BUSINESS_METRICS:
        lines.append(f"- {metric['name']}")
        lines.append(f"  字段/别名: {metric['field']} / {'、'.join(metric['aliases'])}")
        lines.append(f"  含义: {metric['description']}")
        lines.append(f"  计算口径: {metric['calculation']}")
    return "\n".join(lines)
