"""Text-to-SQL 智能体。

使用 LangChain 1.2.12 + langchain-community 提供的官方 SQL Agent：
    create_sql_agent(...)

由 LangChain 自己完成：工具选择、SQL 生成、执行、错误重试、Agent 循环。
这里不手动实现复杂 Agent 内容。

为了向前端/上层返回结构化结果，本模块会从 Agent 的中间步骤中提取实际执行的 SQL，
并使用只读执行器获取列名和数据行。
"""

import json
from typing import Any

from langchain_community.agent_toolkits.sql.base import create_sql_agent
from langchain_community.utilities.sql_database import SQLDatabase

from core.config import settings
from core.llm import get_llm_by_provider
from core.metrics import get_metrics_text
from schemas.agent_io import AgentQuestion, AgentResult
from tools.database import get_engine
from tools.sql_executor import execute_sql


class Text2SQLAgent:
    """基于 LangChain 官方 SQL Agent 的自然语言查询 Agent。"""

    def __init__(self) -> None:
        self.llm = get_llm_by_provider()
        self.db = SQLDatabase(
            engine=get_engine(),
            sample_rows_in_table_info=settings.sql_sample_rows,
        )
        # 官方高层 Agent：自动循环调用工具、生成 SQL、查询数据库
        # return_intermediate_steps=True 可以让我们拿到 Agent 实际执行过的工具动作
        prefix = (
            "你是一个企业数据底座智能问析 SQL Agent。"
            "请根据业务问题自动查询数据库，并使用中文回答用户。\n\n"
            "可参考的业务指标口径如下：\n"
            f"{get_metrics_text()}\n\n"
            "注意事项：\n"
            "1. 只能使用数据库中实际存在的表和字段。\n"
            "2. 涉及指标分析时，请优先参考上述业务指标口径。\n"
            "3. 所有 SQL 必须是只读 SELECT 查询。\n"
            "4. 最终回答请使用中文。"
        )
        self.agent = create_sql_agent(
            llm=self.llm,
            db=self.db,
            agent_type="tool-calling",
            verbose=True,
            prefix=prefix,
            agent_executor_kwargs={"return_intermediate_steps": True},
        )

    @staticmethod
    def _extract_sql_from_steps(intermediate_steps: list[Any] | None) -> str:
        """从 Agent 中间步骤中提取真正执行查询的 SQL。"""
        if not intermediate_steps:
            return ""

        # 倒序查找最近一次真正的数据库查询动作
        for action, _ in reversed(intermediate_steps):
            tool_name = getattr(action, "tool", None)
            if tool_name != "sql_db_query":
                continue

            tool_input = getattr(action, "tool_input", None)
            sql = Text2SQLAgent._parse_tool_input(tool_input)
            if sql:
                return sql

        return ""

    @staticmethod
    def _parse_tool_input(tool_input: Any) -> str:
        """解析 sql_db_query 工具的入参，可能是 dict、JSON 字符串或普通字符串。"""
        if isinstance(tool_input, dict):
            return str(tool_input.get("query") or tool_input.get("sql") or "").strip()

        if isinstance(tool_input, str):
            text = tool_input.strip()
            if text.startswith("{"):
                try:
                    data = json.loads(text)
                    return str(data.get("query") or data.get("sql") or "").strip()
                except json.JSONDecodeError:
                    pass
            return text

        return str(tool_input or "").strip()

    def ask(self, question: AgentQuestion | str) -> AgentResult:
        """把用户问题交给 LangChain SQL Agent 处理，并返回结构化结果。"""
        if isinstance(question, str):
            question = AgentQuestion(question=question)

        result = AgentResult(question=question.question)

        try:
            response = self.agent.invoke({"input": question.question})

            if isinstance(response, dict):
                result.analysis_text = response.get("output", "") or "Agent 未返回分析结果。"
                sql = self._extract_sql_from_steps(response.get("intermediate_steps"))
            else:
                result.analysis_text = str(response)

            if sql:
                result.sql = sql
                columns, rows = execute_sql(sql)
                result.columns = columns
                result.rows = rows
        except Exception as exc:  # noqa: BLE001
            result.success = False
            result.error = str(exc)

        return result