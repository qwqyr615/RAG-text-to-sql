"""路线 B（单表宽表模型）的结构约束与 Prompt 行为测试。

不需要数据库：

- SQL 文件一致性、DDL 解析、``preset_metadata`` 对齐都是静态检查；
- Prompt 行为用假元数据；
- 知识层用假元数据验证「单表模型下业务对象不会静默丢失」。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from core.config import BASE_DIR
from knowledge.knowledge_base import ANALYSIS_THEMES, BUSINESS_OBJECTS
from knowledge.knowledge_service import resolve_knowledge
from metadata.preset_metadata import (
    COLUMN_DESCRIPTIONS,
    PRESET_BUSINESS_TABLES,
    RELATIONSHIPS,
)
from metadata.schema_ddl import (
    CONVERTED_NUMERIC_COLUMNS,
    DDL_FILE,
    LEGACY_DIM_TABLES,
    LOAD_FILE,
    SCHEMA_FILES,
    expected_columns,
    expected_primary_key,
    expected_table,
    iter_statements,
    load_insert_columns,
    normalize_type,
    read_sql,
    resolve_path,
)
from prompt.base import PromptContext
from prompt.providers import DataResourceProvider, KnowledgeProvider

EXPECTED_COLUMN_COUNT = 45


# ----------------------------------------------------------------------
# SQL 文件与 DDL 解析
# ----------------------------------------------------------------------
def test_schema_files_exist() -> None:
    for name in SCHEMA_FILES:
        assert resolve_path(name).is_file(), f"缺少 SQL 文件：{name}"


def test_iter_statements_ignores_comment_lines_and_splits() -> None:
    sql = "-- 注释\nSELECT 1;\n-- 另一段注释\nSELECT 2;\n"
    assert list(iter_statements(sql)) == ["SELECT 1", "SELECT 2"]


def test_normalize_type_matches_sqlalchemy_forms() -> None:
    assert normalize_type("DOUBLE(asdecimal=True)") == "DOUBLE"
    assert normalize_type("INTEGER") == "INT"
    assert normalize_type("DECIMAL(12, 4)") == "DECIMAL(12,4)"
    assert normalize_type("VARCHAR(50)") == "VARCHAR(50)"
    assert normalize_type("INTEGER(display_width=11)") == "INT"


def test_ddl_declares_expected_column_count() -> None:
    assert len(expected_columns()) == EXPECTED_COLUMN_COUNT


def test_ddl_has_no_text_columns() -> None:
    """核心断言：治理后的 DDL 不允许再出现 TEXT 类型列。"""
    offenders = [column.name for column in expected_columns() if "TEXT" in column.type]
    assert offenders == [], f"仍以 TEXT 声明的列：{offenders}"


def test_ddl_every_column_has_comment() -> None:
    missing = [column.name for column in expected_columns() if not column.comment]
    assert missing == [], f"缺少 COMMENT 的列：{missing}"


def test_ddl_converted_columns_are_numeric() -> None:
    types = {column.name: column.type for column in expected_columns()}
    for name in CONVERTED_NUMERIC_COLUMNS:
        assert name in types, f"{name} 不在 DDL 中"
        assert types[name].startswith(("DOUBLE", "INT", "DECIMAL")), (
            f"{name} 应为数值类型，实际 {types[name]}"
        )


def test_ddl_declares_primary_key_on_record_id() -> None:
    assert expected_primary_key() == "record_id"
    record_id = next(c for c in expected_columns() if c.name == "record_id")
    assert record_id.nullable is False


def test_ddl_sensor_columns_stay_nullable() -> None:
    """原始传感器读数有缺失，不能声明 NOT NULL。"""
    by_name = {column.name: column for column in expected_columns()}
    for name in ("ambient_temperature", "humidity", "motor_temperature", "feed_pressure"):
        assert by_name[name].nullable is True, f"{name} 应保持 NULL"


def test_load_column_list_matches_ddl_exactly() -> None:
    """防漂移：装载 SQL 的显式列清单必须与 DDL 的列清单完全一致（含顺序）。"""
    assert load_insert_columns() == [column.name for column in expected_columns()]


def test_load_statement_casts_every_converted_column() -> None:
    sql = _load_insert_statement()
    for name in CONVERTED_NUMERIC_COLUMNS:
        assert f"CAST(TRIM({name})" in sql, f"{name} 缺少显式 CAST"


def test_load_statement_avoids_select_star() -> None:
    """显式列清单：上游加列不应静默改变服务层结构。"""
    sql = _load_insert_statement().upper()
    assert "SELECT *" not in sql
    assert "FROM INTELLIGENT_PRODUCTION_IIOT" in sql


def test_drop_file_covers_all_legacy_dim_tables() -> None:
    sql = read_sql(SCHEMA_FILES[0]).upper()
    for table in LEGACY_DIM_TABLES:
        assert f"DROP TABLE IF EXISTS {table.upper()}" in sql, f"未删除 {table}"


def _load_insert_statement() -> str:
    """取装载文件里的 INSERT 语句本体（已剥离注释，避免注释里的字样干扰断言）。"""
    for statement in iter_statements(read_sql(LOAD_FILE)):
        if statement.upper().startswith("INSERT"):
            return statement
    raise AssertionError(f"{LOAD_FILE} 中找不到 INSERT 语句")


def _load_module(path: Path, name: str) -> Any:
    """按文件路径导入模块并注册到 sys.modules（dataclass 解析注解时依赖它）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def test_scripts_import_without_connecting_to_database() -> None:
    """两个脚本模块级只做定义，导入时不应连库（数据库连接是懒加载的）。"""
    init_module = _load_module(
        BASE_DIR / "scripts" / "init_preset_schema.py", "route_b_init_schema"
    )
    health_module = _load_module(
        BASE_DIR / "scripts" / "check_schema_health.py", "route_b_health_check"
    )

    assert callable(init_module.apply_schema)
    assert callable(health_module.run_health_check)
    assert len(health_module.CHECKS) == 9


# ----------------------------------------------------------------------
# preset_metadata 与知识模型对齐
# ----------------------------------------------------------------------
def test_business_table_whitelist_is_single_table() -> None:
    assert PRESET_BUSINESS_TABLES == [expected_table()]


def test_relationships_are_empty_for_single_table_model() -> None:
    assert RELATIONSHIPS == []


def test_column_descriptions_only_reference_service_table() -> None:
    valid = {f"{expected_table()}.{column.name}" for column in expected_columns()}
    unknown = sorted(key for key in COLUMN_DESCRIPTIONS if key not in valid)
    assert unknown == [], f"字段说明引用了 DDL 之外的列：{unknown}"


def test_knowledge_model_points_to_service_table() -> None:
    assert BUSINESS_OBJECTS, "业务对象不应为空"
    for obj in BUSINESS_OBJECTS:
        assert obj["default_table"] == expected_table()
    for theme in ANALYSIS_THEMES:
        assert set(theme["related_tables"]) <= {expected_table()}


def test_resolve_knowledge_survives_single_table_model() -> None:
    """路线 B 把 default_table 从 dim_* 改成 fact 后，业务对象不应被静默过滤掉。"""
    columns = [
        "production_line",
        "machine_id",
        "product_type",
        "batch_id",
        "shift",
        "defect_rate",
        "first_pass_yield",
        "quality_score",
        "downtime_minutes",
        "fault_event_count",
        "production_volume",
        "machine_utilization",
    ]
    metadata = {
        "tables": [
            {
                "table_name": "fact_production_record",
                "columns": [{"name": name} for name in columns],
            }
        ]
    }

    knowledge = resolve_knowledge(metadata)

    assert len(knowledge["objects"]) == len(BUSINESS_OBJECTS)
    mapped = {rule["name"]: rule["mapped_field"] for rule in knowledge["rules"]}
    assert mapped["缺陷率"] == "defect_rate"
    assert mapped["良率"] == "first_pass_yield"
    assert mapped["设备利用率"] == "machine_utilization"

    production = next(t for t in knowledge["themes"] if t["code"] == "production_analysis")
    assert production["related_tables"] == ["fact_production_record"]
    inventory = next(t for t in knowledge["themes"] if t["code"] == "inventory_analysis")
    assert inventory["related_tables"] == []


# ----------------------------------------------------------------------
# Prompt 行为
# ----------------------------------------------------------------------
def _single_table_metadata() -> dict:
    return {
        "tables": [
            {
                "table_name": "fact_production_record",
                "description": "生产记录事实宽表",
                "columns": [
                    {
                        "name": "record_id",
                        "type": "INTEGER",
                        "description": "生产记录主键",
                        "primary_key": True,
                    },
                    {
                        "name": "production_line",
                        "type": "VARCHAR(50)",
                        "description": "生产线编码",
                        "sample_value": "Line_A",
                    },
                    {
                        "name": "defect_rate",
                        "type": "DOUBLE",
                        "description": "缺陷率",
                        "sample_value": 2.75,
                    },
                ],
            }
        ],
        "relationships": [],
    }


def _single_table_context(question: str = "各产线缺陷率是多少"):
    return PromptContext(
        question=question,
        metadata_json=_single_table_metadata(),
        available_columns=["record_id", "production_line", "defect_rate"],
    )


def test_metadata_provider_hints_single_table_model() -> None:
    provider = DataResourceProvider(2000, min_tables=1)
    section = provider.provide(_single_table_context())

    assert "单表模型" in section.content
    assert "不要生成 JOIN" in section.content
    assert "表间关系" not in section.content


def test_metadata_provider_still_reports_relationships_when_present() -> None:
    context = _single_table_context()
    context.metadata_json["relationships"] = [
        {
            "source_table": "fact_production_record",
            "source_column": "machine_id",
            "target_table": "dim_machine",
            "target_column": "machine_id",
            "relation_type": "many-to-one",
        }
    ]
    section = DataResourceProvider(2000, min_tables=1).provide(context)

    assert "表间关系" in section.content
    assert "单表模型" not in section.content


def test_knowledge_provider_marks_theme_without_tables() -> None:
    knowledge = {
        "themes": [
            {
                "name": "库存分析",
                "description": "预留库存分析主题。",
                "related_tables": [],
            },
            {
                "name": "质量分析",
                "description": "围绕缺陷率、良率。",
                "related_tables": ["fact_production_record"],
            },
        ],
        "objects": [],
        "rules": [],
    }
    context = PromptContext(question="库存情况怎么样", knowledge=knowledge)
    section = KnowledgeProvider(2000).provide(context)

    assert "未接入该主题的数据表" in section.content
    assert "不要为其生成查询" in section.content
    assert "相关表：fact_production_record" in section.content
