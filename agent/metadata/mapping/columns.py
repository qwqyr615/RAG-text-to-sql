"""把「标准字段口径的 SQL」改写成「客户列口径的 SQL」。

为什么需要它
------------
RAG 示例库（``rag/examples/sql_examples.json``）与评测参照 SQL 都是按**标准字段**
写成的（``AVG(defect_rate)``）。面对一张客户表（列名叫 ``def_rate``）时，把这些示例
原样塞进 Prompt，模型会照着编出客户表里并不存在的列名 —— 示例从帮助变成污染源。

两个用法：

1. **改写 RAG 示例**：注入 Prompt 前把示例 SQL 翻译成客户列名，示例立刻变成
   「客户表上正确可执行」的示范；
2. **生成客户侧参照 SQL**：评测里把标准参照 SQL 翻译后执行，用来验证映射本身是否
   语义完整（见 ``scripts/build_customer_cases.py``）。

实现要点
--------
1. **等长掩码**。先构造一份与原文**逐字符等长**的掩码：普通字符原样保留，字符串
   字面量内容、引号标识符内容与注释内容替换为 ``\\x01``。于是「掩码上的下标」就等于
   「原文上的下标」，可以在掩码上定位、在原文上取值。

   注意**不能**直接用 ``tools.sql_guard.strip_sql_literals_and_comments`` 的返回值
   当掩码：它只清空字面量内容、保留定界符，因此长度会变（``'Product_A'`` 变成 ``''``），
   偏移会全部错位。实测代价是 ``GROUP BY production_line`` 被改成
   ``GROUP BY produc`` + 残留 ``tion_line``，SQL 报 Unknown column。

2. **命中即跳过**。掩码里以 ``\\x01`` 开头的匹配一律不替换 —— 那个标识符在字面量
   或注释内部，不是真标识符。

3. **最长优先 + 词边界**。正则按「长名先试」的顺序排列分支，配合
   ``(?<![\\w$]) ... (?![\\w$])``，``def_rate`` 不会吃掉 ``avg_def_rate`` 的一部分。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

__all__ = ["ColumnRewriter", "RewriteResult", "find_identifiers", "mask_literals"]

#: 掩码中代表「非标识符字符」的占位符
HOLE = "\x01"


def mask_literals(sql: str) -> str:
    """构造与 ``sql`` **逐字符等长**的标识符掩码。

    字符串字面量内容、反引号标识符内容、``--`` / ``#`` / ``/* */`` 注释内容被替换为
    ``\\x01``；定界符本身（引号、反引号、注释符）保留，便于识别结构。

    状态机与 ``tools.sql_guard`` 保持一致，包括 MySQL 可执行注释 ``/*! ... */``
    的处理（其内容 MySQL 会真正执行，因此在这里也当作**代码**而不是注释，不掩码）。
    """
    text = sql or ""
    out = list(text)
    length = len(text)

    def blank(start: int, end: int) -> None:
        for position in range(max(0, start), min(end, length)):
            out[position] = HOLE

    index = 0
    while index < length:
        char = text[index]

        # 字符串字面量 / 引号标识符
        if char in ("'", '"', "`"):
            quote = char
            cursor = index + 1
            while cursor < length:
                current = text[cursor]
                if current == "\\" and quote != "`":
                    cursor += 2
                    continue
                if current == quote:
                    if cursor + 1 < length and text[cursor + 1] == quote:
                        cursor += 2  # 双写转义，仍在字面量内
                        continue
                    break
                cursor += 1
            blank(index + 1, cursor)
            index = cursor + 1
            continue

        # 行注释 --
        if char == "-" and text.startswith("--", index):
            newline = text.find("\n", index)
            end = length if newline == -1 else newline
            blank(index, end)
            index = end
            continue

        # 行注释 #
        if char == "#":
            newline = text.find("\n", index)
            end = length if newline == -1 else newline
            blank(index, end)
            index = end
            continue

        # 块注释（MySQL 可执行注释除外）
        if char == "/" and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            body_end = end if end != -1 else length
            if not text.startswith("/*!", index):
                blank(index + 2, body_end)
            index = length if end == -1 else end + 2
            continue

        index += 1

    return "".join(out)


@dataclass
class RewriteResult:
    """一次改写的产物与账本。"""

    sql: str
    applied: dict[str, str] = field(default_factory=dict)
    """实际生效的 ``原标识符 -> 替换表达式``，供人工核对。"""

    @property
    def changed(self) -> bool:
        return bool(self.applied)


class ColumnRewriter:
    """按映射把标准字段口径的 SQL 改写成客户列口径。

    参数:
        replacements: ``标准字段名 -> 客户侧表达式``（例如 ``def_rate`` 或
            ``def_rate * 100``）。由 :mod:`metadata.mapping.compiler` 产出。
        reverse: ``客户列名 -> [可能的标准字段名]``。用于两侧来源混写的 SQL
            （历史示例里命名风格不统一时很常见）。这里是**多对多**：同一个客户列
            可能对应多个标准字段（``util`` 既像设备利用率又像资源利用率），
            这种列不参与反向改写，避免静默改错。
    """

    def __init__(
        self,
        replacements: Mapping[str, str],
        *,
        reverse: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        merged: dict[str, str] = {
            str(key): str(value)
            for key, value in replacements.items()
            if key and value
        }

        for customer_column, standard_names in (reverse or {}).items():
            names = [str(name) for name in standard_names]
            if len(names) != 1:
                continue  # 歧义列不反向改写
            expression = merged.get(names[0])
            if expression is not None:
                merged.setdefault(str(customer_column), expression)

        # 最长优先：正则的优先匹配顺序由分支顺序决定，长名先试才不会截断长名
        self._ordered = sorted(merged.items(), key=lambda item: -len(item[0]))
        self._lookup = {key.lower(): value for key, value in self._ordered}
        keys = [key for key, _ in self._ordered if key]
        self._pattern = (
            re.compile(
                r"(?<![A-Za-z0-9_$])(?:"
                + "|".join(re.escape(key) for key in keys)
                + r")(?![A-Za-z0-9_$])",
                re.IGNORECASE,
            )
            if keys
            else None
        )

    def rewrite(self, sql: str) -> RewriteResult:
        """改写一条 SQL；没有命中任何规则时原样返回。"""
        text = sql or ""
        if self._pattern is None or not text.strip():
            return RewriteResult(sql=text)

        mask = mask_literals(text)
        applied: dict[str, str] = {}
        out: list[str] = []
        cursor = 0

        for match in self._pattern.finditer(mask):
            if mask[match.start()] == HOLE:
                continue  # 落在字面量/注释内部，不是真标识符
            token = text[match.start() : match.end()]
            expression = self._lookup.get(token.lower())
            if expression is None:
                continue

            out.append(text[cursor : match.start()])
            out.append(expression)
            cursor = match.end()
            applied[token] = expression

        out.append(text[cursor:])
        return RewriteResult(sql="".join(out), applied=applied)


def find_identifiers(sql: str, names: Iterable[str]) -> list[str]:
    """返回 ``sql`` 中真实出现过的 ``names`` 成员（不看字面量与注释内部）。"""
    mask = mask_literals(sql)
    found: list[str] = []
    for name in names:
        pattern = re.compile(
            r"(?<![A-Za-z0-9_$])" + re.escape(name) + r"(?![A-Za-z0-9_$])",
            re.IGNORECASE,
        )
        for match in pattern.finditer(mask):
            if mask[match.start()] == HOLE:
                continue
            found.append(name)
            break
    return found
