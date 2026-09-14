"""业务知识模型。

定义分析主题、业务对象、业务规则、指标口径，以及它们到表/字段的语义映射。
实际表/字段是否可用，由 knowledge_service 根据 metadata_json 动态解析。

数据模型是**单表宽表模型（路线 B）**：服务层只有 ``fact_production_record`` 一张表，
维度编码（产线 / 设备 / 产品 / 批次 / 班次）都是该表内的列，因此业务对象的
``default_table`` 指向同一张表，``key_field`` 指向对应编码列。详见 docs/DATA_MODEL.md。
"""

ANALYSIS_THEMES = [
    {
        "code": "production_analysis",
        "name": "生产分析",
        "description": "围绕产量、节拍、效率、生产记录等进行分析。",
        "objects": ["生产产线", "设备", "产品", "批次"],
        "indicators": ["产量", "设备利用率", "停机时长"],
        "related_tables": ["fact_production_record"],
    },
    {
        "code": "quality_analysis",
        "name": "质量分析",
        "description": "围绕缺陷率、良率、质量得分等进行分析。",
        "objects": ["产品", "批次", "生产产线"],
        "indicators": ["缺陷率", "良率", "质量得分"],
        "related_tables": ["fact_production_record"],
    },
    {
        "code": "equipment_analysis",
        "name": "设备分析",
        "description": "围绕设备停机、故障、振动、温度等进行分析。",
        "objects": ["设备", "生产产线"],
        "indicators": ["停机时长", "故障次数"],
        "related_tables": ["fact_production_record"],
    },
    {
        # 预留主题：当前数据底座没有库存表，related_tables 为空，
        # Prompt 里会明确标注「未接入该主题的数据表」，避免模型凭空生成查询。
        "code": "inventory_analysis",
        "name": "库存分析",
        "description": "预留库存分析主题，后续接入库存表后完善。",
        "objects": ["产品", "仓库"],
        "indicators": [],
        "related_tables": [],
    },
]

BUSINESS_OBJECTS = [
    {
        "name": "生产产线",
        "aliases": ["产线", "生产线", "line"],
        "default_table": "fact_production_record",
        "key_field": "production_line",
        "description": "生产组织单位，例如 Line_A。",
    },
    {
        "name": "设备",
        "aliases": ["机器", "machine"],
        "default_table": "fact_production_record",
        "key_field": "machine_id",
        "description": "执行生产活动的设备，例如 M01。",
    },
    {
        "name": "产品",
        "aliases": ["产品类型", "product"],
        "default_table": "fact_production_record",
        "key_field": "product_type",
        "description": "生产的产品类型，例如 Product_A。",
    },
    {
        "name": "批次",
        "aliases": ["生产批次", "batch"],
        "default_table": "fact_production_record",
        "key_field": "batch_id",
        "description": "生产批次编码，例如 B0001。",
    },
    {
        "name": "班次",
        "aliases": ["shift"],
        "default_table": "fact_production_record",
        "key_field": "shift",
        "description": "生产班次，例如 Morning / Afternoon / Night。",
    },
]

BUSINESS_RULES = [
    {
        "name": "缺陷率",
        "aliases": ["不良率", "缺陷比例"],
        "description": "数值越高表示质量缺陷越严重。",
        "candidate_fields": ["defect_rate", "defective_rate", "defect_ratio", "bad_rate"],
        "prefer_table": "fact_production_record",
        "calculation": "统计平均值可用 AVG({field})，越高越差。",
    },
    {
        "name": "良率",
        "aliases": ["直通率", "良品率"],
        "description": "产品一次性通过生产/检验的比例，越高越好。",
        "candidate_fields": ["first_pass_yield", "yield_rate", "pass_rate", "fpy"],
        "prefer_table": "fact_production_record",
        "calculation": "统计平均值可用 AVG({field})，越高越好。",
    },
    {
        "name": "质量得分",
        "aliases": ["质量分"],
        "description": "综合质量水平，越高越好。",
        "candidate_fields": ["quality_score", "quality_index"],
        "prefer_table": "fact_production_record",
        "calculation": "可直接使用 {field}。",
    },
    {
        "name": "停机时长",
        "aliases": ["停机时间"],
        "description": "设备停机分钟数，越高停机越严重。",
        "candidate_fields": ["downtime_minutes", "downtime", "down_time"],
        "prefer_table": "fact_production_record",
        "calculation": "统计总停机时长可用 SUM({field})，平均可用 AVG({field})。",
    },
    {
        "name": "故障次数",
        "aliases": ["故障事件数", "异常次数"],
        "description": "设备或批次发生的故障次数。",
        "candidate_fields": ["fault_event_count", "fault_count", "alarm_count"],
        "prefer_table": "fact_production_record",
        "calculation": "统计总和可用 SUM({field})。",
    },
    {
        "name": "产量",
        "aliases": ["生产量", "产出量"],
        "description": "生产数量，越高产出越多。",
        "candidate_fields": ["production_volume", "output_quantity", "quantity"],
        "prefer_table": "fact_production_record",
        "calculation": "统计趋势可用 SUM({field})，按产线/班次/产品分组。",
    },
    {
        "name": "设备利用率",
        "aliases": ["设备使用率"],
        "description": "设备利用程度。",
        "candidate_fields": ["machine_utilization", "utilization"],
        "prefer_table": "fact_production_record",
        "calculation": "统计平均值可用 AVG({field})。",
    },
]
