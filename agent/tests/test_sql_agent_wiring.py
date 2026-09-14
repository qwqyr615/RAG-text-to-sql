"""SQL Agent 装配测试。

验证三件事（用脚本化的假 ChatModel，不需要 API Key 与网络）：

1. 四段上下文确实被注入到 system prompt（``{context_block}`` 变量打通）；
2. 模型发起的写操作被拦在**真实执行路径**上（工具调用阶段），且没有落库；
3. 只读 SELECT 能正常走完「工具调用 → 观测 → 最终回答」的循环。
"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from agents.text2sql_agent import create_readonly_sql_agent
from prompt.base import PromptContext
from prompt.builder import SQLAgentPromptBuilder
from prompt.providers import (
    DataResourceProvider,
    KnowledgeProvider,
    MetricsProvider,
    RagExampleProvider,
)
from tools.sql_database import ReadOnlySQLDatabase

SAMPLE_SQL = "SELECT AVG(defect_rate) FROM fact_production_record"


class ScriptedToolCallingModel(BaseChatModel):
    """按脚本返回消息的假模型；工具调用由脚本直接给出。"""

    responses: List[AIMessage] = Field(default_factory=list)
    seen_messages: List[List[Any]] = Field(default_factory=list)
    cursor: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-calling"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedToolCallingModel":
        return self

    def _generate(
        self,
        messages: List[Any],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen_messages.append(list(messages))
        index = min(self.cursor, len(self.responses) - 1)
        self.cursor += 1
        return ChatResult(
            generations=[ChatGeneration(message=self.responses[index])]
        )


@pytest.fixture()
def sqlite_engine(sqlite_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{sqlite_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE fact_production_record ("
                "record_id INTEGER PRIMARY KEY, defect_rate FLOAT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO fact_production_record (record_id, defect_rate) "
                "VALUES (1, 0.02), (2, 0.05)"
            )
        )
    yield engine
    engine.dispose()


@pytest.fixture()
def db(sqlite_engine: Engine) -> ReadOnlySQLDatabase:
    return ReadOnlySQLDatabase(
        engine=sqlite_engine, include_tables=["fact_production_record"]
    )


def _make_builder() -> SQLAgentPromptBuilder:
    return SQLAgentPromptBuilder(
        [
            DataResourceProvider(2000),
            MetricsProvider(1200),
            KnowledgeProvider(1200),
            RagExampleProvider(
                800,
                top_k=2,
                min_score=0.0,
                search_fn=lambda question, *, k, min_score: [
                    {
                        "question": "各产线缺陷率",
                        "sql": SAMPLE_SQL,
                        "score": 0.9,
                        "tables": "fact_production_record",
                    }
                ],
            ),
        ],
        total_budget=6000,
    )


def _calling_model(responses: List[AIMessage]) -> ScriptedToolCallingModel:
    return ScriptedToolCallingModel(responses=responses)


def _count_rows(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT COUNT(*) FROM fact_production_record")
            ).scalar_one()
        )


def test_context_block_is_injected_into_system_prompt(
    db: ReadOnlySQLDatabase, make_context: Callable[..., PromptContext]
) -> None:
    llm = _calling_model(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sql_db_query",
                        "args": {"query": SAMPLE_SQL},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="平均缺陷率约为 0.035。"),
        ]
    )
    builder = _make_builder()
    agent = create_readonly_sql_agent(
        llm=llm,
        db=db,
        prompt_builder=builder,
        top_k=50,
        max_iterations=3,
        max_execution_time=30,
        verbose=False,
    )

    build = builder.build_context(make_context("各产线缺陷率是多少"))
    result = agent.invoke(
        {"input": "各产线缺陷率是多少", "context_block": build.context_block}
    )

    system_content = llm.seen_messages[0][0].content

    # 静态工作流约束
    assert "工作流约束" in system_content
    assert "sql_db_schema" in system_content
    assert "只读查询" in system_content
    # 四段动态上下文
    assert "数据资源" in system_content
    assert "业务指标口径" in system_content
    assert "业务知识" in system_content
    assert "相似问题与 SQL 示例" in system_content
    assert SAMPLE_SQL in system_content

    # 正常跑完循环
    assert result["output"] == "平均缺陷率约为 0.035。"
    steps = result["intermediate_steps"]
    assert steps and steps[0][0].tool == "sql_db_query"
    assert "0.035" in str(steps[0][1])


def test_write_tool_call_is_blocked_before_execution(
    db: ReadOnlySQLDatabase,
    sqlite_engine: Engine,
    make_context: Callable[..., PromptContext],
) -> None:
    llm = _calling_model(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sql_db_query",
                        "args": {"query": "DROP TABLE fact_production_record"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="该操作被系统拒绝。"),
        ]
    )
    builder = _make_builder()
    agent = create_readonly_sql_agent(
        llm=llm,
        db=db,
        prompt_builder=builder,
        max_iterations=3,
        max_execution_time=30,
        verbose=False,
    )
    build = builder.build_context(make_context("各产线缺陷率是多少"))

    result = agent.invoke(
        {"input": "把生产事实表删掉", "context_block": build.context_block}
    )
    observations = " ".join(
        str(step[1]) for step in result.get("intermediate_steps", [])
    )

    # 只读违规被转成「可恢复的工具错误」：整次提问不会失败，模型能看到原因后重写
    assert "只读" in observations
    # 关键断言：写操作没有落库
    assert _count_rows(sqlite_engine) == 2


def test_context_block_is_a_required_prompt_variable(
    db: ReadOnlySQLDatabase,
) -> None:
    """缺少 ``context_block`` 会立即报错，而不是静默降级成空上下文。"""
    llm = _calling_model([AIMessage(content="ok")])
    agent = create_readonly_sql_agent(
        llm=llm, db=db, prompt_builder=_make_builder(), verbose=False
    )
    with pytest.raises(Exception):
        agent.invoke({"input": "各产线缺陷率是多少"})
