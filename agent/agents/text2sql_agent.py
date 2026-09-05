"""Text-to-SQL 智能体。

使用 LangChain 1.2.12 + langchain-community 提供的官方 SQL Agent：
    create_sql_agent(...)

由 LangChain 自己完成：工具选择、SQL 生成、执行、错误重试、Agent 循环。
这里不手动实现复杂 Agent 内容。
"""

from langchain_community.agent_toolkits.sql.base import create_sql_agent
from langchain_community.utilities.sql_database import SQLDatabase

from core.config import settings
from core.llm import get_llm_by_provider
from schemas.agent_io import AgentQuestion, AgentResult
from tools.database import get_engine


class Text2SQLAgent:
    """基于 LangChain 官方 SQL Agent 的自然语言查询 Agent。"""

    def __init__(self) -> None:
        self.llm = get_llm_by_provider()
        self.db = SQLDatabase(
            engine=get_engine(),
            sample_rows_in_table_info=settings.sql_sample_rows,
        )
        # 官方高层 Agent：自动循环调用工具、生成 SQL、查询数据库
        self.agent = create_sql_agent(
            llm=self.llm,
            db=self.db,
            agent_type="tool-calling",
            verbose=True,
        )

    def ask(self, question: AgentQuestion | str) -> AgentResult:
        """把用户问题交给 LangChain SQL Agent 处理。"""
        if isinstance(question, str):
            question = AgentQuestion(question=question)

        result = AgentResult(question=question.question)

        try:
            response = self.agent.invoke({"input": question.question})
            output = response.get("output", "") if isinstance(response, dict) else str(response)
            result.analysis_text = output or "Agent 未返回分析结果。"
        except Exception as exc:  # noqa: BLE001
            result.success = False
            result.error = str(exc)

        return result