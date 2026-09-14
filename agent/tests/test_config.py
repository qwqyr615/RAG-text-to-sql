"""验证配置模块可正常导入，且新增的预算与安全配置项存在。"""

from core.config import BASE_DIR, settings


def test_config_loaded() -> None:
    assert BASE_DIR.name == "agent"
    assert hasattr(settings, "deepseek_api_key")
    assert hasattr(settings, "llm_model")


def test_sql_agent_budget_settings() -> None:
    assert settings.sql_agent_max_iterations > 0
    assert settings.sql_agent_max_execution_time >= 0
    assert settings.sql_agent_top_k > 0
    assert settings.sql_result_row_limit > 0


def test_prompt_section_budgets() -> None:
    per_section = (
        settings.prompt_metadata_budget
        + settings.prompt_metrics_budget
        + settings.prompt_knowledge_budget
        + settings.prompt_rag_budget
    )
    assert all(
        budget > 0
        for budget in (
            settings.prompt_metadata_budget,
            settings.prompt_metrics_budget,
            settings.prompt_knowledge_budget,
            settings.prompt_rag_budget,
        )
    )
    # 单段预算之和可以大于总预算（由组装器回收），但不能小到没有意义
    assert settings.prompt_total_budget > 0
    assert per_section >= settings.prompt_total_budget * 0.5


def test_sql_safety_settings() -> None:
    assert isinstance(settings.db_readonly_session, bool)
    assert 0.0 <= settings.rag_min_score <= 1.0
