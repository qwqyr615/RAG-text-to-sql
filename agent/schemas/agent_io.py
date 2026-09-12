"""Agent 输入输出数据结构。"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class AgentQuestion(BaseModel):
    """用户提问请求。"""

    question: str = Field(..., description="用户自然语言问题")
    session_id: str = Field(default="default", description="会话 ID，便于后续多轮对话")
    metadata: str = Field(default="", description="数据资源元数据描述，可由外部传入")
    business_rules: str = Field(default="", description="业务指标/口径说明，可由外部传入")


class AgentResult(BaseModel):
    """Agent 处理结果。"""

    question: str = Field(default="", description="原始问题")
    sql: Optional[str] = Field(default=None, description="生成的 SQL")
    columns: list[str] = Field(default_factory=list, description="结果列名")
    rows: list[list[Any]] = Field(default_factory=list, description="结果数据")
    chart_config: Optional[dict[str, Any]] = Field(default=None, description="图表配置")
    analysis_text: str = Field(default="", description="文字分析结论")
    rag_context: Optional[str] = Field(default=None, description="RAG检索上下文")
    report: Optional[str] = Field(default=None, description="生成的 Markdown 分析报告")
    error: Optional[str] = Field(default=None, description="错误信息")
    success: bool = Field(default=True, description="是否成功")
