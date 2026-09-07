"""元数据文本格式化。

把 metadata_service.get_metadata_json() 产生的统一 JSON
格式化成 Agent Prompt 可读的文本，避免直接读取数据库拼文本。
"""

from typing import Any


def format_data_resources(metadata_json: dict[str, Any]) -> str:
    """把元数据 JSON 格式化成 Agent Prompt 可读的数据资源概览。"""
    lines = []
    for table in metadata_json.get("tables", []):
        table_name = table["table_name"]
        description = table.get("description", "")
        lines.append(f"- {table_name}: {description}")
        for column in table.get("columns", []):
            col_desc = column.get("description", "")
            sample = column.get("sample_value")
            sample_text = f"，样例: {sample}" if sample is not None else ""
            if col_desc:
                lines.append(f"  - {column['name']}: {col_desc}{sample_text}")
            else:
                lines.append(f"  - {column['name']}: {column['type']}{sample_text}")

    if metadata_json.get("relationships"):
        lines.append("\n表间关系:")
        for rel in metadata_json["relationships"]:
            lines.append(
                f"- {rel['source_table']}.{rel['source_column']} "
                f"-> {rel['target_table']}.{rel['target_column']}"
                f" ({rel.get('relation_type', '')})"
            )
    return "\n".join(lines)
