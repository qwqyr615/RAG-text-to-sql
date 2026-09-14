"""标准字段词典：数据底座的「标准字段 ↔ 客户列」映射里，左边那一半的定义。

为什么需要这份词典
------------------
系统要面向**未知的客户数据库**：客户表里可能是 `def_rate`、`不良率`、`NG_RATE`，
也可能一列注释都没有（真实 MES 视图常见如此）。如果只靠表名字段名去猜，模型的
正确率会随客户命名习惯剧烈波动。

所以先固定一套**标准字段**（canonical field），再让每个客户接入时提供一份
``mapping.yaml`` 把「标准字段 ↔ 客户列」对齐。词典是稳定的，客户列是可变的：

    标准字段（本文件）  +  mapping.yaml（客户）  ->  可执行的字段口径

三个层次
--------
1. :data:`STANDARD_FIELDS` —— 45 个标准字段，每个带中文名、单位、业务别名与
   客户列名候选（``column_candidates``）。别名/候选用于**自动草拟**映射，
   人审时改的是 mapping.yaml，不是这份词典。
2. :data:`STANDARD_METRICS` —— 业务指标口径（缺陷率、良率……），指向标准字段。
   原先散在 ``core/metrics.py`` 与 ``knowledge/knowledge_base.py`` 的两份口径
   在这里统一，避免同一指标两处维护、两处漂移。
3. :func:`match_columns` —— 「客户列 -> 标准字段」的反向匹配，供草稿生成与
   审核 CLI 提示用。匹配必须**全局唯一**，有歧义就报出来让人决定。

单位（unit）为什么是必需的
--------------------------
真实客户库里同一个业务量常有两种存法：缺陷率有的存 ``3.9``（百分数），有的存
``0.039``（比例）。本次评测用的 ``mes_prod_log`` 就是后者。标准字段固定一个
**规范单位**，客户列用 ``scale`` 声明换算系数，生成的 SQL 里带上换算（如
``def_rate * 100``），结果才与标准口径可比。没有这一层，「平均缺陷率」会静默
差 100 倍 —— 这正是本项目在 ``docs/DATA_MODEL.md`` 里记录的同类静默错误。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

__all__ = [
    "AMBIGUOUS",
    "MATCHED",
    "UNMAPPED",
    "ColumnMatch",
    "StandardField",
    "StandardMetric",
    "STANDARD_FIELDS",
    "STANDARD_METRICS",
    "STANDARD_FIELD_GROUPS",
    "canonical_names",
    "field_by_name",
    "normalize_identifier",
    "match_columns",
]


# ----------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class StandardField:
    """一个标准字段的定义。"""

    name: str
    """规范字段名（英文标识符），也是标准口径 SQL 里使用的列名。"""

    label: str
    """中文名，渲染进 Prompt 给模型看，也是业务人员提问时用的词。"""

    unit: str
    """规范单位。客户列若单位不同，必须在 mapping 里声明 scale 换算。"""

    data_kind: str
    """``measure`` 度量 / ``dimension`` 维度编码 / ``key`` 主键 / ``time`` 时间。"""

    column_candidates: tuple[str, ...] = ()
    """客户库里该字段可能叫什么（含本次两个数据源）。用于自动草拟映射与反向匹配。"""

    aliases: tuple[str, ...] = ()
    """业务别名，供指标口径与检索使用。"""

    group: str = "其他"
    """业务分组：主键与维度 / 环境与设备传感器 / 产量效率能耗 / 质量与设备健康 /
    改善类指标 / 生产模式。用于 Prompt 分组展示。"""

    @property
    def is_dimension(self) -> bool:
        return self.data_kind in {"dimension", "key", "time"}

    def candidate_set(self) -> set[str]:
        """归一化后的候选列名集合（含规范名、中文名与业务别名）。"""
        raw = (self.name, self.label, *self.column_candidates, *self.aliases)
        return {normalize_identifier(item) for item in raw if item}


@dataclass(frozen=True)
class StandardMetric:
    """一个业务指标口径，指向标准字段。"""

    name: str
    aliases: tuple[str, ...]
    standard_field: str
    description: str
    calculation: str
    """计算口径模板，``{field}`` 会被替换成**客户侧表达式**（可能含单位换算）。"""

    def render_calculation(self, field_expression: str) -> str:
        return self.calculation.format(field=field_expression)


@dataclass
class ColumnMatch:
    """一条「客户列 -> 标准字段」的匹配结论。"""

    table: str
    column: str
    status: str
    """``matched`` 唯一命中 / ``ambiguous`` 多个标准字段都命中 / ``unmapped`` 未命中。"""

    standard_field: str | None = None
    candidates: list[str] = field(default_factory=list)
    """歧义时的候选标准字段，供审核 CLI 让用户挑。"""


MATCHED = "matched"
AMBIGUOUS = "ambiguous"
UNMAPPED = "unmapped"


# ----------------------------------------------------------------------
# 归一化
# ----------------------------------------------------------------------
def normalize_identifier(value: str) -> str:
    """归一化标识符，便于跨命名风格比较。

    ``defect_rate`` / ``defectRate`` / ``Defect-Rate`` / ``defect rate`` /
    ``DEFECTRATE`` 全部归一为 ``defectrate``。中文名与别名同样参与归一化，
    因此 ``不良率`` 这类中文列名也能反向命中标准字段。
    """
    text = str(value or "").strip().lower()
    for char in ("_", "-", " ", ".", "（", "）", "(", ")", "/", "\\"):
        text = text.replace(char, "")
    return text


# ----------------------------------------------------------------------
# 标准字段词典
# ----------------------------------------------------------------------
STANDARD_FIELD_GROUPS: tuple[str, ...] = (
    "主键与维度",
    "环境与设备传感器",
    "产量效率能耗",
    "质量与设备健康",
    "改善类指标",
    "生产模式",
)

#: 45 个标准字段。顺序即展示顺序，与 SQL 服务层 DDL 的列顺序保持一致。
STANDARD_FIELDS: tuple[StandardField, ...] = (
    # ---- 主键与维度 ----
    StandardField(
        name="record_id",
        label="生产记录主键",
        unit="—",
        data_kind="key",
        column_candidates=("record_id", "rec_no", "id", "record_no", "prod_record_id"),
        aliases=("记录编号", "记录ID"),
        group="主键与维度",
    ),
    StandardField(
        name="machine_id",
        label="设备编码",
        unit="—",
        data_kind="dimension",
        column_candidates=("machine_id", "mach_no", "machine_no", "equip_id", "device_id", "asset_no"),
        aliases=("设备编号", "机器编号", "机台"),
        group="主键与维度",
    ),
    StandardField(
        name="production_line",
        label="生产线编码",
        unit="—",
        data_kind="dimension",
        column_candidates=("production_line", "line_cd", "line_code", "line_id", "prod_line"),
        aliases=("产线", "生产线", "线体"),
        group="主键与维度",
    ),
    StandardField(
        name="batch_id",
        label="生产批次编码",
        unit="—",
        data_kind="dimension",
        column_candidates=("batch_id", "lot_no", "lot_id", "batch_no", "lot_number"),
        aliases=("批次", "批次号", "生产批号"),
        group="主键与维度",
    ),
    StandardField(
        name="shift",
        label="生产班次",
        unit="—",
        data_kind="dimension",
        column_candidates=("shift", "sft", "shift_code", "shift_name", "work_shift", "crew"),
        aliases=("班次", "班组", "班别"),
        group="主键与维度",
    ),
    StandardField(
        name="product_type",
        label="产品类型编码",
        unit="—",
        data_kind="dimension",
        column_candidates=("product_type", "prod_tp", "product_code", "item_no", "sku", "prod_type"),
        aliases=("产品", "产品类型", "料号"),
        group="主键与维度",
    ),
    # ---- 环境与设备传感器 ----
    StandardField(
        name="ambient_temperature",
        label="环境温度",
        unit="摄氏度",
        data_kind="measure",
        column_candidates=("ambient_temperature", "env_t", "ambient_temp", "room_temp"),
        aliases=("车间温度",),
        group="环境与设备传感器",
    ),
    StandardField(
        name="humidity",
        label="环境湿度",
        unit="百分比",
        data_kind="measure",
        column_candidates=("humidity", "rh", "relative_humidity", "humid"),
        aliases=("湿度",),
        group="环境与设备传感器",
    ),
    StandardField(
        name="air_pressure",
        label="气压",
        unit="—",
        data_kind="measure",
        column_candidates=("air_pressure", "atm_p", "atmospheric_pressure", "pressure_atm"),
        aliases=(),
        group="环境与设备传感器",
    ),
    StandardField(
        name="ambient_vibration",
        label="环境振动值",
        unit="—",
        data_kind="measure",
        column_candidates=("ambient_vibration", "env_vib", "ambient_vib", "floor_vibration"),
        aliases=(),
        group="环境与设备传感器",
    ),
    StandardField(
        name="motor_temperature",
        label="电机温度",
        unit="摄氏度",
        data_kind="measure",
        column_candidates=("motor_temperature", "mot_t", "motor_temp", "motor_t"),
        aliases=("马达温度",),
        group="环境与设备传感器",
    ),
    StandardField(
        name="spindle_speed",
        label="主轴转速",
        unit="转每分",
        data_kind="measure",
        column_candidates=("spindle_speed", "spd", "spindle_rpm", "rpm", "speed"),
        aliases=("转速",),
        group="环境与设备传感器",
    ),
    StandardField(
        name="motor_current",
        label="电机电流",
        unit="安培",
        data_kind="measure",
        column_candidates=("motor_current", "cur", "current", "motor_amp"),
        aliases=("电流",),
        group="环境与设备传感器",
    ),
    StandardField(
        name="torque",
        label="扭矩",
        unit="牛米",
        data_kind="measure",
        column_candidates=("torque", "trq", "torq"),
        aliases=(),
        group="环境与设备传感器",
    ),
    StandardField(
        name="vibration",
        label="振动值",
        unit="—",
        data_kind="measure",
        column_candidates=("vibration", "vib", "vibration_value"),
        aliases=("振动",),
        group="环境与设备传感器",
    ),
    StandardField(
        name="acoustic_level",
        label="噪声水平",
        unit="分贝",
        data_kind="measure",
        column_candidates=("acoustic_level", "noise", "noise_level", "sound_level", "db_level"),
        aliases=("噪音", "噪声"),
        group="环境与设备传感器",
    ),
    StandardField(
        name="bearing_temperature",
        label="轴承温度",
        unit="摄氏度",
        data_kind="measure",
        column_candidates=("bearing_temperature", "brg_t", "bearing_temp"),
        aliases=("轴温",),
        group="环境与设备传感器",
    ),
    StandardField(
        name="process_temperature",
        label="工艺温度",
        unit="摄氏度",
        data_kind="measure",
        column_candidates=("process_temperature", "proc_t", "process_temp"),
        aliases=("过程温度",),
        group="环境与设备传感器",
    ),
    StandardField(
        name="hydraulic_pressure",
        label="液压压力",
        unit="—",
        data_kind="measure",
        column_candidates=("hydraulic_pressure", "hyd_p", "hyd_pressure"),
        aliases=("液压",),
        group="环境与设备传感器",
    ),
    StandardField(
        name="flow_rate",
        label="流量",
        unit="—",
        data_kind="measure",
        column_candidates=("flow_rate", "flow", "flowrate"),
        aliases=(),
        group="环境与设备传感器",
    ),
    StandardField(
        name="coolant_temperature",
        label="冷却液温度",
        unit="摄氏度",
        data_kind="measure",
        column_candidates=("coolant_temperature", "cool_t", "coolant_temp"),
        aliases=("冷却温度",),
        group="环境与设备传感器",
    ),
    StandardField(
        name="material_feed_rate",
        label="进给速率",
        unit="—",
        data_kind="measure",
        column_candidates=("material_feed_rate", "feed_r", "feed_rate"),
        aliases=("进料速率",),
        group="环境与设备传感器",
    ),
    StandardField(
        name="feed_pressure",
        label="进给压力",
        unit="—",
        data_kind="measure",
        column_candidates=("feed_pressure", "feed_p", "feed_press"),
        aliases=(),
        group="环境与设备传感器",
    ),
    # ---- 产量效率能耗 ----
    StandardField(
        name="cycle_time",
        label="生产节拍",
        unit="秒",
        data_kind="measure",
        column_candidates=("cycle_time", "ct", "cycle", "tact_time"),
        aliases=("节拍", "单件工时"),
        group="产量效率能耗",
    ),
    StandardField(
        name="throughput_rate",
        label="吞吐率",
        unit="—",
        data_kind="measure",
        column_candidates=("throughput_rate", "tput", "throughput", "thruput"),
        aliases=("产能",),
        group="产量效率能耗",
    ),
    StandardField(
        name="production_volume",
        label="产量",
        unit="件",
        data_kind="measure",
        column_candidates=("production_volume", "out_qty", "output_quantity", "quantity", "qty", "output_qty"),
        aliases=("生产量", "产出量", "产出数量"),
        group="产量效率能耗",
    ),
    StandardField(
        name="machine_utilization",
        label="设备利用率",
        unit="百分比",
        data_kind="measure",
        # ``util`` 刻意同时出现在设备利用率与资源利用率的候选里：
        # 裸 ``util`` 列在两个字段之间**真有歧义**（客户可能指任何一个），
        # 因此必须让 match_columns 报 ambiguous 交人审，而不是按声明顺序猜一个。
        # 猜错的代价是静默算错一个业务指标 —— 比多问一句贵得多。
        column_candidates=(
            "machine_utilization", "util", "utilization", "oee",
            "machine_util", "equipment_utilization", "machine_usage_rate",
        ),
        aliases=("设备使用率", "利用率"),
        group="产量效率能耗",
    ),
    StandardField(
        name="resource_utilization",
        label="资源利用率",
        unit="百分比",
        data_kind="measure",
        column_candidates=(
            "resource_utilization", "res_util", "resource_util",
            "util", "utilization", "resource_usage_rate",
        ),
        aliases=("资源使用率",),
        group="产量效率能耗",
    ),
    StandardField(
        name="operator_load",
        label="人员负荷",
        unit="百分比",
        data_kind="measure",
        column_candidates=("operator_load", "op_load", "operator_util"),
        aliases=("人员负载",),
        group="产量效率能耗",
    ),
    StandardField(
        name="power_consumption",
        label="能耗",
        unit="千瓦时",
        data_kind="measure",
        column_candidates=("power_consumption", "kwh", "power", "energy_consumption", "electricity"),
        aliases=("耗电量", "用电量"),
        group="产量效率能耗",
    ),
    StandardField(
        name="energy_per_unit",
        label="单位能耗",
        unit="千瓦时每件",
        data_kind="measure",
        column_candidates=("energy_per_unit", "kwh_unit", "unit_energy", "energy_unit"),
        aliases=("单件能耗",),
        group="产量效率能耗",
    ),
    # ---- 质量与设备健康 ----
    StandardField(
        name="defect_rate",
        label="缺陷率",
        unit="百分比",
        data_kind="measure",
        column_candidates=(
            "defect_rate", "def_rate", "defective_rate", "defect_ratio",
            "bad_rate", "ng_rate", "defect_percent",
        ),
        aliases=("不良率", "缺陷比例", "NG率"),
        group="质量与设备健康",
    ),
    StandardField(
        name="quality_score",
        label="质量得分",
        unit="分",
        data_kind="measure",
        column_candidates=("quality_score", "q_score", "quality_index", "quality_grade_score"),
        aliases=("质量分",),
        group="质量与设备健康",
    ),
    StandardField(
        name="first_pass_yield",
        label="良率",
        unit="百分比",
        data_kind="measure",
        column_candidates=("first_pass_yield", "fpy", "yield_rate", "pass_rate", "first_yield"),
        aliases=("直通率", "良品率", "一次通过率"),
        group="质量与设备健康",
    ),
    StandardField(
        name="fault_event_count",
        label="故障事件次数",
        unit="次",
        data_kind="measure",
        column_candidates=("fault_event_count", "alarm_cnt", "fault_count", "alarm_count", "failure_count"),
        aliases=("故障次数", "报警次数", "异常次数"),
        group="质量与设备健康",
    ),
    StandardField(
        name="downtime_minutes",
        label="停机时长",
        unit="分钟",
        data_kind="measure",
        column_candidates=("downtime_minutes", "stop_min", "downtime", "down_time", "stop_minutes"),
        aliases=("停机时间", "设备停机"),
        group="质量与设备健康",
    ),
    StandardField(
        name="maintenance_frequency",
        label="维护频次",
        unit="次",
        data_kind="measure",
        column_candidates=("maintenance_frequency", "maint_cnt", "maintenance_count", "repair_count"),
        aliases=("维修次数", "保养次数"),
        group="质量与设备健康",
    ),
    StandardField(
        name="production_cost_per_unit",
        label="单位生产成本",
        unit="元每件",
        data_kind="measure",
        column_candidates=("production_cost_per_unit", "unit_cost", "cost_per_unit", "unit_prod_cost"),
        aliases=("单件成本",),
        group="质量与设备健康",
    ),
    # ---- 改善类指标 ----
    StandardField(
        name="resource_efficiency",
        label="资源效率",
        unit="百分比",
        data_kind="measure",
        column_candidates=("resource_efficiency", "res_eff", "resource_eff"),
        aliases=(),
        group="改善类指标",
    ),
    StandardField(
        name="production_efficiency",
        label="生产效率",
        unit="百分比",
        data_kind="measure",
        column_candidates=("production_efficiency", "prod_eff", "production_eff"),
        aliases=(),
        group="改善类指标",
    ),
    StandardField(
        name="energy_saving_pct",
        label="节能比例",
        unit="百分比",
        data_kind="measure",
        column_candidates=("energy_saving_pct", "es_pct", "energy_saving", "energy_saving_percent"),
        aliases=("节能率",),
        group="改善类指标",
    ),
    StandardField(
        name="downtime_reduction_pct",
        label="停机下降比例",
        unit="百分比",
        data_kind="measure",
        column_candidates=("downtime_reduction_pct", "dtr_pct", "downtime_reduction"),
        aliases=("停机下降率",),
        group="改善类指标",
    ),
    StandardField(
        name="cost_reduction_pct",
        label="成本下降比例",
        unit="百分比",
        data_kind="measure",
        column_candidates=("cost_reduction_pct", "cr_pct", "cost_reduction"),
        aliases=("成本下降率",),
        group="改善类指标",
    ),
    StandardField(
        name="benefit_score",
        label="综合效益得分",
        unit="分",
        data_kind="measure",
        column_candidates=("benefit_score", "benefit", "benefit_index"),
        aliases=("效益得分",),
        group="改善类指标",
    ),
    # ---- 生产模式 ----
    StandardField(
        name="operation_mode",
        label="生产模式",
        unit="—",
        data_kind="dimension",
        column_candidates=("operation_mode", "op_mode", "mode", "production_mode"),
        aliases=("运行模式",),
        group="生产模式",
    ),
)


# ----------------------------------------------------------------------
# 业务指标口径（统一此处，core/metrics.py 与 knowledge 均从这里派生）
# ----------------------------------------------------------------------
STANDARD_METRICS: tuple[StandardMetric, ...] = (
    StandardMetric(
        name="缺陷率",
        aliases=("不良率", "缺陷比例", "defect rate", "defect ratio", "NG率"),
        standard_field="defect_rate",
        description="反映生产质量缺陷水平，数值越高表示质量越差。",
        calculation="统计平均值可用 AVG({field})，按产线/产品/班次/设备分组时配合 GROUP BY 使用。",
    ),
    StandardMetric(
        name="良率 / 直通率",
        aliases=("良率", "直通率", "良品率", "first pass yield", "fpy"),
        standard_field="first_pass_yield",
        description="反映产品一次性通过生产/检验的比例，数值越高表示良率越好。",
        calculation="统计平均值可用 AVG({field})，数值越高越好。",
    ),
    StandardMetric(
        name="质量得分",
        aliases=("质量分", "quality score"),
        standard_field="quality_score",
        description="反映综合质量水平，数值越高表示质量越好。",
        calculation="可直接使用 {field}，例如 AVG({field}) 或 MIN({field})。",
    ),
    StandardMetric(
        name="停机时长",
        aliases=("停机时间", "设备停机", "downtime"),
        standard_field="downtime_minutes",
        description="反映设备停机时间，单位通常是分钟；数值越高表示停机越严重。",
        calculation="统计总停机时长可用 SUM({field})，平均可用 AVG({field})。",
    ),
    StandardMetric(
        name="故障次数",
        aliases=("故障事件数", "报警次数", "异常次数", "fault count"),
        standard_field="fault_event_count",
        description="反映设备或批次发生的故障/异常事件次数。",
        calculation="统计总和可用 SUM({field})。",
    ),
    StandardMetric(
        name="产量",
        aliases=("生产量", "产出量", "production volume"),
        standard_field="production_volume",
        description="反映生产数量，数值越高表示产出越多。",
        calculation="统计趋势可用 SUM({field}) 并按时间/产线/班次分组。",
    ),
    StandardMetric(
        name="设备利用率",
        aliases=("设备使用率", "利用率"),
        standard_field="machine_utilization",
        description="反映设备利用程度，数值越高表示产能利用越充分。",
        calculation="统计平均值可用 AVG({field})。",
    ),
)


# ----------------------------------------------------------------------
# 查询与匹配
# ----------------------------------------------------------------------
_FIELD_BY_NAME: dict[str, StandardField] = {field.name: field for field in STANDARD_FIELDS}


def canonical_names() -> tuple[str, ...]:
    """全部标准字段名，顺序与 :data:`STANDARD_FIELDS` 一致。"""
    return tuple(field.name for field in STANDARD_FIELDS)


def field_by_name(name: str) -> StandardField | None:
    """按规范名取标准字段；不存在返回 ``None``。"""
    return _FIELD_BY_NAME.get(str(name or "").strip())


def metrics_for_field(field_name: str) -> tuple[StandardMetric, ...]:
    """取以某标准字段为口径的业务指标。"""
    return tuple(
        metric for metric in STANDARD_METRICS if metric.standard_field == field_name
    )


def build_match_index(
    fields: Sequence[StandardField] = STANDARD_FIELDS,
) -> dict[str, list[str]]:
    """构造 ``归一化候选名 -> [标准字段名]`` 索引。

    一个候选名可能被多个字段声明（例如 ``util``），索引会把它记成多对多，
    匹配时据此判为**歧义**，交由人审核而不是猜一个。
    """
    index: dict[str, list[str]] = {}
    for standard in fields:
        for candidate in standard.candidate_set():
            bucket = index.setdefault(candidate, [])
            if standard.name not in bucket:
                bucket.append(standard.name)
    return index


def match_columns(
    columns: Iterable[str],
    *,
    table: str = "",
    fields: Sequence[StandardField] = STANDARD_FIELDS,
    index: dict[str, list[str]] | None = None,
) -> list[ColumnMatch]:
    """把客户列名反向匹配到标准字段。

    返回每条列的结论，状态有三种：

    - ``matched``：**唯一**命中一个标准字段；
    - ``ambiguous``：命中多个标准字段（例如 ``util`` 同时像设备利用率与资源利用率），
      ``candidates`` 给出候选，需人工在 mapping.yaml 里定夺；
    - ``unmapped``：没有标准字段认领，属于客户特有列，映射里可留空。

    只做唯一匹配是刻意的：自动草拟映射时「猜错」比「标为歧义」代价大得多，
    因为一个错误的字段映射会让生成的 SQL 静默算错，而不是报错。
    """
    lookup = index if index is not None else build_match_index(fields)
    results: list[ColumnMatch] = []

    for column in columns:
        candidates = list(lookup.get(normalize_identifier(column), []))
        if len(candidates) == 1:
            results.append(
                ColumnMatch(
                    table=table,
                    column=column,
                    status=MATCHED,
                    standard_field=candidates[0],
                )
            )
        elif len(candidates) > 1:
            results.append(
                ColumnMatch(
                    table=table, column=column, status=AMBIGUOUS, candidates=candidates
                )
            )
        else:
            results.append(ColumnMatch(table=table, column=column, status=UNMAPPED))

    return results


def field_to_dict(field: StandardField) -> dict[str, Any]:
    """序列化标准字段，供草稿生成 / 审核 CLI 输出 JSON。"""
    return {
        "name": field.name,
        "label": field.label,
        "unit": field.unit,
        "data_kind": field.data_kind,
        "group": field.group,
        "aliases": list(field.aliases),
        "column_candidates": list(field.column_candidates),
    }
