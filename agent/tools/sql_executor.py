"""SQL 执行工具（Agent 完成后的结果回放取数）。

Agent 真正执行 SQL 的路径是 LangChain 的 ``sql_db_query`` 工具，见
``tools/sql_database.ReadOnlySQLDatabase``；本模块负责在 Agent 给出 SQL 之后，再用
只读引擎取一次**结构化结果**（列名 + 数据行）供前端展示——因为 LangChain 回灌给模型
的观测结果是截断后的字符串，前端需要的是干净的表格数据。

两条路径共用 ``tools/sql_guard`` 的同一套只读校验。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from core.config import settings
from tools.database import get_readonly_engine
from tools.sql_guard import ReadOnlyViolation, validate_readonly_sql

__all__ = ["execute_sql", "validate_readonly_sql", "ReadOnlyViolation"]


def execute_sql(
    sql: str, limit: int | None = None
) -> tuple[list[str], list[list[Any]]]:
    """执行只读 SQL 并返回 ``(列名, 行数据)``。

    参数:
        sql: 只读 SELECT / WITH 语句；非只读语句会抛 ``ReadOnlyViolation``
        limit: 最多返回多少行；默认取 ``settings.sql_result_row_limit``
    """
    readonly_sql = validate_readonly_sql(sql)
    effective_limit = settings.sql_result_row_limit if limit is None else int(limit)

    with get_readonly_engine().connect() as conn:
        result = conn.execute(text(readonly_sql))
        columns = list(result.keys())
        rows = [list(row) for row in result.fetchmany(effective_limit)]
    return columns, rows
