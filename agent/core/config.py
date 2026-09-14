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
    llm_timeout: int = 120

    # ========== 数据库配置 ==========
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'agent.db').as_posix()}"

    # SQL Agent 建表时提供给大模型的样例行数
    sql_sample_rows: int = 3

    # ========== SQL 安全 ==========
    # 只读引擎是否把数据库会话设为只读（MySQL/PostgreSQL/SQLite 支持）。
    # SQL 层校验（tools/sql_guard）始终生效，这里是第二道防线。
    db_readonly_session: bool = True
    # 结果回放取数最多返回多少行，防止大结果撑爆前端
    sql_result_row_limit: int = 200

    # ========== SQL Agent 循环与工具预算 ==========
    # 单次提问最多几轮「工具调用 → 观测 → 再决策」
    sql_agent_max_iterations: int = 8
    # 单次提问最长执行时间（秒），None 表示不限制
    sql_agent_max_execution_time: float = 90.0
    # 提示模型默认返回的行数上限（写入 system prompt 的 {top_k}）
    sql_agent_top_k: int = 50
    # 是否打印 LangChain Agent 的中间步骤
    sql_agent_verbose: bool = True

    # ========== Prompt 上下文预算（字符数）==========
    # 四段上下文合计上限；超出后按段优先级回收
    prompt_total_budget: int = 8000
    # 元数据段：表 / 字段 / 样例值 / 表间关系
    prompt_metadata_budget: int = 3000
    # 指标段：业务指标口径 -> 字段映射
    prompt_metrics_budget: int = 1500
    # 知识段：分析主题 / 业务对象 / 指标规则
    prompt_knowledge_budget: int = 1800
    # RAG 段：相似问题与 SQL 示例
    prompt_rag_budget: int = 1500
    # 元数据段至少展示几张表（即使与问题相关性为 0）
    prompt_metadata_min_tables: int = 3
    # 元数据段给相关性最高的几张表附带样例值
    prompt_metadata_sample_tables: int = 2
    # 单张表最多展示多少个字段，其余提示模型用 sql_db_schema 查看
    sql_max_columns_per_table: int = 25

    # ========== Prompt 段开关（消融实验与降级用）==========
    # RAG 段是否启用：关掉即为评测里的 rag_off 配置
    rag_enabled: bool = True
    # 指标段是否启用：关掉即为评测里的 metrics_off 配置
    prompt_metrics_enabled: bool = True

    # ========== 多轮会话 ==========
    # 单个会话保留多少轮历史（一问一答为一轮），0 表示关闭多轮
    session_max_turns: int = 6
    # 会话存储方式：memory（进程内）/ file（落盘到 agent/sessions/）
    session_store: str = "memory"

    # ========== LangSmith 监控（可选）==========
    langsmith_tracing: bool = False
    langsmith_endpoint: str = ""
    langsmith_api_key: str = ""
    langsmith_project: str = "enterprise-data-agent"

    # ========== Tavily 搜索（可选）==========
    tavily_api_key: str = ""

    # ========== SiliconFlow 嵌入模型配置 ==========
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_embedding_model: str = "Qwen/Qwen3-VL-Embedding-8B"

    # ========== Milvus RAG 配置 ==========
    milvus_uri: str = "http://localhost:19530"
    milvus_db_name: str = "rag_dev"
    milvus_collection_name: str = "doc"
    rag_top_k: int = 3
    # 相似度低于该值的示例直接丢弃，避免不相似示例污染 Prompt（COSINE，越大越相似）
    rag_min_score: float = 0.45

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
