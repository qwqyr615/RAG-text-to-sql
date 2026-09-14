"""SQL 只读守卫：所有真正执行 SQL 的路径共用的校验与规范化。

设计原则
--------
1. **挂在执行路径上**。早期只校验 ``execute_sql`` 的结果回放，而 Agent 真正执行
   SQL 走的是 LangChain ``SQLDatabase``（``sql_db_query`` 工具），模型生成的写操作
   会直接落库。现在校验前移到 ``tools/sql_database.ReadOnlySQLDatabase``，
   两条路径共用本模块。
2. **先剥离字符串字面量与注释，再匹配关键字**。否则 ``WHERE remark = 'DELETE'``
   这类查询会被误判为写操作。
3. **报错信息直接回灌给模型**。校验失败会作为工具观测结果返回，模型据此重写查询，
   因此异常文本要写清楚「哪里违规、应该怎么做」。
"""

from __future__ import annotations

import re

from sqlalchemy.exc import SQLAlchemyError

__all__ = [
    "ReadOnlyViolation",
    "validate_readonly_sql",
    "is_readonly_sql",
    "strip_sql_literals_and_comments",
]

#: 允许作为整条语句开头的关键字
ALLOWED_FIRST_KEYWORDS = ("SELECT", "WITH")

#: 出现即判违规的写操作 / DDL / 危险语句片段
BLOCKED_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "REPLACE",
    "MERGE",
    "UPSERT",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "RENAME",
    "GRANT",
    "REVOKE",
    "CALL",
    "EXEC",
    "EXECUTE",
    "LOCK TABLES",
    "UNLOCK TABLES",
    "INTO OUTFILE",
    "INTO DUMPFILE",
    "LOAD DATA",
    "LOAD_FILE",
    "FOR UPDATE",
)

#: 出现即判违规的函数调用（资源耗尽型）
BLOCKED_FUNCTION_CALLS = ("SLEEP", "BENCHMARK")

_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(keyword) for keyword in BLOCKED_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
_FUNCTION_CALL_RE = re.compile(
    r"\b(" + "|".join(BLOCKED_FUNCTION_CALLS) + r")\s*\(",
    re.IGNORECASE,
)


class ReadOnlyViolation(SQLAlchemyError):
    """SQL 未通过只读校验。

    **为什么继承 ``SQLAlchemyError`` 而不是 ``ValueError``**

    LangChain 的 ``QuerySQLDatabaseTool`` 内部调用 ``SQLDatabase.run_no_throw``，
    而它只捕获 ``SQLAlchemyError``，会把异常转成 ``"Error: ..."`` 字符串作为工具观测
    结果回灌给模型。若这里抛普通 ``ValueError``，违规会穿透工具、直接终止整次 Agent
    调用，模型就失去了「看到原因并重写查询」的机会（system prompt 工作流约束第 5 条）。

    异常文本会原样进入模型的观测结果，因此要写清楚「哪里违规、应该怎么做」。
    """

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else self.__class__.__name__


def strip_sql_literals_and_comments(sql: str) -> str:
    """剥离字符串字面量、引号标识符与注释，只留下可判定的 SQL 结构。

    - ``'...'`` / ``"..."`` / ``\\`...\\``` 内容被清空（保留引号本身）；
    - ``--``、``#``、``/* */`` 注释被替换为空格；
    - MySQL 可执行注释 ``/*! ... */`` **不删除**，而是把内部内容原样展开，
      因为 MySQL 会真正执行其中的语句，删除会形成绕过。
    """
    out: list[str] = []
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]

        # 字符串字面量 / 引号标识符
        if char in ("'", '"', "`"):
            quote = char
            out.append(quote)
            index += 1
            while index < length:
                current = sql[index]
                if current == "\\" and quote != "`":
                    index += 2
                    continue
                if current == quote:
                    if index + 1 < length and sql[index + 1] == quote:
                        index += 2
                        continue
                    out.append(quote)
                    index += 1
                    break
                index += 1
            continue

        # 行注释 --
        if char == "-" and sql.startswith("--", index):
            newline = sql.find("\n", index)
            index = length if newline == -1 else newline
            out.append(" ")
            continue

        # 行注释 #
        if char == "#":
            newline = sql.find("\n", index)
            index = length if newline == -1 else newline
            out.append(" ")
            continue

        # 块注释，注意 MySQL 可执行注释
        if char == "/" and sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            inner = sql[index + 2 : end if end != -1 else length]
            if inner.startswith("!"):
                # /*!50700 DROP ... */ 这类注释 MySQL 会执行，展开其内容
                out.append(" " + inner.lstrip("!").strip() + " ")
            end_index = length if end == -1 else end + 2
            index = end_index
            out.append(" ")
            continue

        out.append(char)
        index += 1

    return "".join(out)


def validate_readonly_sql(sql: str) -> str:
    """校验 SQL 是否为单条只读查询，通过则返回可执行 SQL（去掉结尾分号）。

    Raises:
        ReadOnlyViolation: SQL 为空、含多条语句、非 SELECT/WITH 开头、
            或含写操作 / DDL / 危险函数。
    """
    if sql is None or not str(sql).strip():
        raise ReadOnlyViolation("SQL 为空，未执行任何查询。")

    original = str(sql).strip()
    stripped = strip_sql_literals_and_comments(original)

    body = stripped.strip()
    if body.endswith(";"):
        body = body[:-1]
    if ";" in body:
        raise ReadOnlyViolation(
            "检测到多条语句（分号拼接）。本系统一次只允许执行一条只读查询，请拆成单条 SELECT。"
        )

    tokens = body.split()
    first_keyword = tokens[0].upper() if tokens else ""
    if first_keyword not in ALLOWED_FIRST_KEYWORDS:
        raise ReadOnlyViolation(
            f"只允许以 SELECT 或 WITH 开头的只读查询，检测到的开头是「{first_keyword or '空'}」。"
            "本系统不执行任何写操作或 DDL。"
        )

    matched = _KEYWORD_RE.search(body)
    if matched:
        raise ReadOnlyViolation(
            f"检测到非只读关键字「{matched.group(0).upper()}」，本系统只允许只读查询，"
            "请改用 SELECT / WITH 重写。"
        )

    call = _FUNCTION_CALL_RE.search(body)
    if call:
        raise ReadOnlyViolation(
            f"检测到被禁用的函数调用「{call.group(1).upper()}()」，请改用可正常返回的聚合或筛选条件。"
        )

    return original.rstrip().rstrip(";").strip()


def is_readonly_sql(sql: str) -> bool:
    """只读校验的布尔版本，便于测试与外部判断。"""
    try:
        validate_readonly_sql(sql)
    except ReadOnlyViolation:
        return False
    return True
