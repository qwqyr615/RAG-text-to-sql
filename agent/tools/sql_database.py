"""在 SQL 真正执行处做只读校验的 ``SQLDatabase``。

**为什么必须在这一层**

LangChain 的 ``sql_db_query`` / ``sql_db_schema`` 工具最终都调用
``SQLDatabase.run()`` / ``SQLDatabase._execute()``。只在 ``tools/sql_executor``
里校验，只能拦住「Agent 跑完之后的回放取数」，拦不住模型通过工具调用真正执行的
写操作。把守卫挂在这里，模型生成的 ``DROP TABLE`` / ``UPDATE`` 会在执行前被拒绝。
"""

from __future__ import annotations

from typing import Any

from langchain_community.utilities.sql_database import SQLDatabase

from tools.sql_guard import validate_readonly_sql

__all__ = ["ReadOnlySQLDatabase"]


class ReadOnlySQLDatabase(SQLDatabase):
    """所有执行入口都先过只读校验的 ``SQLDatabase``。

    校验失败抛 ``ReadOnlyViolation``，AgentExecutor 会把它当作工具报错写入观测
    结果，模型可据此重写查询（第 5 条工作流约束）。
    """

    def _guard(self, command: Any) -> Any:
        """对字符串 SQL 做只读校验；SQLAlchemy 表达式交给调用方负责。"""
        if isinstance(command, str):
            return validate_readonly_sql(command)
        return command

    def run(
        self,
        command: Any,
        fetch: str = "all",
        include_columns: bool = False,
        *,
        parameters: Any = None,
        execution_options: Any = None,
    ) -> Any:
        """``sql_db_query`` 工具的执行入口。"""
        return super().run(
            self._guard(command),
            fetch,  # type: ignore[arg-type]
            include_columns,
            parameters=parameters,
            execution_options=execution_options,
        )

    def _execute(
        self,
        command: Any,
        fetch: str = "all",
        *,
        parameters: Any = None,
        execution_options: Any = None,
    ) -> Any:
        """底层执行入口（``get_table_info`` 取样例数据也走这里）。"""
        return super()._execute(
            self._guard(command),
            fetch,  # type: ignore[arg-type]
            parameters=parameters,
            execution_options=execution_options,
        )
