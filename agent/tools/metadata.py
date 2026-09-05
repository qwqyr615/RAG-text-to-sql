"""数据库元数据读取工具。

用于把数据库中的表、字段、类型等信息提取成文本，提供给 LLM 生成 SQL。
后续可以补充字段注释、样例值、表关系、业务知识，增强 Text-to-SQL 准确率。
"""

from sqlalchemy import inspect

from tools.database import get_engine


def build_metadata_text() -> str:
    """自动读取数据库结构并生成供 LLM 使用的元数据文本。"""
    inspector = inspect(get_engine())
    lines: list[str] = []

    for table_name in inspector.get_table_names():
        lines.append(f"表名: {table_name}")
        try:
            columns = inspector.get_columns(table_name)
        except Exception:
            continue

        for col in columns:
            col_name = col["name"]
            col_type = str(col.get("type", ""))
            comment = col.get("comment", "") or ""
            lines.append(f"  - {col_name} ({col_type}) {comment}".rstrip())

        lines.append("")

    return "\n".join(lines).strip()


def get_table_names() -> list[str]:
    """返回当前数据库中的所有表名。"""
    inspector = inspect(get_engine())
    return inspector.get_table_names()
