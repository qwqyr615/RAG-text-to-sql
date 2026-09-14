"""发现模式：不硬编码业务表名，直接扫描数据库并套用排除规则。

替代原先的 ``PRESET_BUSINESS_TABLES = ["fact_production_record"]``。原来的写法把
「业务表白名单」写死在代码里，换一个客户库就必须改代码；现在改成：

1. 扫描库里所有表（``inspector.get_table_names()``）；
2. 套用排除规则挡掉系统表与原始层（前缀 / 精确名 / 角色）；
3. 剩下的按业务分层（``service`` 服务层 / ``raw`` 原始层 / ``system`` 系统层）标注。

排除规则有两个来源，**取并集**：

- ``core.config.settings.discovery_exclude_tables``：跨客户通用的兜底
  （MySQL ``information_schema`` 之类）；
- ``mapping.yaml`` 的 ``discovery.exclude`` / ``exclude_prefixes``：客户库特有
  （例如原始 CSV 导入表 ``intelligent_production_iiot``）。

为什么默认排除要在配置里而不是代码里
------------------------------------
原始层表 ``intelligent_production_iiot`` 是同一个 CSV 的原样导入，字段类型未治理，
和 ``fact_production_record`` 是同一批数据的两种形态。把它一起暴露给 Agent，
模型会在两张内容相同的表之间随机挑一张，并且可能挑到脏类型那张 —— 实测过
（见 ``docs/DATA_MODEL.md``：11 个数值指标以 TEXT 存储，排序按字典序，静默算错）。
因此排除是**正确性**要求，不是洁癖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from metadata.mapping.schema import MappingProfile

__all__ = [
    "DEFAULT_EXCLUDE_PREFIXES",
    "DEFAULT_EXCLUDE_TABLES",
    "TableInventory",
    "discover_tables",
]

#: 各家数据库/schema 元数据表的前缀，任何客户库都不该暴露给 Agent。
DEFAULT_EXCLUDE_PREFIXES: tuple[str, ...] = (
    "information_schema",
    "performance_schema",
    "mysql.",
    "sys.",
    "pg_",
    "sqlite_",
    "_prisma",
    "alembic_version",
    "django_",
    "flyway_schema_history",
)

#: 精确排除的常见系统表。
DEFAULT_EXCLUDE_TABLES: tuple[str, ...] = (
    "alembic_version",
    "schema_migrations",
    "flyway_schema_history",
    "sqlite_sequence",
)

#: 角色 -> 中文说明，用于元数据 JSON 与前端展示。
ROLE_LABELS = {
    "fact": "事实表",
    "dimension": "维表",
    "bridge": "桥接表",
    "snapshot": "快照表",
    "source": "原始层",
    "other": "其他",
}


@dataclass
class TableInventory:
    """一次库结构扫描的结果。"""

    all_tables: list[str] = field(default_factory=list)
    """库里实际存在的全部表。"""

    business_tables: list[str] = field(default_factory=list)
    """排除系统表/原始层之后，Agent 可见的表。"""

    excluded: dict[str, str] = field(default_factory=dict)
    """``表名 -> 被排除的原因``，用于向接入工程师解释「为什么看不到这张表」。"""

    roles: dict[str, str] = field(default_factory=dict)
    """``表名 -> 角色``（fact / dimension / source / other）。"""

    columns: dict[str, list[str]] = field(default_factory=dict)
    """``表名 -> 列名列表``，避免上层再查一次 inspector。"""

    primary_keys: dict[str, list[str]] = field(default_factory=dict)

    def summary(self) -> str:
        parts = [
            f"库中共 {len(self.all_tables)} 张表",
            f"业务表 {len(self.business_tables)} 张",
        ]
        if self.excluded:
            parts.append(f"已排除 {len(self.excluded)} 张")
        return "，".join(parts)


def discover_tables(
    engine: Engine,
    *,
    include: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
    exclude_prefixes: Sequence[str] | None = None,
    roles: Mapping[str, str] | None = None,
    profile: MappingProfile | None = None,
) -> TableInventory:
    """扫描数据库并套用排除规则，返回 :class:`TableInventory`。

    参数:
        engine: SQLAlchemy Engine
        include: 显式 include 列表（非空时**只**返回这里声明的表，仍会套排除规则）
        exclude: 额外排除的表名
        exclude_prefixes: 额外排除的表名前缀
        roles: ``表名 -> 角色`` 覆盖
        profile: ``mapping.yaml`` 的解析结果；其中的 ``discovery`` 段与
            ``tables`` 的 role 声明会自动并入
    """
    inspector = inspect(engine)
    all_tables = sorted(inspector.get_table_names())

    profile_discovery = dict(profile.discovery) if profile is not None else {}
    include_names = list(
        include if include is not None else (profile_discovery.get("include") or [])
    )
    exclude_names = {
        str(item)
        for item in (
            list(exclude or [])
            + list(profile_discovery.get("exclude") or [])
        )
    }
    exclude_prefixes_all = tuple(
        str(item).lower()
        for item in (
            list(DEFAULT_EXCLUDE_PREFIXES)
            + list(exclude_prefixes or [])
            + list(profile_discovery.get("exclude_prefixes") or [])
        )
    )
    excluded_tables = set(DEFAULT_EXCLUDE_TABLES) | exclude_names

    role_map: dict[str, str] = dict(roles or {})
    if profile is not None:
        for table in profile.tables:
            role_map.setdefault(table.name, table.role)

    inventory = TableInventory(all_tables=all_tables)

    candidates = all_tables
    if include_names:
        wanted = [name for name in include_names if name in all_tables]
        missing = [name for name in include_names if name not in all_tables]
        for name in missing:
            inventory.excluded[name] = "discovery.include 声明了但库里不存在"
        candidates = wanted

    for table in candidates:
        reason = _exclusion_reason(table, excluded_tables, exclude_prefixes_all)
        if reason:
            inventory.excluded[table] = reason
            continue
        inventory.business_tables.append(table)
        inventory.roles[table] = role_map.get(table, "other")

    inventory.columns = {
        table: [str(column["name"]) for column in inspector.get_columns(table)]
        for table in all_tables
    }
    inventory.primary_keys = {
        table: [
            str(name)
            for name in (
                inspector.get_pk_constraint(table).get("constrained_columns") or []
            )
        ]
        for table in all_tables
    }

    return inventory


def _exclusion_reason(
    table: str, excluded: Iterable[str], prefixes: Sequence[str]
) -> str:
    if table in set(excluded):
        return "在排除清单中（系统表或原始层）"
    lowered = table.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return f"命中排除前缀 {prefix!r}"
    return ""


def inventory_to_dict(
    inventory: TableInventory, columns: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """把扫描结果序列化成 JSON 友好结构，供 ``mapping.cli discover`` 输出。"""
    return {
        "all_tables": inventory.all_tables,
        "business_tables": inventory.business_tables,
        "excluded": inventory.excluded,
        "roles": inventory.roles,
        "column_counts": {
            table: len(inventory.columns.get(table) or [])
            for table in inventory.all_tables
        },
    }
