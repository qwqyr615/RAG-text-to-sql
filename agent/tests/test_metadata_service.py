"""``metadata/metadata_service`` 测试。

两条路径都要覆盖：

1. **DDL COMMENT 优先**（生产路径）——用假 inspector 注入带 comment 的列；
2. **preset_metadata 兜底**（无注释的库，例如 SQLite）——用真实内存库。
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
from metadata.metadata_service import get_metadata_json
from metadata.preset_metadata import COLUMN_DESCRIPTIONS, TABLE_DESCRIPTIONS

SCRATCH_DIR = BASE_DIR / "sessions"


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

    assert metadata["schema_version"] == "1.0"
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
