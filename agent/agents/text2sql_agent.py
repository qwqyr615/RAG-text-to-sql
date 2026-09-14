"""Text-to-SQL 智能体。

使用 LangChain 1.2.12 + langchain-community 提供的官方 SQL Agent：
    create_sql_agent(...)  + agent_type="tool-calling"

由 LangChain 自己完成：工具选择、SQL 生成、执行、错误重试、Agent 循环。
这里不手动实现复杂 Agent 内容。

本模块在官方 SQL Agent 之上补了三件事：

1. **执行期只读守卫**：使用 ``ReadOnlySQLDatabase``（见 ``tools/sql_database.py``），
   模型生成的写操作在**真正执行前**被拦截，而不是只在事后回放取数时校验。违规会以
   「工具错误」形式回灌给模型（``ReadOnlyViolation`` 继承 ``SQLAlchemyError``），
   模型可以据此重写查询。
2. **四段动态 Prompt**：元数据 / 指标 / 知识 / RAG 示例四个 provider 各自裁剪、
   各自持有字符预算，通过 system prompt 的 ``{context_block}`` 注入。
3. **循环与工具预算**：``max_iterations`` / ``max_execution_time`` / ``top_k``
   全部由 ``core.config`` 控制。

为了向前端/上层返回结构化结果，本模块会从 Agent 的中间步骤中提取实际执行的 SQL，
并使用只读执行器获取列名和数据行。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_community.agent_toolkits.sql.base import create_sql_agent
from langchain_community.agent_toolkits.sql.prompt import SQL_FUNCTIONS_SUFFIX
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from agents.report_agent import ReportGenerator
from core.config import settings
from core.llm import get_llm_by_provider
from knowledge.knowledge_service import resolve_knowledge
from metadata.metadata_service import get_metadata_json
from prompt.base import PromptContext
from prompt.builder import PromptBuildResult, SQLAgentPromptBuilder
from schemas.agent_io import AgentQuestion, AgentResult
from tools.database import get_readonly_engine
from tools.sql_database import ReadOnlySQLDatabase
from tools.sql_executor import execute_sql

logger = logging.getLogger(__name__)


def create_readonly_sql_agent(
    *,
    llm: Any,
    db: ReadOnlySQLDatabase,
    prompt_builder: SQLAgentPromptBuilder,
    top_k: int | None = None,
    max_iterations: int | None = None,
    max_execution_time: float | None = None,
    verbose: bool | None = None,
) -> Any:
    """组装一个带只读守卫与四段 Prompt 的 SQL Agent。

    提示词结构（与 LangChain 官方 tool-calling 版一致，只是换成我们自己的模板）：

    - system：``core.prompts.SQL_AGENT_SYSTEM_TEMPLATE``，含工作流约束与
      ``{context_block}``（每次请求动态填充）；
    - human：用户问题；
    - ai：LangChain 官方的 ``SQL_FUNCTIONS_SUFFIX``，保持工具调用行为不变；
    - agent_scratchpad：工具调用轨迹占位符（tool-calling Agent 必需）。
    """
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", prompt_builder.system_template()),
            ("human", "{input}"),
            ("ai", SQL_FUNCTIONS_SUFFIX),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )

    return create_sql_agent(
        llm=llm,
        db=db,
        agent_type="tool-calling",
        prompt=prompt,
        top_k=settings.sql_agent_top_k if top_k is None else top_k,
        max_iterations=(
            settings.sql_agent_max_iterations
            if max_iterations is None
            else max_iterations
        ),
        max_execution_time=(
            settings.sql_agent_max_execution_time
            if max_execution_time is None
            else max_execution_time
        ),
        verbose=settings.sql_agent_verbose if verbose is None else verbose,
        agent_executor_kwargs={
            "return_intermediate_steps": True,
            "handle_parsing_errors": True,
        },
    )


class Text2SQLAgent:
    """基于 LangChain 官方 SQL Agent 的自然语言查询 Agent。"""

    def __init__(
        self,
        metadata_json: dict[str, Any] | None = None,
        prompt_builder: SQLAgentPromptBuilder | None = None,
    ) -> None:
        self.llm = get_llm_by_provider()

        # 先读取当前数据源元数据，动态限定 Agent 可使用的预置业务表
        self.metadata_json = metadata_json or get_metadata_json()
        business_tables = [
            table["table_name"] for table in self.metadata_json.get("tables", [])
        ]
        if not business_tables:
            # 白名单为空必须显式失败：否则 include_tables=None 会让 Agent 看到全部表
            raise RuntimeError(
                "未从元数据中解析到任何业务表，拒绝在缺少表白名单的情况下启动 SQL Agent。"
                "请先执行 scripts/init_preset_schema.py，或检查 DATABASE_URL 配置。"
            )

        # 只读引擎 + 只读 SQLDatabase：写操作在执行前被拒绝
        self.db = ReadOnlySQLDatabase(
            engine=get_readonly_engine(),
            include_tables=business_tables,
            sample_rows_in_table_info=settings.sql_sample_rows,
        )

        self.available_columns = self._get_available_columns(self.metadata_json)
        self.knowledge = resolve_knowledge(self.metadata_json)

        # 四段 Prompt：元数据 / 指标 / 知识 / RAG 示例，各段自带预算
        self.prompt_builder = prompt_builder or SQLAgentPromptBuilder.from_settings()

        self.agent = create_readonly_sql_agent(
            llm=self.llm,
            db=self.db,
            prompt_builder=self.prompt_builder,
        )

        logger.info(
            "Text2SQLAgent 就绪：业务表 %s 张，Prompt 总预算 %s 字符，"
            "工具循环上限 %s 次，执行时限 %s 秒",
            len(business_tables),
            settings.prompt_total_budget,
            settings.sql_agent_max_iterations,
            settings.sql_agent_max_execution_time,
        )

    @staticmethod
    def _get_available_columns(metadata_json: dict[str, Any]) -> list[str]:
        """从 metadata JSON 中获取当前业务表的所有可用字段名。"""
        columns: list[str] = []
        for table in metadata_json.get("tables", []):
            columns.extend(column["name"] for column in table.get("columns", []))
        return columns

    def build_context(self, question: str) -> PromptBuildResult:
        """按当前问题构建四段上下文（含各自的裁剪与预算）。"""
        context = PromptContext(
            question=question,
            metadata_json=self.metadata_json,
            knowledge=self.knowledge,
            available_columns=self.available_columns,
        )
        return self.prompt_builder.build_context(context)

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

        # 把可选的 metadata / business_rules 补充到当次问题中，
        # 这样既能使用官方 SQL Agent 自动读取表结构，也能额外传入业务上下文。
        extra_context = []
        if question.metadata:
            extra_context.append(f"补充数据资源说明：\n{question.metadata}")
        if question.business_rules:
            extra_context.append(f"本次业务规则/口径：\n{question.business_rules}")

        user_input = question.question
        if extra_context:
            user_input += "\n\n" + "\n\n".join(extra_context)

        # 四段动态上下文：元数据 / 指标 / 知识 / RAG 示例
        build = self._safe_build_context(question.question)
        result.prompt_usage = build.usage()
        rag_section = build.section("rag")
        result.rag_context = (
            rag_section.content
            if rag_section is not None and not rag_section.is_empty
            else ""
        )
        logger.info("Prompt 段用量：%s", build.summary())

        try:
            response = self.agent.invoke(
                {"input": user_input, "context_block": build.context_block}
            )
        except Exception as exc:  # noqa: BLE001
            result.success = False
            result.error = str(exc)
            return result

        sql = ""
        if isinstance(response, dict):
            result.analysis_text = response.get("output", "") or "Agent 未返回分析结果。"
            sql = self._extract_sql_from_steps(response.get("intermediate_steps"))
        else:
            result.analysis_text = str(response)

        if not sql:
            return result

        # SQL 已经由 Agent 通过只读守卫执行过一次；这里只读回放一次，拿到结构化
        # 结果（列名 + 数据行）供前端展示。回放失败不影响上面的分析结论。
        result.sql = sql
        try:
            result.columns, result.rows = execute_sql(sql)
        except Exception as exc:  # noqa: BLE001
            result.sql_error = str(exc)
            logger.warning("结果回放取数失败（分析结论已保留）：%s", exc)

        return result

    def _safe_build_context(self, question: str) -> PromptBuildResult:
        """Prompt 组装失败不应该阻断查询。"""
        try:
            return self.build_context(question)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Prompt 上下文构建失败，降级为空上下文：%s", exc)
            return PromptBuildResult(
                context_block="", sections=[], total_budget=0, used_chars=0
            )

    def ask_with_report(self, question: AgentQuestion | str) -> AgentResult:
        """执行 SQL 查询后自动生成 Markdown 分析报告。"""
        result = self.ask(question)
        if not result.success:
            return result

        try:
            generator = ReportGenerator(self.llm)
            result.report = generator.generate(result)
        except Exception as exc:  # noqa: BLE001
            result.report = (
                f"报告生成失败：{exc}\n\n"
                f"以下为基础分析结论：\n{result.analysis_text}"
            )

        return result
