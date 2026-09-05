"""全局配置模块。

所有环境变量统一从 agent/.env 中读取，不要在业务代码中硬编码 API Key / Base URL / 模型名。
使用方法：
    from core.config import settings
    print(settings.deepseek_api_key)
"""

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# agent/ 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent

# 将 .env 中的配置加载到系统环境变量，便于 LangChain / LangSmith 读取
load_dotenv(BASE_DIR / ".env")


class Settings(BaseSettings):
    """从 .env / 系统环境变量中加载项目配置。"""

    # ========== 大模型配置 ==========
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096
    llm_timeout: int = 60

    # ========== 数据库配置 ==========
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'agent.db').as_posix()}"

    # SQL Agent 建表时提供给大模型的样例行数
    sql_sample_rows: int = 3

    # ========== LangSmith 监控（可选）==========
    langsmith_tracing: bool = False
    langsmith_endpoint: str = ""
    langsmith_api_key: str = ""
    langsmith_project: str = "enterprise-data-agent"

    # ========== Tavily 搜索（可选）==========
    tavily_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
