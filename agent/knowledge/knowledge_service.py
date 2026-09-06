"""知识模型服务。

把静态业务知识与实际 metadata JSON 结合，
输出“已解析到真实表/字段”的知识 JSON，供 Agent 和前端知识图谱使用。
"""

import json
from pathlib import Path
from typing import Any

from core.config import BASE_DIR
from knowledge.knowledge_base import (
    ANALYSIS_THEMES,
    BUSINESS_OBJECTS,
    BUSINESS_RULES,
)
from metadata.metadata_service import get_metadata_json

OUTPUT_DIR = BASE_DIR / "outputs"


def _normalize(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
    )


def _build_available_columns(metadata: dict[str, Any]) -> dict[str, list[str]]:
    """返回 { table_name: [column_name, ...] }。"""
    available: dict[str, list[str]] = {}
    for table in metadata.get("tables", []):
        table_name = table["table_name"]
        available[table_name] = [
            column["name"] for column in table.get("columns", [])
        ]
    return available


def resolve_knowledge(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """根据真实元数据解析业务知识到实际表/字段。"""
    metadata = metadata or get_metadata_json()
    table_columns = _build_available_columns(metadata)
    existing_tables = set(table_columns.keys())

    # 解析业务对象：默认表存在则保留
    resolved_objects = []
    for obj in BUSINESS_OBJECTS:
        if obj["default_table"] in existing_tables:
            resolved_objects.append(obj)

    # 解析业务规则：在 prefer_table 中优先匹配 candidate_fields
    resolved_rules = []
    for rule in BUSINESS_RULES:
        matched = None
        candidates = [_normalize(c) for c in rule["candidate_fields"]]

        prefer_table = rule.get("prefer_table")
        if prefer_table in table_columns:
            for col in table_columns[prefer_table]:
                if _normalize(col) in candidates:
                    matched = (prefer_table, col)
                    break

        # 如果 prefer_table 没找到，则从其他表里找
        if matched is None:
            for table_name, cols in table_columns.items():
                for col in cols:
                    if _normalize(col) in candidates:
                        matched = (table_name, col)
                        break
                if matched:
                    break

        resolved_rule = dict(rule)
        if matched:
            resolved_rule["mapped_table"] = matched[0]
            resolved_rule["mapped_field"] = matched[1]
            resolved_rule["resolved_calculation"] = rule["calculation"].format(
                field=matched[1]
            )
        else:
            resolved_rule["mapped_table"] = None
            resolved_rule["mapped_field"] = None
            resolved_rule["resolved_calculation"] = "当前数据源缺少该指标字段"
        resolved_rules.append(resolved_rule)

    # 解析主题：只保留有实际表支撑的主题
    resolved_themes = []
    for theme in ANALYSIS_THEMES:
        related_tables = [
            table
            for table in theme.get("related_tables", [])
            if table in existing_tables
        ]
        resolved_themes.append({**theme, "related_tables": related_tables})

    return {
        "schema_version": "1.0",
        "themes": resolved_themes,
        "objects": resolved_objects,
        "rules": resolved_rules,
    }


def export_knowledge_json(path: Path | None = None) -> Path:
    """导出解析后的知识 JSON 到 outputs/knowledge.json。"""
    output_path = path or (OUTPUT_DIR / "knowledge.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = resolve_knowledge()
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


if __name__ == "__main__":
    export_knowledge_json()
    print(f"knowledge exported: {OUTPUT_DIR / 'knowledge.json'}")
