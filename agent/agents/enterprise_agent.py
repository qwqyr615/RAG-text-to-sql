"""企业数据底座智能问析总 Agent。

负责把用户自然语言问题路由到：
- Text2SQLAgent：数据查询、统计分析、指标分析
- Text2SQLAgent + ReportGenerator：报告生成
- IsolationForest：异常检测
- LinearRegression：简单回归预测

同时保留 metadata / knowledge 上下文，供后续 FastAPI 接口统一暴露。
问题带 ``session_id`` 传入，多轮追问的历史由 Text2SQLAgent 维护。
"""

from typing import Any

from agents.text2sql_agent import DEFAULT_SESSION_ID, Text2SQLAgent
from knowledge.knowledge_service import resolve_knowledge
from metadata.metadata_service import get_metadata_json
from schemas.agent_io import AgentQuestion
from sessions import SessionStore
from tools.modeling import run_anomaly_detection, run_linear_regression


class EnterpriseAgent:
    """统一入口 Agent，后续 Java/前端只需要调用这一个服务。"""

    def __init__(
        self,
        session_store: SessionStore | None = None,
        llm: Any | None = None,
    ) -> None:
        self.sql_agent = Text2SQLAgent(session_store=session_store, llm=llm)
        self.metadata = self.sql_agent.metadata_json
        self.knowledge = resolve_knowledge(self.metadata)

    def get_metadata(self) -> dict[str, Any]:
        """返回当前数据资源元数据 JSON。"""
        return self.metadata

    def get_knowledge(self) -> dict[str, Any]:
        """返回当前业务知识 JSON。"""
        return self.knowledge

    def reset_session(self, session_id: str = DEFAULT_SESSION_ID) -> None:
        """清空某个会话的历史。"""
        self.sql_agent.reset_session(session_id)

    def handle(
        self, question: str, session_id: str = DEFAULT_SESSION_ID
    ) -> dict[str, Any]:
        """根据用户问题路由到对应的分析能力。

        返回统一结构：
            {"type": "agent" | "model" | "error", "result": ...}
        """
        try:
            if "异常" in question or "离群" in question or "outlier" in question.lower():
                return {"type": "model", "result": run_anomaly_detection()}

            if "回归" in question or "预测" in question:
                target = self._select_regression_target(question)
                return {
                    "type": "model",
                    "result": run_linear_regression(target=target),
                }

            if "报告" in question:
                return {
                    "type": "agent",
                    "result": self.sql_agent.ask_with_report(
                        AgentQuestion(question=question, session_id=session_id)
                    ),
                }

            return {
                "type": "agent",
                "result": self.sql_agent.ask(
                    AgentQuestion(question=question, session_id=session_id)
                ),
            }
        except Exception as exc:  # noqa: BLE001
            return {"type": "error", "result": str(exc)}

    @staticmethod
    def _select_regression_target(question: str) -> str:
        if "质量" in question:
            return "quality_score"
        if "停机" in question:
            return "downtime_minutes"
        return "defect_rate"
