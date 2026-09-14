"""RAG 示例库测试。

重点是**示例 SQL 与当前数据模型的一致性**：路线 B 删除 dim_* 之后，示例库里
残留的 ``JOIN dim_line`` 会把模型直接带偏，必须有测试守住。
"""

from __future__ import annotations

from rag.sql_example_store import (
    extract_table_refs,
    load_examples,
    validate_examples,
)

KNOWN_TABLES = ["fact_production_record"]


def test_extract_table_refs_handles_case_and_backticks() -> None:
    refs = extract_table_refs(
        "SELECT * FROM `fact_production_record` f JOIN dim_line l ON 1=1"
    )
    assert refs == {"fact_production_record", "dim_line"}


def test_extract_table_refs_ignores_subquery_alias() -> None:
    refs = extract_table_refs(
        "SELECT * FROM (SELECT 1 AS a) x JOIN fact_production_record f ON 1=1"
    )
    assert refs == {"fact_production_record"}


def test_shipped_examples_pass_validation() -> None:
    """回归测试：示例文件必须引用的都是当前存在的表。

    这条测试就是用来防止「删了维表但示例 SQL 还在 JOIN 它」这类漂移。
    """
    examples = load_examples()
    assert examples, "示例文件不应为空"
    assert validate_examples(examples, known_tables=KNOWN_TABLES) == []


def test_example_referencing_dropped_dim_table_is_rejected() -> None:
    examples = [
        {
            "id": "bad_001",
            "question": "各产线的设备数量",
            "sql": (
                "SELECT l.line_id, COUNT(m.machine_id) FROM dim_line l "
                "LEFT JOIN dim_machine m ON l.line_id = m.line_id GROUP BY l.line_id"
            ),
            "tables": ["dim_line", "dim_machine"],
        }
    ]
    problems = validate_examples(examples, known_tables=KNOWN_TABLES)
    assert any("不存在的表" in problem for problem in problems)


def test_example_with_undeclared_table_is_rejected() -> None:
    examples = [
        {
            "id": "bad_002",
            "question": "平均缺陷率",
            "sql": "SELECT AVG(defect_rate) FROM fact_production_record",
            "tables": [],
        }
    ]
    problems = validate_examples(examples, known_tables=KNOWN_TABLES)
    assert any("未在 tables 字段声明" in problem for problem in problems)


def test_example_with_write_sql_is_rejected() -> None:
    examples = [
        {
            "id": "bad_003",
            "question": "清空数据",
            "sql": "DELETE FROM fact_production_record",
            "tables": ["fact_production_record"],
        }
    ]
    problems = validate_examples(examples, known_tables=KNOWN_TABLES)
    assert any("只读校验" in problem for problem in problems)


def test_duplicate_and_missing_fields_are_reported() -> None:
    examples = [
        {"id": "dup", "question": "问题", "sql": "SELECT 1", "tables": []},
        {"id": "dup", "question": "", "sql": "", "tables": []},
    ]
    problems = validate_examples(examples, known_tables=None)
    assert any("id 重复" in problem for problem in problems)
    assert any("缺少必填字段 question" in problem for problem in problems)
    assert any("缺少必填字段 sql" in problem for problem in problems)


def test_validation_without_known_tables_skips_table_existence_check() -> None:
    examples = [
        {
            "id": "ok_noschema",
            "question": "任意",
            "sql": "SELECT 1 FROM some_unknown_table",
            "tables": ["some_unknown_table"],
        }
    ]
    assert validate_examples(examples, known_tables=None) == []
