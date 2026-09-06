"""预置数据底座元数据说明。

这里保存人工整理的表说明、字段说明、表间关系；
实际字段类型、字段列表、样例数据由 metadata_service 从 MySQL 动态读取。
"""

# 预置业务表范围
PRESET_BUSINESS_TABLES = [
    "dim_line",
    "dim_machine",
    "dim_product",
    "dim_batch",
    "fact_production_record",
]

# 表说明
TABLE_DESCRIPTIONS = {
    "dim_line": "生产线维度表，记录企业生产线编码。",
    "dim_machine": "设备维度表，记录设备编码及其所属生产线。",
    "dim_product": "产品维度表，记录产品类型/产品编码。",
    "dim_batch": "批次维度表，记录生产批次编码。",
    "fact_production_record": "生产记录事实表，记录每次生产运行的环境、质量、设备、产量等指标。",
}

# 字段说明
COLUMN_DESCRIPTIONS = {
    "dim_line.line_id": "生产线编码，例如 Line_A。",
    "dim_line.description": "生产线说明。",
    "dim_machine.machine_id": "设备编码，例如 M01。",
    "dim_machine.line_id": "设备所属生产线编码，关联 dim_line.line_id。",
    "dim_product.product_type": "产品类型编码，例如 Product_A。",
    "dim_product.product_name": "产品名称。",
    "dim_batch.batch_id": "生产批次编码，例如 B0001。",
    "fact_production_record.record_id": "生产记录主键。",
    "fact_production_record.machine_id": "设备编码，关联 dim_machine.machine_id。",
    "fact_production_record.production_line": "生产线编码，关联 dim_line.line_id。",
    "fact_production_record.batch_id": "生产批次编码，关联 dim_batch.batch_id。",
    "fact_production_record.shift": "班次，例如 Morning / Afternoon / Night。",
    "fact_production_record.product_type": "产品类型，关联 dim_product.product_type。",
    "fact_production_record.defect_rate": "缺陷率，数值越高质量越差。",
    "fact_production_record.quality_score": "质量得分，数值越高质量越好。",
    "fact_production_record.first_pass_yield": "良率/直通率，数值越高越好。",
    "fact_production_record.downtime_minutes": "停机时长，单位分钟。",
    "fact_production_record.fault_event_count": "故障事件次数。",
    "fact_production_record.production_volume": "产量。",
    "fact_production_record.machine_utilization": "设备利用率。",
    "fact_production_record.power_consumption": "能耗。",
    "fact_production_record.motor_temperature": "电机温度。",
    "fact_production_record.vibration": "振动值。",
}

# 表间关系
RELATIONSHIPS = [
    {
        "source_table": "dim_machine",
        "source_column": "line_id",
        "target_table": "dim_line",
        "target_column": "line_id",
        "relation_type": "many-to-one",
        "description": "多台设备属于同一条生产线。",
    },
    {
        "source_table": "fact_production_record",
        "source_column": "machine_id",
        "target_table": "dim_machine",
        "target_column": "machine_id",
        "relation_type": "many-to-one",
        "description": "多条生产记录对应同一台设备。",
    },
    {
        "source_table": "fact_production_record",
        "source_column": "production_line",
        "target_table": "dim_line",
        "target_column": "line_id",
        "relation_type": "many-to-one",
        "description": "多条生产记录来自同一条生产线。",
    },
    {
        "source_table": "fact_production_record",
        "source_column": "product_type",
        "target_table": "dim_product",
        "target_column": "product_type",
        "relation_type": "many-to-one",
        "description": "多条生产记录对应同一产品类型。",
    },
    {
        "source_table": "fact_production_record",
        "source_column": "batch_id",
        "target_table": "dim_batch",
        "target_column": "batch_id",
        "relation_type": "many-to-one",
        "description": "一条生产记录属于一个生产批次。",
    },
]


def get_column_description(table_name: str, column_name: str) -> str:
    """返回字段说明，缺失时返回空字符串。"""
    return COLUMN_DESCRIPTIONS.get(f"{table_name}.{column_name}", "")
