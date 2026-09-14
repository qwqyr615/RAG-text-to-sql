"""``metadata/metadata_service`` 测试。

字段说明的来源优先级（本次改造后）是三级：

1. **字段映射**（``mapping.yaml``）—— 客户的列名与业务词对不上时，这是唯一的语义来源；
2. **DDL COMMENT**（生产路径）—— 用假 inspector 注入带 comment 的列；
3. **preset_metadata 兜底**（无注释的库，例如 SQLite）—— 用真实内存库。

三条路径都要覆盖，且要验证优先级顺序，否则「映射优先级最高」这个承诺只是注释里的一句话。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from core.config import BASE_DIR
from metadata import metadata_service
from metadata.mapping.compiler import compile_profile
from metadata.mapping.schema import parse_profile
from metadata.metadata_service import get_metadata_json
from metadata.preset_metadata import COLUMN_DESCRIPTIONS, TABLE_DESCRIPTIONS

SCRATCH_DIR = BASE_DIR / "sessions"

#: 当前元数据 JSON 的结构版本（新增 field_map / inventory 后升到 1.1）
EXPECTED_SCHEMA_VERSION = "1.1"


@pytest.fixture()
def memory_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE fact_production_record ("
                "record_id INTEGER PRIMARY KEY, production_line TEXT, defect_rate REAL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO fact_production_record VALUES "
                "(1, 'Line_A', 2.5), (2, 'Line_B', 3.5)"
            )
        )
    return engine


class _FakeInspector:
    """只提供 metadata_service 用到的四个方法。"""

    def __init__(self, columns: list[dict[str, Any]], table_comment: dict | None = None):
        self._columns = columns
        self._table_comment = table_comment or {}

    def get_table_names(self) -> list[str]:
        return ["fact_production_record"]

    def get_columns(self, table: str) -> list[dict[str, Any]]:
        return self._columns

    def get_pk_constraint(self, table: str) -> dict[str, Any]:
        return {"constrained_columns": ["record_id"]}

    def get_table_comment(self, table: str) -> dict[str, Any]:
        return self._table_comment


def test_metadata_shape_from_memory_engine(memory_engine: Engine) -> None:
    metadata = get_metadata_json(engine=memory_engine)

    assert metadata["schema_version"] == EXPECTED_SCHEMA_VERSION
    assert metadata["database_type"] == "sqlite"
    assert metadata["relationships"] == []

    assert len(metadata["tables"]) == 1
    table = metadata["tables"][0]
    assert table["table_name"] == "fact_production_record"
    assert table["row_count"] == 2
    assert len(table["sample_rows"]) == 2

    record_id = next(c for c in table["columns"] if c["name"] == "record_id")
    assert record_id["primary_key"] is True
    # 注意：SQLite 不会把 INTEGER PRIMARY KEY 报成 NOT NULL，因此这里只验主键标记。


def test_metadata_reports_discovery_inventory(memory_engine: Engine) -> None:
    """元数据里要带发现模式的账本，便于回答「为什么这张表看不到」。"""
    metadata = get_metadata_json(engine=memory_engine)

    inventory = metadata["inventory"]
    assert inventory["business_tables"] == ["fact_production_record"]
    assert inventory["all_tables"] == ["fact_production_record"]
    assert inventory["roles"]["fact_production_record"] == "other"


def test_metadata_without_mapping_has_empty_field_map(memory_engine: Engine) -> None:
    """没配置映射时字段映射相关字段为空，行为退回「单表标准模型」。"""
    metadata = get_metadata_json(engine=memory_engine, mapping=None)

    assert metadata["mapping_profile"] == ""
    assert metadata["field_map"] == {}
    assert metadata["metric_bindings"] == []


def test_mapping_overrides_ddl_comment(
    monkeypatch: pytest.MonkeyPatch, memory_engine: Engine
) -> None:
    """**映射优先于 DDL COMMENT** —— 这是本次改造的核心优先级承诺。

    客户库常常既没有注释、列名又是缩写；映射是人审过的口径，信息量严格更大，
    因此必须能覆盖掉库里读到的（可能过期的）注释。
    """
    fake_columns = [
        {
            "name": "defect_rate",
            "type": "DOUBLE",
            "nullable": False,
            "default": None,
            "comment": "来自 DDL 的缺陷率说明",
        },
    ]
    monkeypatch.setattr(
        metadata_service,
        "inspect",
        lambda engine: _FakeInspector(fake_columns, {"text": "来自 DDL 的表说明"}),
    )

    # 用最小映射把 defect_rate 认领为「缺陷率」标准字段
    profile = parse_profile(
        {
            "schema_version": "1.0",
            "profile": "unit-test",
            "tables": [
                {
                    "name": "fact_production_record",
                    "role": "fact",
                    "description": "映射给出的表说明",
                    "columns": [
                        {
                            "column": "defect_rate",
                            "standard_fields": [{"name": "defect_rate", "unit": "百分比"}],
                        }
                    ],
                }
            ],
        }
    )
    table = get_metadata_json(
        engine=memory_engine, mapping=compile_profile(profile)
    )["tables"][0]

    assert table["description"] == "映射给出的表说明"
    defect = next(c for c in table["columns"] if c["name"] == "defect_rate")
    assert "缺陷率" in defect["description"]
    assert "来自 DDL 的缺陷率说明" not in defect["description"]
    assert defect["standard_fields"] == ["defect_rate"]


def test_descriptions_fall_back_to_preset_metadata(memory_engine: Engine) -> None:
    """SQLite 没有列注释，说明应回退到 preset_metadata。"""
    table = get_metadata_json(engine=memory_engine)["tables"][0]

    assert table["description"] == TABLE_DESCRIPTIONS["fact_production_record"]
    defect = next(c for c in table["columns"] if c["name"] == "defect_rate")
    assert defect["description"] == COLUMN_DESCRIPTIONS[
        "fact_production_record.defect_rate"
    ]
    assert defect["sample_value"] == 2.5


def test_ddl_comment_takes_precedence(
    monkeypatch: pytest.MonkeyPatch, memory_engine: Engine
) -> None:
    """DDL 的 COMMENT 是字段说明的唯一事实来源，优先于 preset_metadata。"""
    fake_columns = [
        {
            "name": "record_id",
            "type": "INTEGER",
            "nullable": False,
            "default": None,
            "comment": "来自 DDL 的主键说明",
        },
        {
            "name": "defect_rate",
            "type": "DOUBLE",
            "nullable": False,
            "default": None,
            "comment": "来自 DDL 的缺陷率说明",
        },
    ]
    monkeypatch.setattr(
        metadata_service,
        "inspect",
        lambda engine: _FakeInspector(fake_columns, {"text": "来自 DDL 的表说明"}),
    )

    table = get_metadata_json(engine=memory_engine)["tables"][0]

    assert table["description"] == "来自 DDL 的表说明"
    defect = next(c for c in table["columns"] if c["name"] == "defect_rate")
    assert defect["description"] == "来自 DDL 的缺陷率说明"


def test_export_metadata_json_writes_file(memory_engine: Engine) -> None:
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    path = SCRATCH_DIR / "metadata_unit_test.json"
    try:
        result = metadata_service.export_metadata_json(
            path=path, engine=memory_engine
        )
        assert result == path
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["tables"][0]["table_name"] == "fact_production_record"
    finally:
        path.unlink(missing_ok=True)
