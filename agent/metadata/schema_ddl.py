"""解析 sql/ 目录下的 DDL 与装载语句。

设计原则：**DDL 文件是表结构的唯一事实来源**。

- ``scripts/init_preset_schema.py`` 负责按顺序执行它们；
- ``scripts/check_schema_health.py`` 解析 DDL 拿到「期望结构」，再与线上库比对；
- ``tests/test_schema_scripts.py`` 用它校验「DDL 列清单」与「装载 SQL 列清单」不会漂移。

因此 ``sql/02_*.sql`` 的书写格式是受约束的：每个列一行，
格式为 ``列名 类型 NOT NULL|NULL COMMENT '说明'``；COMMENT 中不要出现英文单引号。
格式一旦被改坏，单元测试会直接失败，而不是让健康检查给出误判。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from core.config import BASE_DIR

__all__ = [
    "DDL_FILE",
    "DROP_DIM_FILE",
    "LOAD_FILE",
    "SCHEMA_DIR",
    "SCHEMA_FILES",
    "DdlColumn",
    "expected_columns",
    "expected_primary_key",
    "expected_table",
    "iter_statements",
    "load_insert_columns",
    "load_statements",
    "normalize_type",
    "read_sql",
    "resolve_path",
    "split_column_list",
]

SCHEMA_DIR = BASE_DIR / "sql"

DROP_DIM_FILE = "01_drop_legacy_dim_tables.sql"
DDL_FILE = "02_create_fact_production_record.sql"
LOAD_FILE = "03_load_fact_production_record.sql"

#: 按执行顺序排列
SCHEMA_FILES: tuple[str, ...] = (DROP_DIM_FILE, DDL_FILE, LOAD_FILE)

#: 路线 B：不应再存在的派生维表
LEGACY_DIM_TABLES: tuple[str, ...] = (
    "dim_line",
    "dim_machine",
    "dim_product",
    "dim_batch",
)

#: 原始层表名（CSV 导入的原样数据，本套脚本只读不写）
SOURCE_TABLE = "intelligent_production_iiot"

#: 曾经以 TEXT 存储、本次治理转换为数值的列；健康检查据此做排序回归与转换保真校验
CONVERTED_NUMERIC_COLUMNS: tuple[str, ...] = (
    "first_pass_yield",
    "fault_event_count",
    "downtime_minutes",
    "maintenance_frequency",
    "production_cost_per_unit",
    "resource_efficiency",
    "production_efficiency",
    "energy_saving_pct",
    "downtime_reduction_pct",
    "cost_reduction_pct",
    "benefit_score",
)


@dataclass(frozen=True)
class DdlColumn:
    """DDL 中声明的一个列。"""

    name: str
    type: str
    nullable: bool
    comment: str


_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(?P<name>\w+)`?", re.IGNORECASE
)
_PRIMARY_KEY_RE = re.compile(
    r"PRIMARY\s+KEY\s*\(\s*`?(?P<column>\w+)`?\s*\)", re.IGNORECASE
)
_COLUMN_RE = re.compile(
    r"^\s*`?(?P<name>[A-Za-z_][A-Za-z0-9_]*)`?\s+"
    r"(?P<type>[A-Za-z]+(?:\s*\(\s*\d+\s*(?:,\s*\d+\s*)?\))?)\s+"
    r"(?P<nullability>NOT\s+NULL|NULL)\s+"
    r"COMMENT\s+'(?P<comment>[^']*)'",
    re.IGNORECASE,
)
_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+`?(?P<table>\w+)`?\s*\((?P<columns>[^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)

#: 类型别名归一：让 SQLAlchemy 报出的类型与 DDL 里的写法可比较
_TYPE_ALIASES = {
    "INTEGER": "INT",
    "INT": "INT",
    "BIGINT": "BIGINT",
    "DOUBLE": "DOUBLE",
    "FLOAT": "FLOAT",
    "DECIMAL": "DECIMAL",
    "NUMERIC": "DECIMAL",
    "VARCHAR": "VARCHAR",
    "TEXT": "TEXT",
}


def resolve_path(name: str) -> Path:
    """返回 sql/ 目录下的文件路径。"""
    return SCHEMA_DIR / name


def read_sql(name: str) -> str:
    """读取 sql/ 目录下的 SQL 文件。"""
    return resolve_path(name).read_text(encoding="utf-8")


def iter_statements(sql_text: str) -> Iterator[str]:
    """把 SQL 文件拆成单条语句。

    只做两件事：丢掉整行 ``--`` 注释，然后按 ``;`` 切分。
    本目录的 SQL 不使用行尾注释，也不在字符串里放分号，因此这个简化拆分是安全的
    （见 tests/test_schema_scripts.py）。
    """
    kept_lines = [
        line for line in sql_text.splitlines() if not line.strip().startswith("--")
    ]
    for chunk in "\n".join(kept_lines).split(";"):
        statement = chunk.strip()
        if statement:
            yield statement


def load_statements(path: Path) -> list[str]:
    """读取并拆分一个 SQL 文件。"""
    return list(iter_statements(path.read_text(encoding="utf-8")))


def parse_ddl(sql_text: str) -> tuple[str, list[DdlColumn], str | None]:
    """解析建表 DDL，返回 ``(表名, 列清单, 主键列名)``。"""
    table_match = _TABLE_RE.search(sql_text)
    if table_match is None:
        raise ValueError("DDL 中找不到 CREATE TABLE 语句")

    columns: list[DdlColumn] = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        match = _COLUMN_RE.match(line)
        if match is None:
            continue
        columns.append(
            DdlColumn(
                name=match.group("name"),
                type=re.sub(r"\s+", "", match.group("type")).upper(),
                nullable=match.group("nullability").upper().replace(" ", "") == "NULL",
                comment=match.group("comment").strip(),
            )
        )

    if not columns:
        raise ValueError("DDL 中解析不到任何列，请检查书写格式")

    primary_key_match = _PRIMARY_KEY_RE.search(sql_text)
    primary_key = primary_key_match.group("column") if primary_key_match else None
    return table_match.group("name"), columns, primary_key


def expected_table() -> str:
    """DDL 声明的表名。"""
    return parse_ddl(read_sql(DDL_FILE))[0]


def expected_columns() -> list[DdlColumn]:
    """DDL 声明的列清单（保持文件中的顺序）。"""
    return parse_ddl(read_sql(DDL_FILE))[1]


def expected_primary_key() -> str | None:
    """DDL 声明的主键列名。"""
    return parse_ddl(read_sql(DDL_FILE))[2]


def split_column_list(text: str) -> list[str]:
    """把 ``a, b, c`` 形式的列清单拆成列名列表。"""
    return [
        item.strip().strip("`")
        for item in text.split(",")
        if item.strip().strip("`")
    ]


def load_insert_columns(name: str = LOAD_FILE) -> list[str]:
    """解析装载 SQL 中 ``INSERT INTO ... (列清单)`` 的列。"""
    match = _INSERT_RE.search(read_sql(name))
    if match is None:
        raise ValueError(f"{name} 中找不到 INSERT INTO ... (列清单) 语句")
    return split_column_list(match.group("columns"))


def normalize_type(type_text: str) -> str:
    """归一化类型文本，便于与 DDL 声明比较。

    SQLAlchemy 会给出 ``DOUBLE(asdecimal=True)``、``INTEGER``、``DECIMAL(12, 4)``
    这类写法，统一成 ``DOUBLE``、``INT``、``DECIMAL(12,4)``。
    """
    text = str(type_text).upper()
    text = re.sub(r"\(ASDECIMAL=[A-Z]+\)", "", text)
    text = re.sub(r"\(DISPLAY_WIDTH=\d+\)", "", text)
    text = re.sub(r"\s+", "", text)
    match = re.match(r"(?P<base>[A-Z]+)(?P<args>\(.*\))?$", text)
    if not match:
        return text
    base = _TYPE_ALIASES.get(match.group("base"), match.group("base"))
    return f"{base}{match.group('args') or ''}"


def describe_columns(columns: Sequence[DdlColumn]) -> str:
    """把列清单渲染成一行摘要，便于日志与报错信息。"""
    return ", ".join(f"{column.name}:{column.type}" for column in columns)
