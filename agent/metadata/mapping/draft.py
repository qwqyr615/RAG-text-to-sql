"""自动草拟 ``mapping.yaml``：把「扫库得到的结构 + 取值画像」变成一份可人审的草稿。

为什么需要自动草拟
------------------
接入一个新的客户库时，最费人力的不是写 SQL，而是**读 45 列没有注释的宽表**。
``mappings/mes_prod_log.mapping.yaml`` 就是这种场景的手写样本：列名是 MES 缩写
（``def_rate`` / ``stop_min`` / ``fpy`` / ``alarm_cnt``），数据库里一条 ``COMMENT``
都没有，语义完全靠人一列一列看数猜出来。45 列尚可人工完成，换成一个 300 列的
客户库就不可行 —— 于是有了本模块：先自动产出一份**草稿**，人只做审核与修正。

两个来源，各司其职
------------------
1. **规则匹配**（:func:`metadata.standard_fields.match_columns`）：列名与标准字段的
   候选名/别名归一化比对。它不需要网络、不会编造，但只认名字。
2. **大模型**：把每列的**取值画像**（样例值、取值范围、枚举取值）喂给模型，让它
   判断 ``util`` 到底是设备利用率还是资源利用率、``def_rate`` 存的是 3.9 还是 0.039。

规则与模型冲突时**规则赢**（唯一命中即视为可信）。原因是代价不对称：规则唯一命中
时错判概率很低，而模型在缺上下文时爱「补全」出一个看起来合理但错误的绑定；一个错误
的绑定不会报错，只会让生成的 SQL 静默算错 —— 本项目在 ``docs/DATA_MODEL.md`` 里
记录过同类静默错误（11 个指标以 TEXT 存、排序按字典序）。歧义列（如 ``util``）
规则不猜，标为 ``ambiguous`` 交给模型或人；模型给的绑定若引用了**不存在的标准字段**
一律丢弃并记录，绝不让它进 YAML。

单位换算为什么必须显式写出来
----------------------------
客户列存比例（``0.039``）、标准口径是百分数（``3.9``）时，映射必须写成
``def_rate * 100``。漏了这一步，「平均缺陷率」会静默差 100 倍且**任何数值都合格**，
人审时看不出来。所以 Prompt 里用具体例子讲清楚，审核 CLI（``mapping.cli review``）
再把所有换算逐条列出来让人签字。

可测试性
--------
本模块的所有产物都必须能**不联网**得到：``use_llm=False`` 走纯规则路径；
模型调用失败时记进 :attr:`DraftResult.llm_error` 并退回规则草稿，而不是抛异常。
测试用一个暴露 ``.invoke(messages) -> obj(.content)`` 的桩对象替换真实模型。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine

from metadata.inventory import TableInventory, discover_tables
from metadata.mapping.schema import (
    MappingError,
    MappingIssue,
    MappingProfile,
    ColumnSpec,
    StandardFieldBinding,
    TableMappingSpec,
    validate_profile,
)
from metadata.standard_fields import (
    AMBIGUOUS,
    MATCHED,
    STANDARD_FIELDS,
    ColumnMatch,
    StandardField,
    canonical_names,
    field_by_name,
    match_columns,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DRAFT_HEADER",
    "ColumnProfile",
    "DraftResult",
    "build_draft_prompt",
    "draft_mapping",
    "merge_llm_into_profile",
    "parse_llm_json",
    "profile_table",
    "render_draft_yaml",
    "write_draft",
]

#: 草稿文件头的第一行：任何被机器生成的映射都必须自报「未审核」。
DRAFT_HEADER = "# DRAFT — 未经人工审核，请勿直接投入生产"


# ----------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------
@dataclass
class ColumnProfile:
    """一列的结构 + 取值画像。"""

    table: str
    column: str
    data_type: str
    nullable: bool
    is_primary_key: bool
    comment: str
    sample_values: list[Any]
    distinct_count: int | None
    """可为 ``None``（未采集）。SQLite 等方言取不到时保持 ``None``，不伪造 0。"""

    min_value: Any = None
    max_value: Any = None
    numeric: bool = False

    def render_line(self) -> str:
        """渲染成 Prompt 里的一行描述。前缀 ``-`` 由调用方加，便于统一缩进。"""
        parts = [f"`{self.column}` {self.data_type or '未知类型'}"]
        if self.is_primary_key:
            parts.append("主键")
        parts.append("可空" if self.nullable else "非空")
        if self.comment:
            parts.append(f"注释：{self.comment}")
        else:
            parts.append("无注释")

        if self.distinct_count is not None:
            parts.append(f"去重值数≈{self.distinct_count}")
        if self.sample_values:
            rendered = "、".join(_render_value(item) for item in self.sample_values)
            parts.append(f"样例：{rendered}")
        else:
            parts.append("样例：无数据")
        if self.numeric and (self.min_value is not None or self.max_value is not None):
            parts.append(
                f"范围：{_render_value(self.min_value)} ~ {_render_value(self.max_value)}"
            )
        return "；".join(parts)


@dataclass
class DraftResult:
    """一次草拟的全部产物与账本。"""

    profile: MappingProfile
    issues: list[MappingIssue]
    matched: list[ColumnMatch]
    """规则匹配唯一命中的列。"""

    ambiguous: list[ColumnMatch]
    """规则匹配有歧义、需要人审定的列。"""

    unmapped: list[ColumnMatch]
    """没有任何标准字段认领的客户特有列。"""

    llm_used: bool = False
    llm_error: str = ""
    raw_llm_output: str = ""

    @property
    def has_errors(self) -> bool:
        return any(issue.is_error for issue in self.issues)

    def summary(self) -> str:
        return (
            f"表 {len(self.profile.tables)} 张，规则命中 {len(self.matched)} 列，"
            f"歧义 {len(self.ambiguous)} 列，未映射 {len(self.unmapped)} 列，"
            f"大模型：{'已使用' if self.llm_used else '未使用'}"
        )


# ----------------------------------------------------------------------
# 取值画像
# ----------------------------------------------------------------------
def _to_jsonable(value: Any) -> Any:
    """把数据库取值转成可 JSON 序列化 / 可稳定打印的值。

    日期时间与 ``Decimal`` 统一转字符串：它们进不了 JSON，且数值化会丢掉「这是
    日期型维度」这一关键信息 —— 模型需要看到 ``2024-03-01`` 才能判断这是时间列。
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _render_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return repr(value)
    return str(value)


def _is_numeric_type(raw_type: Any) -> bool:
    """判断 SQLAlchemy 类型对象是否数值型（用于决定要不要取 min/max）。"""
    try:
        from sqlalchemy import types as sqltypes  # noqa: PLC0415 - 延迟导入避免顶层耦合

        return isinstance(
            raw_type,
            (
                sqltypes.Numeric,
                sqltypes.Integer,
                sqltypes.Float,
                sqltypes.Interval,
            ),
        )
    except Exception:  # noqa: BLE001 - 类型判定失败不影响画像其余部分
        return False


def _quote(engine: Engine, name: str) -> str:
    """按方言引用标识符，防止列名含空格/关键字时 SQL 直接语法错误。"""
    try:
        return engine.dialect.identifier_preparer.quote(str(name))
    except Exception:  # noqa: BLE001 - 极端方言下退化为原样
        return str(name)


def _distinct_count(engine: Engine, table: str, column: str) -> int | None:
    """取该列的**近似**去重值数：按去重采样计数，取不到时返回 ``None``。

    刻意不写 ``COUNT(DISTINCT col)``：客户库可能是千万行宽表，一次精确去重就会
    拖垮接入流程。草稿阶段只需要「这列像枚举（3 个值）还是像连续度量（几千个值）」
    这个量级判断，采样足够，而且不会因为权限/超时把整个画像带崩。
    """
    from sqlalchemy import func  # noqa: PLC0415 - 仅在需要计数时导入

    try:
        statement = select(func.count()).select_from(
            select(func.distinct(text(_quote(engine, column))))
            .select_from(text(_quote(engine, table)))
            .limit(200)
            .subquery()
        )
        with engine.connect() as conn:
            value = conn.execute(statement).scalar()
        return int(value) if value is not None else None
    except Exception as exc:  # noqa: BLE001 - 计数失败只是少一项画像
        logger.debug("统计去重值数失败 %s.%s：%s", table, column, exc)
        return None


def _profile_column(
    engine: Engine,
    table: str,
    raw: Mapping[str, Any],
    *,
    primary_keys: set[str],
    sample_rows: int,
) -> ColumnProfile:
    """采集单列的画像；任何一步失败都只影响这一步的结果，不向上抛。"""
    name = str(raw.get("name") or "")
    data_type = str(raw.get("type") or "")
    nullable = bool(raw.get("nullable", True))
    comment = str(raw.get("comment") or "")

    profile = ColumnProfile(
        table=table,
        column=name,
        data_type=data_type,
        nullable=nullable,
        is_primary_key=name in primary_keys,
        comment=comment,
        sample_values=[],
        distinct_count=None,
    )
    if not name:
        return profile

    try:
        # 引用失败（极端方言）等同于「这一列查不了」：整列放弃取值画像，
        # 而不是退回未引用标识符 —— 后者在含空格/关键字的列名上会直接语法错误。
        quoted_column = _quote(engine, name)
        quoted_table = _quote(engine, table)

        # 样例值（去重取样）：同值重复出现会占满额度，掩盖真实取值分布
        if sample_rows > 0:
            try:
                statement = (
                    f"SELECT DISTINCT {quoted_column} AS __v FROM {quoted_table} "
                    f"WHERE {quoted_column} IS NOT NULL LIMIT {int(sample_rows)}"
                )
                with engine.connect() as conn:
                    rows = conn.execute(text(statement)).fetchall()
                profile.sample_values = [_to_jsonable(row[0]) for row in rows]
            except Exception as exc:  # noqa: BLE001 - 单列取样失败不影响其余列
                logger.debug("取样失败 %s.%s：%s", table, name, exc)

        if _is_numeric_type(raw.get("type")):
            try:
                statement = (
                    f"SELECT MIN({quoted_column}) AS __min, "
                    f"MAX({quoted_column}) AS __max FROM {quoted_table}"
                )
                with engine.connect() as conn:
                    row = conn.execute(text(statement)).fetchone()
                if row is not None:
                    profile.min_value = _to_jsonable(row[0])
                    profile.max_value = _to_jsonable(row[1])
                    profile.numeric = profile.min_value is not None
            except Exception as exc:  # noqa: BLE001 - 范围取不到就不标 numeric
                logger.debug("取范围失败 %s.%s：%s", table, name, exc)

        profile.distinct_count = _distinct_count(engine, table, name)
    except Exception as exc:  # noqa: BLE001 - 该列彻底放弃画像，结构信息仍保留
        logger.debug("列画像失败 %s.%s：%s", table, name, exc)
        profile.sample_values = []
        profile.min_value = None
        profile.max_value = None
        profile.numeric = False
        profile.distinct_count = None

    return profile


def profile_table(
    engine: Engine,
    table: str,
    *,
    sample_rows: int = 5,
    distinct_sample: int = 50,
) -> list[ColumnProfile]:
    """采集一张表所有列的画像。

    参数:
        engine: SQLAlchemy Engine（只读使用；这里全部是 ``SELECT``）
        table: 表名
        sample_rows: 每列最多取几个去重样例值
        distinct_sample: 去重值数的采样上限（当前实现固定按 200 行采样，保留参数
            是为了让调用方可以显式表达意图，后续换成精确计数时不改签名）

    单列失败（权限不足、类型不能比较、列已删除）只让那一列少一项画像，整体不失败：
    草稿的质量可以打折，但接入流程不能因为一列而中断。
    """
    inspector = inspect(engine)
    columns = list(inspector.get_columns(table))
    try:
        constraint = inspector.get_pk_constraint(table)
        constraints = constraint.get("constrained_columns") or []
        primary_keys = {str(item) for item in constraints}
    except Exception as exc:  # noqa: BLE001 - 取不到主键就按「无主键」处理
        logger.debug("读取主键失败 %s：%s", table, exc)
        primary_keys = set()

    profiles: list[ColumnProfile] = []
    for raw in columns:
        profiles.append(
            _profile_column(
                engine,
                table,
                raw,
                primary_keys=primary_keys,
                sample_rows=int(sample_rows),
            )
        )
    return profiles


# ----------------------------------------------------------------------
# Prompt 构建
# ----------------------------------------------------------------------
def _standard_field_lines() -> list[str]:
    """按业务分组渲染 45 个标准字段（名称/中文名/单位/类型/别名）。

    刻意把单位与别名都写出来：模型要靠「标准口径是百分数」才能识别出客户列的
    ``0.039`` 需要 ×100；要靠别名才能把 ``不良率`` 这种中文列名对上 ``defect_rate``。
    """
    by_group: dict[str, list[StandardField]] = {}
    for standard in STANDARD_FIELDS:
        by_group.setdefault(standard.group or "其他", []).append(standard)

    lines: list[str] = []
    for group, items in by_group.items():
        lines.append(f"[{group}]")
        for standard in items:
            aliases = "、".join(standard.aliases) or "—"
            candidates = "、".join(standard.column_candidates) or "—"
            lines.append(
                f"- {standard.name}（{standard.label}）单位：{standard.unit}；"
                f"类型：{standard.data_kind}；业务别名：{aliases}；"
                f"见过的客户列名：{candidates}"
            )
    return lines


def build_draft_prompt(
    profiles_by_table: dict[str, list[ColumnProfile]], *, database_type: str = ""
) -> str:
    """构造草稿生成的 Prompt（纯中文，要求模型只输出严格 JSON）。

    注意：整段 Prompt 用字符串拼接与 ``join`` 组装，**不用** ``str.format()`` ——
    列名、注释、样例值都来自客户数据库，任何一个花括号都会让 ``format`` 抛
    ``KeyError`` 或把内容吃掉。这也是本项目的硬约定：模型/外部数据不参与格式化。
    """
    lines: list[str] = []
    lines.append("你是数据仓库接入工程师，负责把一个客户数据库的物理列对齐到标准字段词典。")
    lines.append("")
    lines.append("## 一、标准字段词典（只能使用下列 name，不得自造）")
    lines.extend(_standard_field_lines())
    lines.append("")
    lines.append("## 二、客户库结构与取值画像")
    if database_type:
        lines.append(f"数据库类型：{database_type}")
    if not profiles_by_table:
        lines.append("（没有采集到任何列）")
    for table, profiles in profiles_by_table.items():
        lines.append(f"### 表 {table}")
        if not profiles:
            lines.append("- （该表没有列）")
        for profile in profiles:
            lines.append(f"- {profile.render_line()}")
        lines.append("")
    lines.append("## 三、输出要求")
    lines.append("1. 只输出一个 JSON 对象，**不要**任何解释文字、不要 Markdown 代码围栏。")
    lines.append("2. JSON 结构如下（键名必须完全一致）：")
    lines.append(
        '{"tables": [{"name": "表名", "role": "fact|dimension|bridge|snapshot|'
        'source|other", "description": "中文业务描述", "grain": "一行代表什么", '
        '"primary_key": "主键列名", "columns": [{"column": "物理列名", '
        '"standard_fields": [{"name": "标准字段名", "unit": "客户列单位", '
        '"scale": 100, "expression": "该列在 SQL 里的写法", '
        '"enum_values": ["取值1", "取值2"], "notes": "中文说明"}]}]}]}'
    )
    lines.append(
        "3. **没有标准字段可对应的列，直接省略 standard_fields 或整条 columns 项**，"
        "不要硬塞一个相近但语义不同的标准字段；列名照原样保留在 column 里更安全。"
    )
    lines.append(
        "4. **当客户列的存储单位与标准口径不同时，必须同时给出 scale 与 expression**，"
        "并在 notes 里说明。示例：客户列 def_rate 存的是比例 0.039，标准字段 defect_rate "
        "的单位是百分数；此时写 "
        '{"name": "defect_rate", "unit": "比例(0-1)", "scale": 100, '
        '"expression": "def_rate * 100", "notes": "客户列存 0.039，标准口径为 3.9"}。'
        "漏掉这一步会让所有指标静默差 100 倍，务必逐列检查。"
    )
    lines.append(
        "5. 单位一致时 expression 写裸列名，不要加多余函数包装；不要额外嵌套 AS 别名。"
    )
    lines.append(
        "6. 拿不准的列：宁可省略 standard_fields（留给人审），也不要猜 —— "
        "错误的绑定不会报错，只会让后续 SQL 静默算错。"
    )
    lines.append(
        "7. 每个标准字段在**同一张表内只能绑定一次**；若两列都像同一个标准字段，"
        "选更可信的一列，另一列留空。"
    )
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 容错 JSON 解析
# ----------------------------------------------------------------------
def parse_llm_json(text: str) -> dict:
    """从模型输出里解析出 JSON 对象，尽最大努力容忍常见格式污染。

    依次处理：
    1. 剥掉 Markdown 代码围栏（模型极爱输出 ```json ... ```）；
    2. 从第一个 ``{`` 开始做**括号配平扫描**（跳过字符串内部与转义字符），
       取出第一个完整对象 —— 于是「先说一段废话再给 JSON」「JSON 后面还有解释」
    都能解析；
    3. 仍然失败就抛 :class:`MappingError`，并把原文片段带进报错信息，
    因为接入失败时人最需要看到的就是「模型到底返回了什么」。
    """
    raw = str(text or "")
    if not raw.strip():
        raise MappingError("大模型返回为空，无法解析为 JSON")

    cleaned = _strip_code_fences(raw)
    payload = _extract_first_object(cleaned)
    if payload is None:
        snippet = cleaned.strip()[:400]
        raise MappingError(f"大模型输出里找不到完整的 JSON 对象，原文片段：{snippet}")

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise MappingError(
            f"大模型输出的 JSON 无法解析：{exc}；原文片段：{payload[:400]}"
        ) from exc

    if not isinstance(parsed, dict):
        raise MappingError(f"大模型输出的顶层应为对象（dict），实际是 {type(parsed).__name__}")
    return parsed


def _strip_code_fences(text: str) -> str:
    """去掉 Markdown 代码围栏（开围栏可能是 ```` ```json ````），其余内容原样保留。

    围栏行与正文可能混在废话里，所以这里的状态机按行推进：只要在围栏内就保留，
    围栏标记行本身一律丢弃。这样 ``开场白 + ```json + {...} + ``` + 收尾话``
    在剥离后仍是干净的一段文本，交给括号配平扫描。
    """
    lines = text.splitlines()
    body: list[str] = []
    inside = False
    for line in lines:
        if line.strip().startswith("```"):
            inside = not inside
            continue
        if inside:
            body.append(line)
    if body:
        return "\n".join(body)
    # 围栏不对称（模型只写了开围栏）：退化为「丢掉所有围栏行」的宽松处理
    return "\n".join(
        line for line in lines if not line.strip().startswith("```")
    )


def _extract_first_object(text: str) -> str | None:
    """取出第一个**括号配平的** ``{...}``，扫描时跳过字符串内部与转义字符。

    不做配平判断的朴素做法（取第一个 ``{`` 到最后一个 ``}``）在模型输出两段 JSON
    时会拼出一个非法串，报错信息还指向错误的位置。
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        # 这一段没有配平（例如模型输出被截断），尝试从下一个 { 重新开始
        start = text.find("{", start + 1)
    return None


# ----------------------------------------------------------------------
# 合并
# ----------------------------------------------------------------------
def _normalize_payload_tables(payload: Mapping) -> list[Mapping]:
    tables = payload.get("tables")
    if tables is None:
        # 容忍模型直接给一张表的对象（少一层包装）
        if "columns" in payload or "name" in payload:
            return [payload]
        raise MappingError("大模型输出的 JSON 缺少 tables 字段")
    if isinstance(tables, Mapping):
        return [tables]
    if not isinstance(tables, list):
        raise MappingError(f"大模型输出的 tables 应为列表，实际是 {type(tables).__name__}")
    return [item for item in tables if isinstance(item, Mapping)]


def _coerce_scale(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        scale = float(value)
    except (TypeError, ValueError):
        return None
    return scale


def _coerce_binding(
    raw: Mapping,
    *,
    column: str,
    base: StandardFieldBinding | None,
) -> StandardFieldBinding | None:
    """把模型给出的一条绑定转成 :class:`StandardFieldBinding`；字段非法时返回 ``None``。"""
    standard = str(raw.get("name") or "").strip()
    if not standard or field_by_name(standard) is None:
        return None

    scale = _coerce_scale(raw.get("scale"))
    expression = str(raw.get("expression") or "").strip()
    if not expression:
        expression = f"{column} * {scale:g}" if scale is not None else column

    enum_values = raw.get("enum_values") or ()
    if isinstance(enum_values, str):
        enum_values = (enum_values,)
    if not isinstance(enum_values, (list, tuple)):
        enum_values = ()

    unit = str(raw.get("unit") or "").strip()
    role = str(raw.get("role") or "").strip()
    notes = str(raw.get("notes") or "").strip()
    if base is not None:
        # 规则侧的既有信息更可靠（例如主键列一定是 key），模型没说的不覆盖
        unit = unit or base.unit
        role = role or base.role
        role = role or _role_for(standard)

    if enum_values:
        resolved_enum: tuple[str, ...] = tuple(str(item) for item in enum_values)
    else:
        resolved_enum = tuple(base.enum_values) if base is not None else ()

    return StandardFieldBinding(
        standard_field=standard,
        expression=expression,
        column=column,
        scale=scale if scale is not None else (base.scale if base else None),
        unit=unit,
        enum_values=resolved_enum,
        role=role,
        notes=notes or (base.notes if base else ""),
    )


def merge_llm_into_profile(
    base: MappingProfile,
    payload: dict,
    *,
    issues: list[MappingIssue] | None = None,
) -> MappingProfile:
    """把大模型提议的绑定叠加到规则草稿上，返回**新的** profile。

    三条硬规则：

    1. **规则唯一命中优先**。列已有规则绑定且模型提议的是同一个标准字段 → 补齐
       字段；提议的是另一个标准字段 → 丢弃并记一条 issue（模型在猜）。
    2. **不存在的标准字段一律丢弃**并记录。``util`` 被模型写成 ``utilization``
       这种不在词典里的名字，一旦写进 YAML 会让 ``validate_profile`` 直接失败，
       更糟的是有人为了「让它过」去改词典 —— 这里就地拦掉。
    3. **发现/排除声明保持不动**。``discovery``、``relationships``、``database``
       来自人工或规则，模型无权修改：它看不到库里有几张表，最容易把 discovery 写坏，
       而 discovery 一旦被写坏，Agent 要么看不到目标表，要么在等价表之间随机挑。

    歧义列（``base`` 里没有绑定）会被模型的结论**采纳**，这正是要模型来的原因。

    参数:
        base: 规则草稿（不被修改）
        payload: 已解析的模型 JSON
        issues: 传入一个列表即可**额外**收到本次合并的全部结论（丢弃了什么、为什么），
            便于调用方 / 测试做审计；返回的 profile 本身不带问题清单。
    """
    merged = _clone_profile(base)
    standard_names = set(canonical_names())
    issues = issues if issues is not None else []

    tables_by_name = {table.name: table for table in merged.tables}
    known_columns = {
        table.name: {column.column: column for column in table.columns}
        for table in merged.tables
    }

    for raw_table in _normalize_payload_tables(payload):
        table_name = str(raw_table.get("name") or "").strip()
        if not table_name:
            issues.append(MappingIssue("warning", "大模型输出里有表缺少 name，已跳过"))
            continue

        target = tables_by_name.get(table_name)
        raw_columns = raw_table.get("columns") or []
        if not isinstance(raw_columns, list):
            issues.append(
                MappingIssue(
                    "warning",
                    f"大模型输出的 columns 不是列表，已跳过：{type(raw_columns).__name__}",
                    f"tables[{table_name}]",
                )
            )
            raw_columns = []

        if target is None:
            # 模型凭空多给了一张表：只在它确实带了列时补一张，且角色收紧为 other，
            # 避免模型随手写的 fact 把指标口径落到一张空表上。
            if not raw_columns:
                issues.append(
                    MappingIssue(
                        "warning",
                        "大模型输出了一张不在扫描结果里的表且没有列，已忽略",
                        f"tables[{table_name}]",
                    )
                )
                continue
            target = TableMappingSpec(
                name=table_name, role=_coerce_role(raw_table.get("role"))
            )
            merged.tables.append(target)
            tables_by_name[table_name] = target
            known_columns[table_name] = {}
            issues.append(
                MappingIssue(
                    "warning",
                    "大模型提出了扫描结果里没有的表，已按草稿补入，请人工确认表名",
                    f"tables[{table_name}]",
                )
            )
        else:
            _overlay_table_meta(target, raw_table, issues)

        for raw_column in raw_columns:
            if not isinstance(raw_column, Mapping):
                continue
            column_name = str(raw_column.get("column") or "").strip()
            if not column_name:
                issues.append(
                    MappingIssue("warning", "大模型输出里有列缺少 column，已跳过", table_name)
                )
                continue

            column = known_columns[table_name].get(column_name)
            if column is None:
                # 客户列以扫库结果为准：模型写错的列名不进草稿，否则 validate 会失败
                issues.append(
                    MappingIssue(
                        "warning",
                        "大模型提出了扫描结果里没有的列，已忽略（列名可能是拼写的）",
                        f"{table_name}.{column_name}",
                    )
                )
                continue

            raw_bindings = raw_column.get("standard_fields") or []
            if not isinstance(raw_bindings, list):
                continue

            for raw_binding in raw_bindings:
                if not isinstance(raw_binding, Mapping):
                    continue
                standard = str(raw_binding.get("name") or "").strip()
                if not standard:
                    continue
                if standard not in standard_names:
                    issues.append(
                        MappingIssue(
                            "warning",
                            f"大模型给出的标准字段不在词典中，已丢弃：{standard}",
                            f"{table_name}.{column_name}",
                        )
                    )
                    continue

                existing = next(
                    (
                        item
                        for item in column.bindings
                        if item.standard_field == standard
                    ),
                    None,
                )
                if existing is not None:
                    merged_binding = _coerce_binding(
                        raw_binding, column=column_name, base=existing
                    )
                    if merged_binding is not None:
                        _fill_missing(existing, merged_binding)
                    continue

                if column.bindings:
                    # 一列已经绑定了别的标准字段：模型这次是「换个字段」，
                    # 属于猜测，记下来交给人，不直接采纳。
                    current = "、".join(item.standard_field for item in column.bindings)
                    issues.append(
                        MappingIssue(
                            "warning",
                            f"该列已由规则绑定到 {current}，大模型提议 {standard} 未被采纳，请人工判定",
                            f"{table_name}.{column_name}",
                        )
                    )
                    continue

                if standard in _bound_fields(target):
                    issues.append(
                        MappingIssue(
                            "warning",
                            f"标准字段 {standard} 在本表内已被其他列绑定，已丢弃该提议",
                            f"{table_name}.{column_name}",
                        )
                    )
                    continue

                binding = _coerce_binding(raw_binding, column=column_name, base=None)
                if binding is None:
                    continue
                column.bindings.append(binding)

    issues.extend(validate_profile(merged))
    merged.source_path = None
    return merged


def _clone_profile(profile: MappingProfile) -> MappingProfile:
    """深拷贝到「可安全改」的程度：表、列、绑定都是新对象。"""
    tables: list[TableMappingSpec] = []
    for table in profile.tables:
        columns = [
            ColumnSpec(
                column=column.column,
                bindings=[
                    StandardFieldBinding(
                        standard_field=binding.standard_field,
                        expression=binding.expression,
                        column=binding.column or column.column,
                        scale=binding.scale,
                        unit=binding.unit,
                        enum_values=tuple(binding.enum_values),
                        role=binding.role,
                        notes=binding.notes,
                    )
                    for binding in column.bindings
                ],
            )
            for column in table.columns
        ]
        tables.append(
            TableMappingSpec(
                name=table.name,
                role=table.role,
                description=table.description,
                grain=table.grain,
                primary_key=table.primary_key,
                time_column=table.time_column,
                columns=columns,
                notes=table.notes,
            )
        )
    return MappingProfile(
        schema_version=profile.schema_version,
        profile=profile.profile,
        description=profile.description,
        database=dict(profile.database),
        discovery=dict(profile.discovery),
        relationships=[dict(item) for item in profile.relationships],
        tables=tables,
        source_path=profile.source_path,
    )


def _overlay_table_meta(
    table: TableMappingSpec, raw_table: Mapping, issues: list[MappingIssue]
) -> None:
    """用模型给的表级语义补空位；已有的（人写的/规则给的）不覆盖。"""
    description = str(raw_table.get("description") or "").strip()
    grain = str(raw_table.get("grain") or "").strip()
    primary_key = str(raw_table.get("primary_key") or "").strip()

    if not table.description and description:
        table.description = description
    if not table.grain and grain:
        table.grain = grain
    if not table.primary_key and primary_key:
        columns = {column.column for column in table.columns}
        if primary_key in columns:
            table.primary_key = primary_key
        else:
            issues.append(
                MappingIssue(
                    "warning",
                    f"大模型给出的主键 {primary_key} 不在表的列里，已忽略",
                    f"tables[{table.name}]",
                )
            )
    if raw_table.get("role") and str(raw_table.get("role")) != table.role:
        # 角色影响指标口径落在哪张表上，不由模型改；只提示。
        issues.append(
            MappingIssue(
                "info",
                f"大模型认为该表角色是 {raw_table.get('role')}，草稿保留 {table.role}",
                f"tables[{table.name}]",
            )
        )


def _fill_missing(target: StandardFieldBinding, source: StandardFieldBinding) -> None:
    """把 ``source`` 里「target 没有的」信息补进 ``target``（不覆盖已有值）。"""
    if target.scale is None and source.scale is not None:
        target.scale = source.scale
    if not target.unit and source.unit:
        target.unit = source.unit
    if not target.enum_values and source.enum_values:
        target.enum_values = tuple(source.enum_values)
    if not target.role and source.role:
        target.role = source.role
    if not target.notes and source.notes:
        target.notes = source.notes
    # expression 只在「规则版是裸列名、模型版带换算」时更新，其余以规则版为准
    if source.expression and target.expression == target.column:
        target.expression = source.expression


def _bound_fields(table: TableMappingSpec) -> set[str]:
    return {binding.standard_field for binding in table.bindings()}


def _role_for(standard_name: str) -> str:
    standard = field_by_name(standard_name)
    if standard is None:
        return ""
    if standard.data_kind == "key":
        return "key"
    return "dimension" if standard.is_dimension else ""


def _coerce_role(value: Any) -> str:
    role = str(value or "").strip()
    allowed = {"fact", "dimension", "bridge", "snapshot", "source", "other"}
    return role if role in allowed else "other"


# ----------------------------------------------------------------------
# 规则草稿
# ----------------------------------------------------------------------
def _build_base_profile(
    inventory: TableInventory,
    *,
    profile_name: str,
    tables: Sequence[str],
    database_type: str,
) -> tuple[MappingProfile, list[ColumnMatch], list[ColumnMatch], list[ColumnMatch]]:
    """按规则匹配构造草稿 profile，并返回 ``matched / ambiguous / unmapped`` 账本。"""
    matched: list[ColumnMatch] = []
    ambiguous: list[ColumnMatch] = []
    unmapped: list[ColumnMatch] = []

    specs: list[TableMappingSpec] = []
    for table in tables:
        columns = list(inventory.columns.get(table) or [])
        verdicts = match_columns(columns, table=table)
        for verdict in verdicts:
            if verdict.status == MATCHED:
                matched.append(verdict)
            elif verdict.status == AMBIGUOUS:
                ambiguous.append(verdict)
            else:
                unmapped.append(verdict)

        primary_keys = list(inventory.primary_keys.get(table) or [])
        column_specs: list[ColumnSpec] = []
        seen_standard: set[str] = set()
        for verdict in verdicts:
            bindings: list[StandardFieldBinding] = []
            if verdict.status == MATCHED and verdict.standard_field:
                standard = field_by_name(verdict.standard_field)
                if standard is not None and standard.name not in seen_standard:
                    seen_standard.add(standard.name)
                    bindings.append(
                        StandardFieldBinding(
                            standard_field=standard.name,
                            expression=verdict.column,
                            column=verdict.column,
                            unit=(
                                standard.unit
                                if standard.unit and standard.unit != "—"
                                else ""
                            ),
                            role=_role_for(standard.name),
                            notes="规则自动匹配，待人审确认",
                        )
                    )
            column_specs.append(ColumnSpec(column=verdict.column, bindings=bindings))

        specs.append(
            TableMappingSpec(
                name=table,
                role=str(inventory.roles.get(table) or "fact"),
                description="自动草拟：待补充业务描述",
                grain="",
                primary_key=primary_keys[0] if primary_keys else "",
                columns=column_specs,
                notes="由 metadata.mapping.draft 自动生成，未经人工审核",
            )
        )

    excluded = _excluded_names(inventory)
    profile = MappingProfile(
        schema_version=_schema_version(),
        profile=profile_name,
        description=f"自动草拟的客户接入映射（{database_type or '未知方言'}）",
        database={"dialect": database_type} if database_type else {},
        # 无排除项时写空 dict 而不是 ``{"exclude": []}``：手写样本里没有这一层噪音，
        # 草稿与它保持同形，人审 diff 才不会满屏空列表。
        discovery={"exclude": excluded} if excluded else {},
        relationships=[],
        tables=specs,
    )
    return profile, matched, ambiguous, unmapped


def _excluded_names(inventory: TableInventory) -> list[str]:
    """从扫描账本里挑出「本库特有、值得写进 discovery.exclude」的表。

    只写因**显式排除清单**被挡下的表（通常是客户原始层/等价表），不写命中通用前缀的
    系统表 —— 后者由 :data:`metadata.inventory.DEFAULT_EXCLUDE_PREFIXES` 覆盖，
    写进 YAML 只会让文件里堆满 ``information_schema`` 这类噪音。
    """
    generic = ("命中排除前缀", "在排除清单中（系统表或原始层）")
    return [
        name
        for name, reason in inventory.excluded.items()
        if reason not in generic and "discovery.include" not in reason
    ]


def _schema_version() -> str:
    from metadata.mapping.schema import SCHEMA_VERSION  # noqa: PLC0415 - 避免循环导入

    return SCHEMA_VERSION


def _pick_tables(
    inventory: TableInventory, requested: Sequence[str] | None
) -> list[str]:
    if requested:
        wanted = [str(item) for item in requested]
        missing = [name for name in wanted if name not in inventory.all_tables]
        if missing:
            raise MappingError(
                "以下表在库里不存在：" + "、".join(missing)
                + f"（库里可见：{'、'.join(inventory.all_tables) or '无'}）"
            )
        return wanted
    return list(inventory.business_tables)


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def draft_mapping(
    engine: Engine,
    tables: list[str],
    *,
    profile_name: str,
    use_llm: bool = True,
    llm: Any = None,
    sample_rows: int = 5,
) -> DraftResult:
    """扫库 → 画像 → 规则匹配 →（可选）大模型 → 合并 → 校验。

    参数:
        engine: SQLAlchemy Engine
        tables: 要草拟的表名；表不存在时抛 :class:`MappingError`（早失败，
            免得生成一份指向空气的映射）
        profile_name: 写进 ``profile:`` 的名称
        use_llm: ``False`` 时**完全不碰模型**（离线、无 Key、CI 都走这条路）
        llm: 可注入的聊天模型（LangChain 风格：``invoke(messages) -> obj.content``）；
            ``None`` 且 ``use_llm=True`` 时用 :func:`core.llm.get_llm_by_provider`
        sample_rows: 每列取几个样例值进画像/Prompt

    模型调用失败**不**让草稿失败：把异常记进 :attr:`DraftResult.llm_error`，
    退回规则草稿。接入工程师拿到的不该是一次崩溃，而是「一份能用的兜底 + 一条原因」。
    """
    inventory = discover_tables(engine)
    selected = _pick_tables(inventory, tables)
    database_type = str(getattr(getattr(engine, "dialect", None), "name", "") or "")

    base, matched, ambiguous, unmapped = _build_base_profile(
        inventory,
        profile_name=profile_name,
        tables=selected,
        database_type=database_type,
    )

    result = DraftResult(
        profile=base,
        issues=[],
        matched=matched,
        ambiguous=ambiguous,
        unmapped=unmapped,
    )

    if not use_llm:
        result.issues = validate_profile(base)
        return result

    profiles_by_table = {
        table: profile_table(engine, table, sample_rows=sample_rows)
        for table in selected
    }
    prompt = build_draft_prompt(profiles_by_table, database_type=database_type)

    client = llm
    if client is None:
        try:
            from core.llm import get_llm_by_provider  # noqa: PLC0415 - 未用模型时不导入

            client = get_llm_by_provider()
        except Exception as exc:  # noqa: BLE001 - 拿不到模型时退回规则草稿
            result.llm_error = f"无法创建大模型实例：{exc}"
            result.issues = validate_profile(base)
            return result

    try:
        raw = _invoke_llm(client, prompt)
    except Exception as exc:  # noqa: BLE001 - 模型/网络问题不该毁掉草稿
        result.llm_error = f"调用大模型失败：{exc}"
        result.issues = validate_profile(base)
        return result

    result.raw_llm_output = raw
    try:
        payload = parse_llm_json(raw)
    except MappingError as exc:
        result.llm_error = str(exc)
        result.issues = validate_profile(base)
        return result

    merge_issues: list[MappingIssue] = []
    result.profile = merge_llm_into_profile(base, payload, issues=merge_issues)
    result.llm_used = True
    # 合并账本（模型提议被丢弃/被规则顶回的原因）在校验结论之前，人审时先看它：
    # 「模型想绑定 X 但被丢弃」比「校验通过」更能说明这一轮草稿的成色。
    result.issues = merge_issues + validate_profile(result.profile)
    return result


def _invoke_llm(llm: Any, prompt: str) -> str:
    """调用聊天模型并把返回统一成字符串（兼容 ``.content`` 为字符串或分块列表）。"""
    if not hasattr(llm, "invoke"):
        raise TypeError(f"传入的 llm 没有 invoke 方法：{type(llm).__name__}")
    try:
        response = llm.invoke([{"role": "user", "content": prompt}])
    except TypeError:
        # 少数实现只接受纯字符串
        response = llm.invoke(prompt)
    return _response_text(response)


def _response_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if content is None:
        return str(response or "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping) and "text" in item:
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


# ----------------------------------------------------------------------
# 渲染与落盘
# ----------------------------------------------------------------------
_BINDING_ORDER = ("name", "expression", "scale", "unit", "enum_values", "role", "notes")


def _binding_dict(binding: StandardFieldBinding) -> dict[str, Any]:
    values = {
        "name": binding.standard_field,
        "expression": binding.expression,
        "scale": binding.scale,
        "unit": binding.unit,
        "enum_values": list(binding.enum_values),
        "role": binding.role,
        "notes": binding.notes,
    }
    # 键序固定为 _BINDING_ORDER，并丢掉空值 —— 与手写的
    # mappings/mes_prod_log.mapping.yaml 逐键对齐，人审时 diff 才不会满屏噪音。
    return {
        key: values[key]
        for key in _BINDING_ORDER
        if values.get(key) not in (None, "", [], ())
    }


def _table_dict(table: TableMappingSpec) -> dict[str, Any]:
    return {
        "name": table.name,
        "role": table.role,
        "description": table.description,
        "grain": table.grain,
        "primary_key": table.primary_key,
        "time_column": table.time_column,
        "notes": table.notes,
        "columns": [
            {
                "column": column.column,
                "standard_fields": [
                    _binding_dict(binding) for binding in column.bindings
                ],
            }
            for column in table.columns
        ],
    }


def render_draft_yaml(profile: MappingProfile) -> str:
    """把草稿渲染成 YAML 文本（形状与键序对齐 ``mappings/mes_prod_log.mapping.yaml``）。

    文件头必须带 ``# DRAFT`` 标记。草稿的定位是**审核输入**而不是成品：一份没有
    标记的草稿被误当成生效映射，后果是未经人审的单位换算直接进生产 SQL —— 这是
    最贵的一类错误，所以标记写在第一行，任何 diff/编辑器都能立刻看到。
    """
    yaml = _require_yaml()
    payload = {
        "schema_version": profile.schema_version,
        "profile": profile.profile,
        "description": profile.description,
        "database": dict(profile.database),
        "discovery": dict(profile.discovery),
        "relationships": [dict(item) for item in profile.relationships],
        "tables": [_table_dict(table) for table in profile.tables],
    }
    body = yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return f"{DRAFT_HEADER}\n{_meta_header(profile)}\n\n{body}"


def _meta_header(profile: MappingProfile) -> str:
    """草稿的二段说明：生成来源与「必须做什么」。"""
    tables = "、".join(profile.table_names) or "（无）"
    return "\n".join(
        [
            "# 由 metadata.mapping.draft 自动生成，profile=" + (profile.profile or "（未命名）"),
            "# 覆盖表：" + tables,
            "# 审核要点：",
            "#   1. 逐列确认标准字段绑定是否正确（规则命中与模型提议都可能有错）；",
            "#   2. 逐条确认 scale / expression 代表的单位换算（漏一条就整体差一个倍数）；",
            "#   3. 确认 discovery.exclude 没有把需要暴露的表挡在门外。",
            "# 审核后请删除本文件头，并把文件改名去掉 draft 字样。",
        ]
    )


def _require_yaml() -> Any:
    try:
        import yaml  # noqa: PLC0415 - 延迟导入，缺依赖时给出可操作的报错
    except ImportError as exc:  # pragma: no cover - 环境问题
        raise MappingError(
            "缺少 PyYAML，无法写出草稿映射。请执行：pip install PyYAML"
        ) from exc
    return yaml


def write_draft(profile: MappingProfile, path, *, header: str = "") -> Path:
    """把草稿写到 ``path`` 并返回该路径；``header`` 会追加到文件头。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = render_draft_yaml(profile)
    extra = str(header or "").strip()
    if extra:
        lines = extra.splitlines()
        commented = "\n".join(
            line if line.lstrip().startswith("#") else f"# {line}" for line in lines
        )
        text = f"{text}\n\n{commented}\n"
    target.write_text(text, encoding="utf-8")
    return target
