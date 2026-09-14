"""只读执行路径测试：ReadOnlySQLDatabase + 只读会话。

这一组测试针对一个真实存在过的漏洞：校验器只挂在「结果回放取数」上，而 Agent 真正
执行 SQL 走的是 LangChain 的 ``sql_db_query`` 工具（最终调用 ``SQLDatabase.run``），
写操作会直接落库。现在守卫前移到 ``ReadOnlySQLDatabase``。
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tools.database import _READONLY_SESSION_SQL, _attach_readonly_session
from tools.sql_database import ReadOnlySQLDatabase
from tools.sql_guard import ReadOnlyViolation


@pytest.fixture()
def sqlite_engine(sqlite_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{sqlite_path}")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        )
        conn.execute(text("INSERT INTO t (id, name) VALUES (1, 'a'), (2, 'b')"))
    yield engine
    engine.dispose()


@pytest.fixture()
def db(sqlite_engine: Engine) -> ReadOnlySQLDatabase:
    return ReadOnlySQLDatabase(engine=sqlite_engine, include_tables=["t"])


def test_select_through_run_is_allowed(db: ReadOnlySQLDatabase) -> None:
    assert "2" in str(db.run("SELECT COUNT(*) FROM t"))


def test_table_info_still_works(db: ReadOnlySQLDatabase) -> None:
    """sql_db_schema 走 _execute 取样例数据，必须仍然可用。"""
    info = db.get_table_info(["t"])
    assert "CREATE TABLE" in info.upper() or "id" in info


def test_write_through_run_is_blocked(
    db: ReadOnlySQLDatabase, sqlite_engine: Engine
) -> None:
    with pytest.raises(ReadOnlyViolation):
        db.run("DROP TABLE t")

    # 表还在，说明写操作没有落库
    assert "2" in str(db.run("SELECT COUNT(*) FROM t"))


def test_write_through_langchain_tool_is_blocked(db: ReadOnlySQLDatabase) -> None:
    """模型通过 sql_db_query 工具执行写操作时，违规变成回灌给模型的错误文本。

    ``QuerySQLDatabaseTool`` 走 ``run_no_throw``，只捕获 ``SQLAlchemyError``；
    ``ReadOnlyViolation`` 正是 ``SQLAlchemyError``，所以这里拿到的是错误字符串而不是
    异常——这就是「可恢复的拒绝」：模型能读到原因并重写查询。
    """
    from langchain_community.tools import QuerySQLDatabaseTool

    tool = QuerySQLDatabaseTool(db=db)
    observation = str(tool.invoke({"query": "DELETE FROM t"}))

    assert "只读" in observation
    # 写操作没有落库
    assert "2" in str(db.run("SELECT COUNT(*) FROM t"))


def test_multi_statement_through_tool_is_blocked(db: ReadOnlySQLDatabase) -> None:
    from langchain_community.tools import QuerySQLDatabaseTool

    tool = QuerySQLDatabaseTool(db=db)
    observation = str(tool.invoke({"query": "SELECT 1; DROP TABLE t"}))

    assert "多条语句" in observation


def test_readonly_session_sql_covers_configured_dialects() -> None:
    assert _READONLY_SESSION_SQL["mysql"] == "SET SESSION TRANSACTION READ ONLY"
    assert _READONLY_SESSION_SQL["sqlite"] == "PRAGMA query_only = ON"


def test_readonly_session_blocks_writes(sqlite_path: Path) -> None:
    """第二道防线：即使 SQL 校验器被绕过，数据库会话本身也拒绝写入。"""
    setup_engine = create_engine(f"sqlite:///{sqlite_path}")
    with setup_engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER)"))
    setup_engine.dispose()

    engine = create_engine(f"sqlite:///{sqlite_path}")
    _attach_readonly_session(engine)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM t")).scalar() == 0
            with pytest.raises(Exception):
                conn.execute(text("INSERT INTO t (id) VALUES (1)"))
    finally:
        engine.dispose()
