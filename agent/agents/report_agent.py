"""报告生成模块。

基于已经查询到的 SQL、列名、数据行和文字分析结论，调用大模型生成 Markdown 报告。
报告生成不是复杂 Agent 循环，只使用简单的 LLM 调用。
"""

from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from core.llm import get_chat_llm
from core.metrics import get_metrics_text
from core.prompts import REPORT_SYSTEM_PROMPT
from schemas.agent_io import AgentResult


class ReportGenerator:
    """根据 AgentResult 生成 Markdown 分析报告。"""

    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm or get_chat_llm()
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", REPORT_SYSTEM_PROMPT),
                (
                    "human",
                    (
                        "请根据以下信息生成报告。\n\n"
                        "用户问题：{question}\n"
                        "执行的 SQL：{sql}\n"
                        "查询列名：{columns}\n"
                        "查询数据（最多展示 50 行）：\n{rows}\n"
                        "文字分析结论：{analysis_text}"
                    ),
                ),
            ]
        )

    @staticmethod
    def _format_rows(result: AgentResult, limit: int = 50) -> str:
        if not result.columns:
            return "无查询结果"

        header = " | ".join(str(col) for col in result.columns)
        lines = [header, "---"]
        for row in result.rows[:limit]:
            lines.append(" | ".join("" if v is None else str(v) for v in row))
        return "\n".join(lines)

    def generate(self, result: AgentResult) -> str:
        """生成报告并返回 Markdown 文本。"""
        metrics_text = get_metrics_text()

        chain = self._prompt | self.llm
        response = chain.invoke(
            {
                "question": result.question,
                "sql": result.sql or "无 SQL",
                "columns": ", ".join(result.columns),
                "rows": self._format_rows(result),
                "analysis_text": result.analysis_text,
                "metrics": metrics_text,
            }
        )

        # ChatModel.invoke 返回的消息对象
        if hasattr(response, "content"):
            return response.content or ""
        return str(response)
