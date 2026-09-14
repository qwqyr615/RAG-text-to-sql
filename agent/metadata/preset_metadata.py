"""标准数据底座的遗留说明（单表宽表模型，路线 B）。

**本模块不再持有业务表白名单。** 原先这里的 ``PRESET_BUSINESS_TABLES =
["fact_production_record"]`` 是硬编码的 Agent 可见表白名单，换一张客户表就必须改代码。
现在表范围由**发现模式**决定（``metadata/inventory.py``：扫描全库 + 排除规则），
字段口径由**映射**决定（``mapping.yaml`` -> ``metadata/mapping/``）。

保留下来的两样东西，只有在「没有配置映射」时才会被用到：

- ``TABLE_DESCRIPTIONS`` / ``COLUMN_DESCRIPTIONS``：人工维护的说明，作为
  数据库列 ``COMMENT`` 读不到时的兜底；
- ``RELATIONSHIPS``：内置表间关系（单表模型下为空）。

字段说明的完整优先级见 ``metadata/metadata_service.py``：
**mapping.yaml > 列 COMMENT > 本模块兜底**。

关于原始层
----------
``intelligent_production_iiot`` 是 CSV 导入的原样数据，字段类型未经治理（11 个数值
指标以 TEXT 存储，排序按字典序）。它必须对 Agent 不可见，否则模型可能选到脏类型
那张表而静默算错 —— 实测证据见 ``docs/DATA_MODEL.md``。排除规则写在
``mapping.yaml`` 的 ``discovery.exclude`` 里，不再写死在代码中。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "RELATIONSHIPS",
    "TABLE_DESCRIPTIONS",
    "COLUMN_DESCRIPTIONS",
    "get_column_description",
]

#: 标准服务层表名。仅用于「未配置映射」时的默认行为与健康检查，
#: **不是** Agent 可见表白名单 —— 那由发现模式决定。
STANDARD_SERVICE_TABLE = "fact_production_record"

#: 表说明（兜底，运行时优先读映射的 description，其次读表级 COMMENT）
TABLE_DESCRIPTIONS: dict[str, str] = {
    "fact_production_record": (
        "生产记录事实宽表，单表模型的服务层，一行代表一次生产运行，"
        "含维度编码与全部质量设备产量指标。"
    ),
    "intelligent_production_iiot": (
        "CSV 导入的原始层数据，字段类型未经治理，仅供服务层装载使用，Agent 不直接查询。"
    ),
}

#: 字段说明（兜底，运行时优先读映射口径，其次读列级 COMMENT）
COLUMN_DESCRIPTIONS: dict[str, str] = {
    "fact_production_record.record_id": "生产记录主键，一行代表一次生产运行。",
    "fact_production_record.machine_id": "设备编码，例如 M01。",
    "fact_production_record.production_line": "生产线编码，例如 Line_A。",
    "fact_production_record.batch_id": "生产批次编码，例如 B0001。",
    "fact_production_record.shift": "生产班次，Morning / Afternoon / Night。",
    "fact_production_record.product_type": "产品类型编码，例如 Product_A。",
    "fact_production_record.operation_mode": "生产模式，例如 Balanced_Production。",
    "fact_production_record.defect_rate": "缺陷率，数值越高质量越差。",
    "fact_production_record.quality_score": "质量得分，数值越高质量越好。",
    "fact_production_record.first_pass_yield": "良率或直通率，数值越高越好。",
    "fact_production_record.downtime_minutes": "停机时长，单位分钟。",
    "fact_production_record.fault_event_count": "故障事件次数。",
    "fact_production_record.maintenance_frequency": "维护频次。",
    "fact_production_record.production_volume": "产量。",
    "fact_production_record.machine_utilization": "设备利用率百分比。",
    "fact_production_record.resource_utilization": "资源利用率百分比。",
    "fact_production_record.cycle_time": "生产节拍，单位秒。",
    "fact_production_record.throughput_rate": "吞吐率。",
    "fact_production_record.power_consumption": "能耗。",
    "fact_production_record.energy_per_unit": "单位能耗。",
    "fact_production_record.production_cost_per_unit": "单位生产成本。",
    "fact_production_record.motor_temperature": "电机温度，单位摄氏度。",
    "fact_production_record.vibration": "振动值。",
    "fact_production_record.resource_efficiency": "资源效率百分比。",
    "fact_production_record.production_efficiency": "生产效率百分比。",
    "fact_production_record.energy_saving_pct": "节能比例百分比。",
    "fact_production_record.downtime_reduction_pct": "停机下降比例百分比。",
    "fact_production_record.cost_reduction_pct": "成本下降比例百分比。",
    "fact_production_record.benefit_score": "综合效益得分。",
}

#: 表间关系：单表模型下为空。多表客户库的关系写在 mapping.yaml 的 relationships 段。
RELATIONSHIPS: list[dict[str, Any]] = []


def get_column_description(table_name: str, column_name: str) -> str:
    """返回字段说明，缺失时返回空字符串。"""
    return COLUMN_DESCRIPTIONS.get(f"{table_name}.{column_name}", "")
