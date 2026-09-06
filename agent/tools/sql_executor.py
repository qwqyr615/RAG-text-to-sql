"""SQL 执行工具。

目前只允许执行只读 SELECT，避免 Agent 对数据造成破坏。
"""

import re
from typing import Any

from sqlalchemy import text

from tools.database import get_engine


def validate_readonly_sql(sql: str) -> str:
    """简单校验 SQL 是否为只读查询。

    后续可以替换成更严格的 SQL Parser 或数据库只读账号。
    """
    normalized = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    normalized = re.sub(r"/\*.*?\*/", "", normalized, flags=re.DOTALL)
    normalized = normalized.strip().rstrip(";").strip()

    first_keyword = normalized.split(maxsplit=1)[0].upper() if normalized else ""
    if first_keyword not in {"SELECT", "WITH"}:
        raise ValueError("仅允许执行 SELECT 或只读 WITH 查询")

    dangerous = re.findall(
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if dangerous:
        raise ValueError("检测到非只读 SQL 操作")

    return normalized


def execute_sql(sql: str, limit: int = 200) -> tuple[list[str], list[list[Any]]]:
    """执行 SQL 并返回 (列名, 行数据)。

    参数:
        sql: 只读 SELECT 语句
        limit: 最多返回多少行，防止结果过大
    """
    readonly_sql = validate_readonly_sql(sql)
    with get_engine().connect() as conn:
        result = conn.execute(text(readonly_sql))
        columns = list(result.keys())
        rows = [list(row) for row in result.fetchmany(limit)]
    return columns, rows
