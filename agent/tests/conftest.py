"""Agent 测试共享 fixture。

全部使用内存数据（假元数据 / 假知识 / 假检索），不依赖 MySQL、Milvus 与大模型 API。
"""

import uuid
from pathlib import Path
from typing import Any, Callable

import pytest

from core.config import BASE_DIR
from prompt.base import PromptContext


@pytest.fixture()
def sqlite_path() -> Any:
    """临时 SQLite 文件路径（放在 data/ 下，已被 .gitignore 忽略）。

    不用 pytest 的 ``tmp_path``：它需要在临时目录里枚举编号目录，在受限环境下会
    直接被拒绝；这里只做「创建 + 删除单个文件」，不需要目录枚举。
    """
    directory = BASE_DIR / "data"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"test_{uuid.uuid4().hex[:8]}.db"
    yield path
    for suffix in ("", "-journal", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            candidate.unlink()


@pytest.fixture()
def sample_metadata() -> dict[str, Any]:
    """模拟 metadata_service.get_metadata_json() 的输出。"""
    return {
        "schema_version": "1.0",
        "database_type": "mysql",
        "tables": [
            {
                "table_name": "dim_line",
                "description": "产线维表",
                "columns": [
                    {
                        "name": "line_id",
                        "type": "VARCHAR(32)",
                        "description": "产线编号",
                        "primary_key": True,
                        "sample_value": "Line_A",
                    },
                    {
                        "name": "line_name",
                        "type": "VARCHAR(64)",
                        "description": "产线名称",
                        "sample_value": "一线",
                    },
                ],
            },
            {
                "table_name": "dim_machine",
                "description": "设备维表",
                "columns": [
                    {
                        "name": "machine_id",
                        "type": "VARCHAR(32)",
                        "description": "设备编号",
                        "primary_key": True,
                        "sample_value": "M01",
                    },
                    {
                        "name": "machine_utilization",
                        "type": "FLOAT",
                        "description": "设备利用率",
                        "sample_value": 0.88,
                    },
                ],
            },
            {
                "table_name": "fact_production_record",
                "description": "生产事实表",
                "columns": [
                    {
                        "name": "record_id",
                        "type": "INTEGER",
                        "description": "记录编号",
                        "primary_key": True,
                        "sample_value": 1,
                    },
                    {
                        "name": "defect_rate",
                        "type": "FLOAT",
                        "description": "缺陷率",
                        "sample_value": 0.02,
                    },
                    {
                        "name": "first_pass_yield",
                        "type": "FLOAT",
                        "description": "良率/直通率",
                        "sample_value": 0.97,
                    },
                    {
                        "name": "downtime_minutes",
                        "type": "FLOAT",
                        "description": "停机分钟数",
                        "sample_value": 12.5,
                    },
                ],
            },
        ],
        "relationships": [
            {
                "source_table": "fact_production_record",
                "source_column": "machine_id",
                "target_table": "dim_machine",
                "target_column": "machine_id",
                "relation_type": "many-to-one",
            }
        ],
    }


@pytest.fixture()
def sample_knowledge() -> dict[str, Any]:
    """模拟 knowledge_service.resolve_knowledge() 的输出。"""
    return {
        "schema_version": "1.0",
        "themes": [
            {
                "code": "quality_analysis",
                "name": "质量分析",
                "description": "围绕缺陷率、良率、质量得分等进行分析。",
                "related_tables": ["fact_production_record"],
            },
            {
                "code": "inventory_analysis",
                "name": "库存分析",
                "description": "预留库存分析主题。",
                "related_tables": [],
            },
        ],
        "objects": [
            {
                "name": "设备",
                "default_table": "dim_machine",
                "key_field": "machine_id",
                "description": "执行生产活动的设备，例如 M01。",
            }
        ],
        "rules": [
            {
                "name": "缺陷率",
                "mapped_table": "fact_production_record",
                "mapped_field": "defect_rate",
                "resolved_calculation": "统计平均值可用 AVG(defect_rate)，越高越差。",
            },
            {
                "name": "库存周转率",
                "mapped_table": None,
                "mapped_field": None,
                "resolved_calculation": "当前数据源缺少该指标字段",
            },
        ],
    }


@pytest.fixture()
def sample_columns(sample_metadata: dict[str, Any]) -> list[str]:
    columns: list[str] = []
    for table in sample_metadata["tables"]:
        columns.extend(column["name"] for column in table["columns"])
    return columns


@pytest.fixture()
def make_context(
    sample_metadata: dict[str, Any],
    sample_knowledge: dict[str, Any],
    sample_columns: list[str],
) -> Callable[..., PromptContext]:
    """构造 PromptContext 的工厂，默认问题与「缺陷率」相关。"""

    def _make(question: str = "各产线缺陷率是多少") -> PromptContext:
        return PromptContext(
            question=question,
            metadata_json=sample_metadata,
            knowledge=sample_knowledge,
            available_columns=sample_columns,
        )

    return _make
