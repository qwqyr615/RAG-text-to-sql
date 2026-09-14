"""``tools/sql_executor`` 测试（注入内存库，不依赖 MySQL）。

对应 vanna 那套「fake store」思路：把引擎换成可注入的假对象/内存库，
测的是本模块自己的逻辑（校验、取数、行数上限），不是数据库。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from tools import sql_executor
from tools.sql_executor import ReadOnlyViolation, execute_sql, validate_readonly_sql


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
                "(1, 'Line_A', 2.5), (2, 'Line_B', 3.5), (3, 'Line_C', 4.5)"
            )
        )
    return engine


@pytest.fixture()
def readonly_memory_engine(monkeypatch: pytest.MonkeyPatch, memory_engine: Engine):
    """把 execute_sql 用的只读引擎换成内存库。"""
    monkeypatch.setattr(sql_executor, "get_readonly_engine", lambda: memory_engine)
    return memory_engine


def test_execute_sql_returns_columns_and_rows(readonly_memory_engine: Engine) -> None:
    columns, rows = execute_sql(
        "SELECT record_id, production_line FROM fact_production_record ORDER BY record_id"
    )
    assert columns == ["record_id", "production_line"]
    assert rows == [[1, "Line_A"], [2, "Line_B"], [3, "Line_C"]]


def test_execute_sql_respects_limit(readonly_memory_engine: Engine) -> None:
    _columns, rows = execute_sql(
        "SELECT record_id FROM fact_production_record ORDER BY record_id", limit=2
    )
    assert rows == [[1], [2]]


def test_execute_sql_rejects_write_statements() -> None:
    with pytest.raises(ReadOnlyViolation):
        execute_sql("DROP TABLE fact_production_record")


def test_execute_sql_rejects_multi_statement() -> None:
    with pytest.raises(ReadOnlyViolation, match="多条语句"):
        execute_sql("SELECT 1; DROP TABLE fact_production_record")


def test_validate_readonly_sql_is_reexported() -> None:
    """sql_executor 继续导出校验函数，保持既有调用方可用。"""
    assert validate_readonly_sql("SELECT 1") == "SELECT 1"
