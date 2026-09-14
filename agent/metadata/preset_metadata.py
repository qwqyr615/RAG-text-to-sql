"""预置数据底座元数据说明（路线 B：单表宽表模型）。

- ``PRESET_BUSINESS_TABLES``：Agent 可见的业务表白名单。单表模型下只有服务层
  ``fact_production_record``；原始层 ``intelligent_production_iiot`` 不暴露给 Agent。
- ``TABLE_DESCRIPTIONS`` / ``COLUMN_DESCRIPTIONS``：人工维护的说明，仅作**兜底**——
  运行时优先使用 DDL 里的 ``COMMENT``（见 metadata/metadata_service.py）。
  两份说明若冲突，以 ``sql/02_create_fact_production_record.sql`` 为准。
- ``RELATIONSHIPS``：单表模型下为空。原先这里声明了 4 条 ``fact -> dim_*`` 关系，
  但那 4 张派生维表已删除，且这类关系在宽表里本就是冗余的（实测 JOIN 前后行数与取值
  完全相同）。若将来切回星型模型（路线 A），再在这里补充真正有意义的关系。

字段类型、字段列表、样例值由 metadata_service 从数据库动态读取。
详见 docs/DATA_MODEL.md
"""

# 预置业务表范围：单表模型，只暴露服务层
PRESET_BUSINESS_TABLES = [
    "fact_production_record",
]

# 表说明（兜底，运行时优先读表级 COMMENT）
TABLE_DESCRIPTIONS = {
    "fact_production_record": (
        "生产记录事实宽表，单表模型的服务层，一行代表一次生产运行，"
        "含维度编码与全部质量设备产量指标。"
    ),
    "intelligent_production_iiot": (
        "CSV 导入的原始层数据，字段类型未经治理，仅供服务层装载使用，Agent 不直接查询。"
    ),
}

# 字段说明（兜底，运行时优先读列级 COMMENT）
COLUMN_DESCRIPTIONS = {
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

# 表间关系：单表模型下为空
RELATIONSHIPS: list[dict[str, str]] = []


def get_column_description(table_name: str, column_name: str) -> str:
    """返回字段说明，缺失时返回空字符串。"""
    return COLUMN_DESCRIPTIONS.get(f"{table_name}.{column_name}", "")
