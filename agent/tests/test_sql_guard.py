"""SQL 只读守卫的单元测试。

重点覆盖两类历史问题：
1. 误报——字符串字面量 / 注释 / 列名里的关键字被当成写操作；
2. 漏报——分号拼接、MySQL 可执行注释等绕过手法。
"""

import pytest

from tools.sql_guard import (
    ReadOnlyViolation,
    is_readonly_sql,
    strip_sql_literals_and_comments,
    validate_readonly_sql,
)


def test_select_is_allowed() -> None:
    assert validate_readonly_sql("SELECT 1") == "SELECT 1"
    assert is_readonly_sql("select line_id from dim_line")


def test_with_cte_is_allowed() -> None:
    sql = "WITH t AS (SELECT 1 AS a) SELECT a FROM t"
    assert validate_readonly_sql(sql) == sql


def test_trailing_semicolon_is_normalized() -> None:
    assert validate_readonly_sql("SELECT 1;") == "SELECT 1"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "ALTER TABLE t ADD COLUMN c INT",
        "CREATE TABLE t (a INT)",
        "TRUNCATE TABLE t",
        "GRANT ALL ON db.* TO u",
        "SELECT 1 FOR UPDATE",
    ],
)
def test_write_operations_are_rejected(sql: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        validate_readonly_sql(sql)


def test_multi_statement_is_rejected() -> None:
    with pytest.raises(ReadOnlyViolation, match="多条语句"):
        validate_readonly_sql("SELECT 1; DROP TABLE t")


def test_select_into_outfile_is_rejected() -> None:
    with pytest.raises(ReadOnlyViolation):
        validate_readonly_sql("SELECT * FROM t INTO OUTFILE '/tmp/x'")


def test_keyword_inside_string_literal_is_allowed() -> None:
    """回归测试：旧实现按全文匹配关键字，会把字面量里的 DELETE 判成违规。"""
    sql = "SELECT * FROM fact_production_record WHERE remark = 'DELETE 工单'"
    assert validate_readonly_sql(sql) == sql


def test_keyword_inside_comment_is_ignored() -> None:
    assert is_readonly_sql("SELECT record_id FROM fact_production_record -- 曾执行过 DELETE")


def test_keyword_as_column_name_prefix_is_not_matched() -> None:
    assert is_readonly_sql("SELECT created_at, updated_at FROM dim_batch")


def test_mysql_executable_comment_is_not_a_bypass() -> None:
    """MySQL 会真正执行 /*! ... */ 里的内容，不能被当成普通注释删掉。"""
    with pytest.raises(ReadOnlyViolation):
        validate_readonly_sql("SELECT 1 /*! ; DROP TABLE t */")


def test_blocked_function_call() -> None:
    with pytest.raises(ReadOnlyViolation, match="SLEEP"):
        validate_readonly_sql("SELECT SLEEP(10)")


def test_empty_sql_is_rejected() -> None:
    with pytest.raises(ReadOnlyViolation):
        validate_readonly_sql("   ")


def test_strip_helper_removes_literals_and_comments() -> None:
    stripped = strip_sql_literals_and_comments("SELECT 'DELETE' /* DELETE */ FROM t")
    assert "DELETE" not in stripped
    assert "FROM t" in stripped
