"""大模型实例工厂。

当前默认使用 LangChain 官方 DeepSeek 集成。
以后如果换成 Qwen / ChatGLM / Ollama，只需要在这里增加对应工厂函数或切换供应商，
上层 Agent 不需要修改。
"""

from functools import lru_cache

from langchain_deepseek import ChatDeepSeek

from core.config import settings


@lru_cache(maxsize=1)
def get_chat_llm() -> ChatDeepSeek:
    """返回全局复用的大模型实例。"""
    return ChatDeepSeek(
        model=settings.llm_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout,
    )


def get_llm_by_provider(provider: str = "deepseek"):
    """
    根据配置创建模型，便于后续扩展
    当前只实现 DeepSeek 官方 LangChain 集成。
    """
    if provider == "deepseek":
        return get_chat_llm()

    # TODO: 如需 Qwen / ChatGLM / Ollama，请在这里增加对应的 LangChain ChatModel
    raise ValueError(f"暂不支持的大模型提供商: {provider}")