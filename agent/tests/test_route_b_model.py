"""路线 B（单表宽表模型）的结构约束与 Prompt 行为测试。

不需要数据库：

- SQL 文件一致性、DDL 解析、``preset_metadata`` 对齐都是静态检查；
- Prompt 行为用假元数据；
- 知识层用假元数据验证「单表模型下业务对象不会静默丢失」。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from core.config import BASE_DIR
from knowledge.knowledge_base import ANALYSIS_THEMES, BUSINESS_OBJECTS
from knowledge.knowledge_service import resolve_knowledge
from metadata.mapping import resolve_mapping
from metadata.inventory import discover_tables
from metadata.mapping.schema import validate_profile
from metadata.preset_metadata import (
    COLUMN_DESCRIPTIONS,
    RELATIONSHIPS,
    STANDARD_SERVICE_TABLE,
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
from metadata.standard_fields import STANDARD_FIELDS
from prompt.base import PromptContext
from prompt.providers import DataResourceProvider, KnowledgeProvider

EXPECTED_COLUMN_COUNT = 45

#: 仓库自带的映射文件（发现 + 字段口径的入口）
STANDARD_MAPPING = "mappings/standard_production.mapping.yaml"
CUSTOMER_MAPPING = "mappings/mes_prod_log.mapping.yaml"


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
    # 9 项标准模型检查 + 发现模式 + 字段映射 = 11 项
    assert len(health_module.CHECKS) == 11


def _run_script_directly(script_name: str) -> subprocess.CompletedProcess:
    """在子进程里复现 `python scripts/xxx.py` 的导入环境。

    按路径直接运行脚本时 ``sys.path[0]`` 是 ``scripts/`` 而不是 ``agent/``，
    脚本必须自己把项目根目录补进 ``sys.path``，否则 ``import core/metadata/...`` 会失败。
    """
    code = (
        "import runpy, sys\n"
        "sys.path[0] = 'scripts'\n"
        f"module = runpy.run_path('scripts/{script_name}', run_name='probe')\n"
        "assert module\n"
    )
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BASE_DIR,
        env=env,
        check=False,
    )


@pytest.mark.parametrize(
    "script_name", ["init_preset_schema.py", "check_schema_health.py"]
)
def test_scripts_run_directly_without_import_errors(script_name: str) -> None:
    """回归：README 里写的 `python scripts/xxx.py` 必须真的能跑通。

    历史上这两个脚本依赖 PyCharm 自动把 content root 加进 PYTHONPATH，
    在命令行直接运行会报 ``ModuleNotFoundError``。
    """
    result = _run_script_directly(script_name)
    assert result.returncode == 0, (
        f"{script_name} 直接运行失败（退出码 {result.returncode}）"
    )


# ----------------------------------------------------------------------
# 发现模式与字段映射（替代原先的硬编码白名单）
# ----------------------------------------------------------------------
def test_standard_mapping_covers_every_ddl_column() -> None:
    """新契约：标准底座的字段口径来自 mapping.yaml，而不是代码里的白名单。

    这条断言承接了原先 ``PRESET_BUSINESS_TABLES == [expected_table()]`` 的作用 ——
    但方向反了过来：不再检查「代码里的表名是不是 DDL 那张表」，而是检查
    「映射文件是否完整覆盖了 DDL 声明的每一列」。漏一列就意味着模型拿不到那列的
    口径，只能靠猜。
    """
    mapping = resolve_mapping(STANDARD_MAPPING, use_cache=False)
    assert mapping is not None

    assert mapping.tables == [expected_table()]
    assert mapping.profile.discovery.get("exclude"), "标准映射必须排除未治理的原始层"

    mapped_columns = {item.column for item in mapping.table(expected_table())}
    ddl_columns = {column.name for column in expected_columns()}
    assert mapped_columns == ddl_columns, (
        f"映射未覆盖的列：{sorted(ddl_columns - mapped_columns)}；"
        f"映射中多余的列：{sorted(mapped_columns - ddl_columns)}"
    )


def test_standard_mapping_binds_every_standard_field() -> None:
    """45 个标准字段应当都被标准底座认出，否则指标段会静默缺项。"""
    mapping = resolve_mapping(STANDARD_MAPPING, use_cache=False)
    assert mapping is not None

    bound = {item.standard_field for item in mapping.table(expected_table())}
    missing = sorted({field.name for field in STANDARD_FIELDS} - bound)
    assert missing == [], f"标准底座未绑定的标准字段：{missing}"


def test_standard_mapping_needs_no_unit_conversion() -> None:
    """标准底座的单位就是规范单位，不应出现任何换算。"""
    mapping = resolve_mapping(STANDARD_MAPPING, use_cache=False)
    assert mapping is not None

    converted = [item for item in mapping.table(expected_table()) if item.needs_conversion]
    assert converted == [], f"标准底座不该有单位换算：{converted}"


def test_customer_mapping_declares_unit_conversions_explicitly() -> None:
    """客户映射的核心价值：把「客户单位 ≠ 标准口径」显式写出来。

    ``mes_prod_log`` 的 ``def_rate`` / ``util`` 存的是比例（0.039 / 0.70），
    标准口径是百分数（3.9 / 70）。映射必须给出 ×100 表达式，否则生成的 SQL
    结果会整体差 100 倍，而且**不会报错** —— 属于最危险的一类静默错误。
    """
    mapping = resolve_mapping(CUSTOMER_MAPPING, use_cache=False)
    assert mapping is not None

    converted = {
        item.standard_field: item.expression
        for item in mapping.table("mes_prod_log")
        if item.needs_conversion
    }
    assert converted == {
        "machine_utilization": "util * 100",
        "defect_rate": "def_rate * 100",
    }


def test_customer_mapping_excludes_equivalent_tables() -> None:
    """客户映射必须把「内容等价但没治理」的表排除掉，避免模型随机挑选。"""
    mapping = resolve_mapping(CUSTOMER_MAPPING, use_cache=False)
    assert mapping is not None

    excluded = set(mapping.profile.discovery.get("exclude") or [])
    assert "intelligent_production_iiot" in excluded  # 未治理的原始层
    assert "fact_production_record" in excluded  # 与客户表内容等价的标准表


def test_mappings_pass_structural_validation() -> None:
    """仓库里的映射文件本身必须通过校验（CI 意义）。"""
    for path in (STANDARD_MAPPING, CUSTOMER_MAPPING):
        mapping = resolve_mapping(path, use_cache=False)
        assert mapping is not None, path
        problems = [
            issue.render()
            for issue in validate_profile(mapping.profile)
            if issue.is_error
        ]
        assert problems == [], f"{path} 校验未通过：{problems}"


def test_customer_mapping_defers_to_standard_field_vocabulary() -> None:
    """映射只能使用词典里的标准字段 —— 防止接入时自造字段名。"""
    valid = {field.name for field in STANDARD_FIELDS}
    for path in (STANDARD_MAPPING, CUSTOMER_MAPPING):
        mapping = resolve_mapping(path, use_cache=False)
        assert mapping is not None
        for table in mapping.tables:
            for item in mapping.table(table):
                assert item.standard_field in valid, (
                    f"{path} 使用了词典外的标准字段：{item.standard_field}"
                )


def test_discovery_excludes_system_tables() -> None:
    """发现模式必须挡住系统表前缀（不依赖具体客户库）。

    SQLite 不允许建 ``sqlite_*`` 表，所以这里用 ``information_schema_*`` 前缀
    来验证前缀规则同样生效。
    """
    from sqlalchemy import create_engine, text

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE business_fact (id INTEGER)"))
        conn.execute(text("CREATE TABLE information_schema_columns (c TEXT)"))

    inventory = discover_tables(engine)
    assert "business_fact" in inventory.business_tables
    assert "information_schema_columns" not in inventory.business_tables
    assert "information_schema_columns" in inventory.excluded


def test_relationships_are_empty_for_single_table_model() -> None:
    """内置关系为空：单表模型下不应声明无意义的 JOIN。"""
    assert RELATIONSHIPS == []


def test_standard_service_table_is_the_ddl_table() -> None:
    """``STANDARD_SERVICE_TABLE`` 只是「未配置映射」时的兜底，必须与 DDL 一致。"""
    assert STANDARD_SERVICE_TABLE == expected_table()


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
