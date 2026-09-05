"""验证配置模块可正常导入。"""

from core.config import BASE_DIR, settings


def test_config_loaded() -> None:
    assert BASE_DIR.name == "agent"
    assert hasattr(settings, "deepseek_api_key")
    assert hasattr(settings, "llm_model")
