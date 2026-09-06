"""数据资源理解服务。

从 MySQL 动态读取表结构、字段类型、样例值，
再合并预置的表说明、字段说明、表间关系，最终输出统一 JSON。
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


def get_metadata_json() -> dict[str, Any]:
    """读取当前数据源的完整元数据 JSON。"""
    engine = get_engine()
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
                    "description": get_column_description(table_name, col_name),
                    "sample_value": (
                        sample_rows[0].get(col_name) if sample_rows else None
                    ),
                }
            )

        tables.append(
            {
                "table_name": table_name,
                "description": TABLE_DESCRIPTIONS.get(table_name, ""),
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


def _get_row_count(engine: Any, table_name: str) -> int:
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            return int(result.scalar_one())
    except Exception:
        return -1


def export_metadata_json(path: Path | None = None) -> Path:
    """导出元数据 JSON 到 outputs/metadata.json。"""
    output_path = path or (OUTPUT_DIR / "metadata.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = get_metadata_json()
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


if __name__ == "__main__":
    export_metadata_json()
    print(f"metadata exported: {OUTPUT_DIR / 'metadata.json'}")
