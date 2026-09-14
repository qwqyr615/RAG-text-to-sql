"""数据资源理解服务。

从数据库动态读取表结构、字段类型、样例值，输出统一 JSON 供 Agent / 前端使用。

表范围：**发现模式**
--------------------
不硬编码业务表白名单。每次调用都扫描数据库，套用排除规则（系统表前缀、原始层表）
得到当前可见表集合 —— 规则来源见 :mod:`metadata.inventory`。``mapping.yaml`` 的
``discovery.include`` 可以把范围收敛到客户指定的几张表。

字段说明的来源优先级
--------------------
1. ``mapping.yaml`` 里的**标准字段口径**（推荐，且是接入客户库的唯一要求）；
2. 建表 DDL 的 ``COMMENT``（见 ``sql/02_create_fact_production_record.sql``）；
3. 兜底：``metadata/preset_metadata.py`` 中人工维护的说明。

第 1 条优先于第 2 条是刻意的：客户库几乎没有列注释（本次评测的 ``mes_prod_log``
45 列全空），而映射里的口径是人审过的、还带单位换算，信息量严格更大。

输出结构在原先基础上**只增不改**（``tables`` / ``relationships`` / ``sample_rows``
字段不变），新增：
- ``field_map``：每张表的标准字段口径，供 Prompt 的字段映射段渲染；
- ``inventory``：发现过程的账本（哪些表被排除、为什么），供接入与排障；
- ``database_type`` / ``mapping_profile``：数据源标识。
"""

import json
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from core.config import BASE_DIR, settings
from metadata.inventory import discover_tables
from metadata.mapping import CompiledMapping, MappingProfile, resolve_mapping
from metadata.preset_metadata import (
    RELATIONSHIPS,
    TABLE_DESCRIPTIONS,
    get_column_description,
)
from tools.database import get_engine

OUTPUT_DIR = BASE_DIR / "outputs"

#: 每张表取几行样例，用于向模型展示真实取值（枚举列尤其重要）
SAMPLE_ROWS = 2


def _to_jsonable(value: Any) -> Any:
    """把数据库值转成可 JSON 序列化的值。"""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _split_list(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def get_metadata_json(
    engine: Engine | None = None,
    *,
    mapping: CompiledMapping | None = None,
    use_mapping_cache: bool = True,
) -> dict[str, Any]:
    """读取当前数据源的完整元数据 JSON。

    参数:
        engine: 可注入的 SQLAlchemy Engine，默认用 :func:`get_engine`。
            单元测试用内存库注入，避免依赖 MySQL。
        mapping: 可注入的已编译映射；不传则按 ``ANALYSIS_MAPPING`` 自动加载。
            显式传 ``use_mapping_cache`` 与 mapping=None 时才会走缓存。
    """
    engine = engine if engine is not None else get_engine()

    if mapping is None:
        mapping = resolve_mapping(use_cache=use_mapping_cache)
    profile: MappingProfile | None = mapping.profile if mapping else None

    inventory = discover_tables(
        engine,
        exclude=_split_list(settings.discovery_exclude_tables),
        exclude_prefixes=_split_list(settings.discovery_exclude_prefixes),
        profile=profile,
    )

    inspector = inspect(engine)
    tables = []
    for table_name in inventory.business_tables:
        tables.append(
            _describe_table(
                engine, inspector, table_name, mapping=mapping, inventory=inventory
            )
        )

    relationships = _relationships(inventory, profile)

    return {
        "schema_version": "1.1",
        "database_type": engine.dialect.name,
        "mapping_profile": profile.profile if profile else "",
        "mapping_source": str(profile.source_path) if profile and profile.source_path else "",
        "tables": tables,
        "relationships": relationships,
        "field_map": _field_map(mapping, inventory),
        # 指标口径已落到客户侧表达式，供指标段直接展示「要写哪个表达式」
        "metric_bindings": list(mapping.metrics) if mapping else [],
        "unmatched_metrics": list(mapping.unmatched_metrics) if mapping else [],
        "inventory": {
            "all_tables": inventory.all_tables,
            "business_tables": inventory.business_tables,
            "excluded": inventory.excluded,
            "roles": inventory.roles,
        },
    }


def _describe_table(
    engine: Engine,
    inspector: Any,
    table_name: str,
    *,
    mapping: CompiledMapping | None,
    inventory: Any,
) -> dict[str, Any]:
    """描述一张表的列、样例值与标准字段口径。"""
    columns_info = inspector.get_columns(table_name)
    pk_constraint = inspector.get_pk_constraint(table_name)
    pk_columns = set(pk_constraint.get("constrained_columns") or [])

    sample_rows: list[dict[str, Any]] = []
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT * FROM {table_name} LIMIT {SAMPLE_ROWS}"))
            keys = list(result.keys())
            sample_rows = [
                {key: _to_jsonable(value) for key, value in zip(keys, row)}
                for row in result.fetchall()
            ]
    except Exception:  # noqa: BLE001 - 样例值取不到不应阻断元数据
        sample_rows = []

    # 该表的标准字段口径：列 -> 标准字段名，用于给列打标
    column_to_standard: dict[str, list[str]] = {}
    if mapping is not None:
        for item in mapping.table(table_name):
            column_to_standard.setdefault(item.column, []).append(item.standard_field)

    columns = []
    for col in columns_info:
        col_name = col["name"]
        standards = column_to_standard.get(col_name, [])
        description = (
            _mapping_description(mapping, table_name, standards)
            or str(col.get("comment") or "").strip()
            or get_column_description(table_name, col_name)
        )
        columns.append(
            {
                "name": col_name,
                "type": str(col.get("type", "")),
                "nullable": col.get("nullable", True),
                "default": col.get("default"),
                "primary_key": col_name in pk_columns,
                "description": description,
                "standard_fields": standards,
                "sample_value": (sample_rows[0].get(col_name) if sample_rows else None),
            }
        )

    hints = (mapping.table_hints.get(table_name) if mapping else None) or {}
    role = inventory.roles.get(table_name, "other")
    description = (
        hints.get("description")
        or _get_table_comment(inspector, table_name)
        or TABLE_DESCRIPTIONS.get(table_name, "")
    )

    return {
        "table_name": table_name,
        "description": description,
        "role": role,
        "grain": hints.get("grain", ""),
        "primary_key": hints.get("primary_key", ""),
        "time_column": hints.get("time_column", ""),
        "columns": columns,
        "sample_rows": sample_rows,
        "row_count": _get_row_count(engine, table_name),
    }


def _mapping_description(
    mapping: CompiledMapping | None, table_name: str, standards: list[str]
) -> str:
    """用映射里的标准字段口径生成列说明。"""
    if mapping is None or not standards:
        return ""
    parts: list[str] = []
    for name in standards:
        item = mapping.field(table_name, name)
        if item is None:
            continue
        text = f"{item.label}（标准字段 {item.standard_field}）"
        if item.needs_conversion:
            text += f"，SQL 请写 {item.expression}"
        if item.unit and item.unit != "—":
            text += f"，单位 {item.unit}"
        if item.enum_values:
            text += f"，取值 {'/'.join(item.enum_values)}"
        parts.append(text)
    return "；".join(parts)


def _field_map(
    mapping: CompiledMapping | None, inventory: Any
) -> dict[str, list[dict[str, Any]]]:
    """标准字段口径的结构化输出，供 Prompt 与前端使用。"""
    if mapping is None:
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for table_name in inventory.business_tables:
        items = mapping.table(table_name)
        if not items:
            continue
        result[table_name] = [
            {
                "standard_field": item.standard_field,
                "label": item.label,
                "column": item.column,
                "expression": item.expression,
                "unit": item.unit,
                "customer_unit": item.customer_unit,
                "scale": item.scale,
                "data_kind": item.data_kind,
                "group": item.group,
                "enum_values": list(item.enum_values),
                "needs_conversion": item.needs_conversion,
            }
            for item in items
        ]
    return result


def _relationships(
    inventory: Any, profile: MappingProfile | None
) -> list[dict[str, Any]]:
    """表间关系：映射声明的关系优先，其次用内置关系（单表模型下为空）。"""
    declared: list[dict[str, Any]] = []
    if profile is not None:
        for relation in profile.relationships:
            source = relation.get("source_table")
            target = relation.get("target_table")
            if source in inventory.business_tables and target in inventory.business_tables:
                declared.append(dict(relation))
    if declared:
        return declared

    return [
        rel
        for rel in RELATIONSHIPS
        if rel["source_table"] in inventory.business_tables
        and rel["target_table"] in inventory.business_tables
    ]


def _get_table_comment(inspector: Any, table_name: str) -> str:
    """读取表级 COMMENT，失败时返回空字符串。"""
    try:
        comment = inspector.get_table_comment(table_name) or {}
    except Exception:  # noqa: BLE001 - 部分方言不支持表注释
        return ""
    return str(comment.get("text") or "").strip()


def _get_row_count(engine: Engine, table_name: str) -> int:
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            return int(result.scalar_one())
    except Exception:
        return -1


def export_metadata_json(
    path: Path | None = None,
    engine: Engine | None = None,
    *,
    mapping: CompiledMapping | None = None,
) -> Path:
    """导出元数据 JSON 到 outputs/metadata.json。"""
    output_path = path or (OUTPUT_DIR / "metadata.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = get_metadata_json(engine, mapping=mapping)
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


if __name__ == "__main__":
    export_metadata_json()
    print(f"metadata exported: {OUTPUT_DIR / 'metadata.json'}")
