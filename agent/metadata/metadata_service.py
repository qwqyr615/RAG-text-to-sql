"""数据资源理解服务。

从数据库动态读取表结构、字段类型、样例值，输出统一 JSON 供 Agent / 前端使用。

字段说明的来源优先级（**DDL 是唯一事实来源**）：

1. 建表 DDL 的 ``COMMENT``（见 ``sql/02_create_fact_production_record.sql``）；
2. 兜底：``metadata/preset_metadata.py`` 中人工维护的说明。

表说明同理：优先读表级 ``COMMENT``，其次用 ``TABLE_DESCRIPTIONS``。
这样「说明」与「结构」写在同一处，不会各改一份而漂移；健康检查
（``scripts/check_schema_health.py``）会校验每个列都有 COMMENT。
"""

import json
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text

from core.config import BASE_DIR
from metadata.preset_metadata import (
    PRESET_BUSINESS_TABLES,
    RELATIONSHIPS,
    TABLE_DESCRIPTIONS,
    get_column_description,
)
from tools.database import get_engine

OUTPUT_DIR = BASE_DIR / "outputs"


def _to_jsonable(value: Any) -> Any:
    """把数据库值转成可 JSON 序列化的值。"""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def get_metadata_json(engine: Any | None = None) -> dict[str, Any]:
    """读取当前数据源的完整元数据 JSON。

    表范围由 ``PRESET_BUSINESS_TABLES`` 白名单限定：单表模型下就是
    ``fact_production_record`` 一张，Agent 的 ``include_tables`` 也来自这里。

    参数:
        engine: 可注入的 SQLAlchemy Engine，默认用 ``get_engine()``。
                单元测试用内存库注入，避免依赖 MySQL。
    """
    engine = engine if engine is not None else get_engine()
    inspector = inspect(engine)
    all_tables = set(inspector.get_table_names())
    table_names = [name for name in PRESET_BUSINESS_TABLES if name in all_tables]

    tables = []
    for table_name in table_names:
        columns_info = inspector.get_columns(table_name)
        pk_constraint = inspector.get_pk_constraint(table_name)
        pk_columns = set(pk_constraint.get("constrained_columns") or [])

        columns = []
        sample_rows: list[dict[str, Any]] = []
        try:
            with engine.connect() as conn:
                result = conn.execute(text(f"SELECT * FROM {table_name} LIMIT 2"))
                keys = list(result.keys())
                sample_rows = [
                    {key: _to_jsonable(value) for key, value in zip(keys, row)}
                    for row in result.fetchall()
                ]
        except Exception:
            sample_rows = []

        for col in columns_info:
            col_name = col["name"]
            columns.append(
                {
                    "name": col_name,
                    "type": str(col.get("type", "")),
                    "nullable": col.get("nullable", True),
                    "default": col.get("default"),
                    "primary_key": col_name in pk_columns,
                    "description": (
                        str(col.get("comment") or "").strip()
                        or get_column_description(table_name, col_name)
                    ),
                    "sample_value": (
                        sample_rows[0].get(col_name) if sample_rows else None
                    ),
                }
            )

        tables.append(
            {
                "table_name": table_name,
                "description": (
                    _get_table_comment(inspector, table_name)
                    or TABLE_DESCRIPTIONS.get(table_name, "")
                ),
                "columns": columns,
                "sample_rows": sample_rows,
                "row_count": _get_row_count(engine, table_name),
            }
        )

    relationships = [
        rel
        for rel in RELATIONSHIPS
        if rel["source_table"] in all_tables and rel["target_table"] in all_tables
    ]

    return {
        "schema_version": "1.0",
        "database_type": engine.dialect.name,
        "tables": tables,
        "relationships": relationships,
    }


def _get_table_comment(inspector: Any, table_name: str) -> str:
    """读取表级 COMMENT，失败时返回空字符串。"""
    try:
        comment = inspector.get_table_comment(table_name) or {}
    except Exception:  # noqa: BLE001 - 部分方言不支持表注释
        return ""
    return str(comment.get("text") or "").strip()


def _get_row_count(engine: Any, table_name: str) -> int:
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            return int(result.scalar_one())
    except Exception:
        return -1


def export_metadata_json(
    path: Path | None = None, engine: Any | None = None
) -> Path:
    """导出元数据 JSON 到 outputs/metadata.json。"""
    output_path = path or (OUTPUT_DIR / "metadata.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = get_metadata_json(engine)
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


if __name__ == "__main__":
    export_metadata_json()
    print(f"metadata exported: {OUTPUT_DIR / 'metadata.json'}")
