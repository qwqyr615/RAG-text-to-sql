"""预置数据底座 schema 健康检查（演示前 / 提交前跑一次）。

以 ``sql/02_create_fact_production_record.sql`` 为期望结构，与线上库逐项比对：

1. 表清单符合单表模型：只有原始层 + 服务层，没有历史派生维表
2. 列清单与顺序与 DDL 完全一致（防漂移）
3. 列类型与 DDL 一致，且**不存在 TEXT 类型列**（本次治理的核心问题）
4. 主键与 grain：``record_id`` 是主键、唯一、非空
5. 可空性与 DDL 一致，且 NOT NULL 列实际没有空值
6. 行数与原始层一致，且 11 个转换过的列**数值保真**（源表文本 vs 服务层数值）
7. 数值排序回归：列的 ``MIN`` / ``MAX`` 必须等于「按数值解释后的 MIN/MAX」
   （TEXT 存储时是字典序，两者不等，这正是曾经静默返回错误答案的根因）
8. 每个列都有 COMMENT（保证元数据说明不丢失）
9. **发现模式**：Agent 可见表集合由扫描 + 排除规则得出，且排除了原始层
10. **字段映射**（配置了 ``ANALYSIS_MAPPING`` 时）：映射能编译、引用的表与列都真实存在

检查 9 / 10 替代了原先的「preset_metadata 白名单校验」—— 业务表范围不再是代码里的
常量，而是发现与映射的产物，因此校验对象也跟着变。

运行::

    cd agent
    D:\\Anaconda\\envs\\sqllangchain\\python.exe scripts\\check_schema_health.py

退出码：全部通过为 0，任一项失败为 1（可直接用于 CI 或演示前自检）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# 允许用 `python scripts/xxx.py` 直接运行（该方式下 sys.path[0] 是 scripts/）
_AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from core.config import settings  # noqa: E402
from metadata.inventory import discover_tables  # noqa: E402
from metadata.mapping import resolve_mapping  # noqa: E402
from metadata.mapping.schema import mapping_coverage  # noqa: E402
from metadata.preset_metadata import COLUMN_DESCRIPTIONS, RELATIONSHIPS  # noqa: E402
from metadata.schema_ddl import (  # noqa: E402
    CONVERTED_NUMERIC_COLUMNS,
    LEGACY_DIM_TABLES,
    SOURCE_TABLE,
    DdlColumn,
    expected_columns,
    expected_primary_key,
    expected_table,
    normalize_type,
)
from tools.database import get_engine  # noqa: E402

#: 浮点比较容差。
#: MySQL 对精确类型（INT / DECIMAL）做 AVG() 会返回 4 位小数的 DECIMAL，
#: 例如 INT 列均值 2.1246667 会变成 2.1247，因此容差要覆盖 5e-5 的舍入误差。
_TOLERANCE = 1e-4


@dataclass
class CheckResult:
    """单项检查结果。"""

    name: str
    passed: bool
    detail: str = ""
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        lines = [f"[{mark}] {self.name}"]
        if self.detail:
            lines.append(f"       {self.detail}")
        lines.extend(f"       - {note}" for note in self.notes)
        return "\n".join(lines)


@dataclass
class SchemaSnapshot:
    """一次性的库结构快照，避免每个检查各查一遍。"""

    table_names: set[str]
    columns: list[dict[str, Any]]
    primary_key: list[str]
    row_count: int

    @property
    def column_names(self) -> list[str]:
        return [str(column["name"]) for column in self.columns]

    def column(self, name: str) -> dict[str, Any] | None:
        for column in self.columns:
            if str(column["name"]) == name:
                return column
        return None


def _snapshot(engine: Engine, table: str) -> SchemaSnapshot:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if table not in table_names:
        return SchemaSnapshot(
            table_names=table_names, columns=[], primary_key=[], row_count=0
        )

    with engine.connect() as conn:
        row_count = int(
            conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        )
    primary_key = list(
        (inspector.get_pk_constraint(table).get("constrained_columns") or [])
    )
    return SchemaSnapshot(
        table_names=table_names,
        columns=list(inspector.get_columns(table)),
        primary_key=primary_key,
        row_count=row_count,
    )


# ----------------------------------------------------------------------
# 检查项
# ----------------------------------------------------------------------
def _check_table_inventory(engine: Engine, snapshot: SchemaSnapshot) -> CheckResult:
    """标准模型表清单：原始层 + 服务层必须存在，历史派生维表必须不存在。

    注意**不**把「库里还有别的表」判为失败：客户库里本来就会有别的业务表
    （例如本次评测的 ``mes_prod_log``）。哪些表对 Agent 可见由发现模式决定，
    由 ``_check_discovery_mode`` 单独把关。
    """
    table = expected_table()
    missing = [name for name in (table, SOURCE_TABLE) if name not in snapshot.table_names]
    leftovers = [name for name in LEGACY_DIM_TABLES if name in snapshot.table_names]
    passed = not missing and not leftovers

    detail = (
        f"库中共 {len(snapshot.table_names)} 张表："
        f"{', '.join(sorted(snapshot.table_names))}"
    )
    notes = []
    if missing:
        notes.append(
            f"缺少必需表：{', '.join(missing)}（先运行 scripts/init_preset_schema.py）"
        )
    if leftovers:
        notes.append(f"仍存在历史派生维表：{', '.join(leftovers)}（路线 B 不应保留）")
    return CheckResult(
        name="标准模型表清单（原始层 + 服务层，无历史派生维表）",
        passed=passed,
        detail=detail,
        notes=notes,
    )


def _compare_columns(
    snapshot: SchemaSnapshot, expected: list[DdlColumn]
) -> list[str]:
    """返回列清单/顺序不一致的说明。"""
    actual = snapshot.column_names
    wanted = [column.name for column in expected]

    issues: list[str] = []
    if actual != wanted:
        missing = [name for name in wanted if name not in actual]
        extra = [name for name in actual if name not in wanted]
        if missing:
            issues.append(f"DDL 有但库里没有：{', '.join(missing)}")
        if extra:
            issues.append(f"库里有但 DDL 没有：{', '.join(extra)}")
        if not missing and not extra:
            issues.append("列集合相同但顺序不一致")
    return issues


def _check_column_list(engine: Engine, snapshot: SchemaSnapshot) -> CheckResult:
    expected = expected_columns()
    issues = _compare_columns(snapshot, expected)
    return CheckResult(
        name="列清单与顺序与 DDL 一致",
        passed=not issues,
        detail=f"DDL 声明 {len(expected)} 列，库中 {len(snapshot.column_names)} 列",
        notes=issues,
    )


def _check_column_types(engine: Engine, snapshot: SchemaSnapshot) -> CheckResult:
    expected = expected_columns()
    issues: list[str] = []
    for column in expected:
        actual = snapshot.column(column.name)
        if actual is None:
            continue
        actual_type = normalize_type(actual["type"])
        if actual_type != column.type:
            issues.append(
                f"{column.name}: DDL={column.type} 实际={actual_type}"
                f"（原始 {actual['type']}）"
            )

    text_columns = [
        name
        for name in snapshot.column_names
        if "TEXT" in normalize_type(snapshot.column(name)["type"])  # type: ignore[index]
    ]
    if text_columns:
        issues.append(f"仍存在 TEXT 类型列：{', '.join(text_columns)}")

    return CheckResult(
        name="列类型与 DDL 一致且无 TEXT 列",
        passed=not issues,
        detail=f"已比对 {len(expected)} 个列的类型",
        notes=issues,
    )


def _check_primary_key(engine: Engine, snapshot: SchemaSnapshot) -> CheckResult:
    table = expected_table()
    wanted = expected_primary_key()
    issues: list[str] = []

    if wanted is None:
        issues.append("DDL 未声明主键")
    elif snapshot.primary_key != [wanted]:
        issues.append(f"主键应为 {wanted}，实际为 {snapshot.primary_key or '无'}")

    if wanted:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT COUNT(*), COUNT(DISTINCT {wanted}), "
                    f"SUM(CASE WHEN {wanted} IS NULL THEN 1 ELSE 0 END) "
                    f"FROM {table}"
                )
            ).fetchone()
        total, distinct, nulls = int(row[0]), int(row[1]), int(row[2] or 0)
        if distinct != total:
            issues.append(f"{wanted} 不唯一：{total} 行中只有 {distinct} 个不同值")
        if nulls:
            issues.append(f"{wanted} 存在 {nulls} 个空值")
        detail = f"{table}.{wanted}：{total} 行 / {distinct} 个唯一值 / {nulls} 个空值"
    else:
        detail = "无主键声明"

    return CheckResult(
        name="主键与 grain 唯一性", passed=not issues, detail=detail, notes=issues
    )


def _check_nullability(engine: Engine, snapshot: SchemaSnapshot) -> CheckResult:
    table = expected_table()
    expected = expected_columns()
    issues: list[str] = []

    for column in expected:
        actual = snapshot.column(column.name)
        if actual is None:
            continue
        actual_nullable = bool(actual.get("nullable", True))
        if actual_nullable != column.nullable:
            issues.append(
                f"{column.name}: DDL 为 {'NULL' if column.nullable else 'NOT NULL'}，"
                f"实际为 {'NULL' if actual_nullable else 'NOT NULL'}"
            )

    not_null_columns = [column.name for column in expected if not column.nullable]
    if not_null_columns and not issues:
        select_parts = ", ".join(
            f"SUM(CASE WHEN `{name}` IS NULL THEN 1 ELSE 0 END)"
            for name in not_null_columns
        )
        with engine.connect() as conn:
            null_counts = conn.execute(
                text(f"SELECT {select_parts} FROM {table}")
            ).fetchone()
        for name, nulls in zip(not_null_columns, null_counts):
            if int(nulls or 0):
                issues.append(f"{name} 声明 NOT NULL 但存在 {int(nulls)} 个空值")

    return CheckResult(
        name="可空性与 DDL 一致且无空值违反",
        passed=not issues,
        detail=f"其中 {len(not_null_columns)} 个 NOT NULL 列已核对实际数据",
        notes=issues,
    )


def _check_row_parity_and_fidelity(
    engine: Engine, snapshot: SchemaSnapshot
) -> CheckResult:
    table = expected_table()
    issues: list[str] = []

    with engine.connect() as conn:
        source_rows = int(
            conn.execute(text(f"SELECT COUNT(*) FROM {SOURCE_TABLE}")).scalar_one()
        )
        if source_rows != snapshot.row_count:
            issues.append(
                f"行数不一致：原始层 {source_rows} 行，服务层 {snapshot.row_count} 行"
            )

        for column in CONVERTED_NUMERIC_COLUMNS:
            source_avg = conn.execute(
                text(f"SELECT AVG(CAST(TRIM(`{column}`) AS DOUBLE)) FROM {SOURCE_TABLE}")
            ).scalar_one()
            target_avg = conn.execute(
                text(f"SELECT AVG(`{column}`) FROM {table}")
            ).scalar_one()

            if source_avg is None or target_avg is None:
                issues.append(
                    f"{column}: 平均值无法计算（源={source_avg} 服务层={target_avg}）"
                )
                continue
            tolerance = max(_TOLERANCE, abs(float(source_avg)) * 1e-9)
            if abs(float(source_avg) - float(target_avg)) > tolerance:
                issues.append(
                    f"{column}: 转换后数值不一致（源表均值 {float(source_avg):.6f}，"
                    f"服务层均值 {float(target_avg):.6f}）"
                )

    return CheckResult(
        name="行数与数值保真（转换未静默归零）",
        passed=not issues,
        detail=(
            f"服务层 {snapshot.row_count} 行，"
            f"已核对 {len(CONVERTED_NUMERIC_COLUMNS)} 个转换列的均值"
        ),
        notes=issues,
    )


def _check_numeric_sorting(engine: Engine, snapshot: SchemaSnapshot) -> CheckResult:
    """数值排序回归。

    关键断言是「列自身的 MIN/MAX」必须等于「把该列按数值解释后的 MIN/MAX」。
    若列仍是 TEXT，``MIN`` / ``MAX`` 走字典序（``'9.99'`` 大于 ``'25.08'``），
    两者不等 —— 这正是曾经静默返回错误答案的根因。

    注意不能只比较 ``ORDER BY col DESC`` 与 ``MAX(col)``：在 TEXT 列上两者都是
    字典序、彼此自洽，检查会漏判。
    """
    table = expected_table()
    issues: list[str] = []

    with engine.connect() as conn:
        for column in CONVERTED_NUMERIC_COLUMNS:
            row = conn.execute(
                text(
                    f"SELECT MIN(`{column}`), MAX(`{column}`), "
                    f"MIN(CAST(`{column}` AS DOUBLE)), MAX(CAST(`{column}` AS DOUBLE)) "
                    f"FROM {table}"
                )
            ).fetchone()
            column_min, column_max, numeric_min, numeric_max = row

            if column_min is None or column_max is None:
                issues.append(f"{column}: 无数据")
                continue

            if float(column_min) != float(numeric_min) or float(column_max) != float(
                numeric_max
            ):
                issues.append(
                    f"{column}: 列的 MIN/MAX 与按数值解释的结果不一致"
                    f"（列：{column_min} ~ {column_max}，"
                    f"数值：{numeric_min} ~ {numeric_max}），说明仍在按字典序比较"
                )
                continue

            highest = conn.execute(
                text(f"SELECT `{column}` FROM {table} ORDER BY `{column}` DESC LIMIT 1")
            ).scalar_one()
            if float(highest) != float(numeric_max):
                issues.append(
                    f"{column}: ORDER BY DESC 首行 {highest} != MAX {numeric_max}"
                )

    return CheckResult(
        name="数值排序回归（MIN/MAX 与数值语义一致）",
        passed=not issues,
        detail=f"已核对 {len(CONVERTED_NUMERIC_COLUMNS)} 个原 TEXT 列的排序语义",
        notes=issues,
    )


def _check_comments(engine: Engine, snapshot: SchemaSnapshot) -> CheckResult:
    missing = [
        str(column["name"])
        for column in snapshot.columns
        if not str(column.get("comment") or "").strip()
    ]
    return CheckResult(
        name="每个列都有 COMMENT",
        passed=not missing,
        detail=f"已核对 {len(snapshot.column_names)} 个列",
        notes=[f"缺少 COMMENT：{', '.join(missing)}"] if missing else [],
    )


def _split_list(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _load_profile_quietly() -> Any:
    """加载映射的 ``discovery`` 规则；加载失败时返回 ``None``。

    发现模式检查要的是「排除规则生效了吗」，映射本身能不能编译由
    ``_check_mapping`` 单独负责 —— 这里失败不该重复报错。
    """
    try:
        mapping = resolve_mapping(use_cache=False)
    except Exception:  # noqa: BLE001
        return None
    return mapping.profile if mapping else None


def _check_discovery_mode(engine: Engine, snapshot: SchemaSnapshot) -> CheckResult:
    """发现模式：Agent 可见表由扫描 + 排除规则得出，原始层必须被挡住。

    这条检查取代了原先的「白名单引用了不存在的表」。业务表范围不再是代码里的常量，
    因此要守住的是两件事：

    1. 可见表集合非空（否则 Agent 启动时会直接失败）；
    2. 原始层 ``intelligent_production_iiot`` 不在可见集合里 —— 它是未治理的脏类型
       副本，暴露出去会让模型在两张内容等价的表之间随机挑选并可能算错。
    """
    inventory = discover_tables(
        engine,
        exclude=_split_list(settings.discovery_exclude_tables),
        exclude_prefixes=_split_list(settings.discovery_exclude_prefixes),
        profile=_load_profile_quietly(),
    )
    issues: list[str] = []

    if not inventory.business_tables:
        issues.append("发现模式下没有可见业务表，Agent 将无法启动")
    if SOURCE_TABLE in inventory.business_tables:
        issues.append(
            f"原始层 {SOURCE_TABLE} 暴露给了 Agent（字段类型未经治理，"
            "应在 mapping.yaml 的 discovery.exclude 或 DISCOVERY_EXCLUDE_TABLES 中排除）"
        )

    detail = (
        f"{inventory.summary()}；可见：{', '.join(inventory.business_tables) or '无'}"
    )
    if inventory.excluded:
        detail += f"；排除：{', '.join(sorted(inventory.excluded))}"

    return CheckResult(
        name="发现模式（扫描全库 + 排除系统表与原始层）",
        passed=not issues,
        detail=detail,
        notes=issues,
    )


def _check_mapping(engine: Engine, snapshot: SchemaSnapshot) -> CheckResult:
    """字段映射：能编译、引用的表与列真实存在、覆盖率可解释。

    未配置映射时本项直接通过（此时系统走「单表标准模型」，用列 COMMENT 当说明），
    并在 detail 里说明 —— 免得看到 PASS 却不知道到底验了什么。
    """
    from metadata.mapping import default_mapping_path

    target = default_mapping_path()
    if target is None:
        return CheckResult(
            name="字段映射（ANALYSIS_MAPPING）",
            passed=True,
            detail="未配置映射，系统按「单表标准模型」使用数据库列 COMMENT 作为字段说明",
        )

    try:
        mapping = resolve_mapping(use_cache=False)
    except Exception as exc:  # noqa: BLE001 - 映射错误要作为检查失败报出来
        return CheckResult(
            name="字段映射（ANALYSIS_MAPPING）",
            passed=False,
            detail=f"加载映射失败：{target}",
            notes=[str(exc)],
        )

    if mapping is None:  # pragma: no cover - 与上面 target is None 等价
        return CheckResult(name="字段映射（ANALYSIS_MAPPING）", passed=True, detail="未启用")

    inspector = inspect(engine)
    live_columns = {
        table: [str(column["name"]) for column in inspector.get_columns(table)]
        for table in inspector.get_table_names()
    }
    coverage = mapping_coverage(mapping.profile, live_columns)

    issues: list[str] = []
    for table_name, info in coverage["tables"].items():
        if info["standard_fields"] == 0:
            issues.append(f"{table_name}: 一个标准字段都没有映射")
        if info["unmapped_columns"]:
            issues.append(
                f"{table_name}: {len(info['unmapped_columns'])} 个列没有标准口径"
                f"（{', '.join(info['unmapped_columns'][:8])}）"
            )

    converted = [
        item
        for table in mapping.tables
        for item in mapping.table(table)
        if item.needs_conversion
    ]

    detail = (
        f"profile={mapping.profile.profile or '未命名'}，"
        f"表 {len(mapping.tables)} 张，"
        f"标准字段 {sum(len(mapping.table(t)) for t in mapping.tables)} 个，"
        f"需单位换算 {len(converted)} 个"
    )
    return CheckResult(
        name="字段映射（ANALYSIS_MAPPING）",
        passed=not issues,
        detail=detail,
        notes=issues,
    )


def _check_preset_metadata(engine: Engine, snapshot: SchemaSnapshot) -> CheckResult:
    """兜底说明不引用不存在的表或列（仅在未配置映射时才有实际约束力）。"""
    issues: list[str] = []
    known_tables = snapshot.table_names

    for key in COLUMN_DESCRIPTIONS:
        table_name, _, column_name = key.partition(".")
        if table_name not in known_tables:
            issues.append(f"COLUMN_DESCRIPTIONS 引用了不存在的表：{key}")
        elif column_name and table_name == expected_table():
            if snapshot.column(column_name) is None:
                issues.append(f"COLUMN_DESCRIPTIONS 引用了不存在的列：{key}")

    relations = list(RELATIONSHIPS)
    for relation in relations:
        for side in ("source", "target"):
            table_name = relation[f"{side}_table"]
            column_name = relation[f"{side}_column"]
            if table_name not in known_tables:
                issues.append(f"RELATIONSHIPS 引用了不存在的表：{table_name}")
            elif table_name == expected_table() and snapshot.column(column_name) is None:
                issues.append(
                    f"RELATIONSHIPS 引用了不存在的列：{table_name}.{column_name}"
                )

    detail = f"兜底字段说明 {len(COLUMN_DESCRIPTIONS)} 条，表间关系 {len(relations)} 条"
    return CheckResult(
        name="兜底说明不引用不存在的表或列",
        passed=not issues,
        detail=detail,
        notes=issues,
    )


CHECKS: list[tuple[str, Callable[[Engine, SchemaSnapshot], CheckResult]]] = [
    ("标准模型表清单（原始层 + 服务层，无历史派生维表）", _check_table_inventory),
    ("列清单与顺序与 DDL 一致", _check_column_list),
    ("列类型与 DDL 一致且无 TEXT 列", _check_column_types),
    ("主键与 grain 唯一性", _check_primary_key),
    ("可空性与 DDL 一致且无空值违反", _check_nullability),
    ("行数与数值保真（转换未静默归零）", _check_row_parity_and_fidelity),
    ("数值排序回归（MIN/MAX 与数值语义一致）", _check_numeric_sorting),
    ("每个列都有 COMMENT", _check_comments),
    ("发现模式（扫描全库 + 排除系统表与原始层）", _check_discovery_mode),
    ("字段映射（ANALYSIS_MAPPING）", _check_mapping),
    ("兜底说明不引用不存在的表或列", _check_preset_metadata),
]


def run_health_check(engine: Engine | None = None) -> list[CheckResult]:
    """执行全部检查并返回结果列表。"""
    engine = engine or get_engine()
    snapshot = _snapshot(engine, expected_table())

    results: list[CheckResult] = []
    for name, check in CHECKS:
        try:
            results.append(check(engine, snapshot))
        except Exception as exc:  # noqa: BLE001 - 单项异常不应中断整体检查
            results.append(
                CheckResult(name=name, passed=False, detail=f"检查执行失败：{exc}")
            )
    return results


def main() -> int:
    results = run_health_check()

    print("=" * 70)
    print("预置数据底座 schema 健康检查")
    print("=" * 70)
    for result in results:
        print(result.render())

    failed = [result for result in results if not result.passed]
    print("=" * 70)
    print(f"共 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项：" + "、".join(result.name for result in failed))
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
