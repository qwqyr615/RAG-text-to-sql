"""SiliconFlow 嵌入模型工厂。

统一从 .env 读取 SiliconFlow API 配置，供 RAG 向量检索使用。
"""

from functools import lru_cache

from langchain_openai import OpenAIEmbeddings

from core.config import settings


@lru_cache(maxsize=1)
def get_embedding_model() -> OpenAIEmbeddings:
    """返回全局复用的 SiliconFlow 嵌入模型。"""
    if not settings.siliconflow_api_key:
        raise ValueError("缺少 SILICONFLOW_API_KEY，请在 .env 中配置")

    return OpenAIEmbeddings(
        model=settings.siliconflow_embedding_model,
        api_key=settings.siliconflow_api_key,
        base_url=settings.siliconflow_base_url,
        check_embedding_ctx_length=False,
        chunk_size=32,
        request_timeout=settings.llm_timeout,
    )