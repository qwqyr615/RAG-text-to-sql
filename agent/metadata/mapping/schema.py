"""``mapping.yaml`` 的 schema、加载与校验。

这份文件是**客户接入契约**：它把客户的物理列对齐到标准字段，并补上数据库里
读不出来的语义（单位、枚举取值、grain、表间关系）。设计目标是「可 review、
可 diff、可校验」，所以：

- 全部字段有显式校验，错误在 ``mapping.cli validate`` 阶段就暴露，而不是等到
  模型生成了一条算错的 SQL；
- 未知键一律报错（``unknown keys``），防止写错键名后被静默忽略；
- 缺失映射不报错、但要能被统计出来 —— 「客户表里有 12 列没有标准口径」是正常
  情况，应该展示给接入工程师看，而不是当成失败。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from metadata.standard_fields import canonical_names

__all__ = [
    "MappingError",
    "MappingProfile",
    "TableMappingSpec",
    "ColumnSpec",
    "StandardFieldBinding",
    "MappingIssue",
    "load_profile",
    "parse_profile",
    "SCHEMA_VERSION",
]

SCHEMA_VERSION = "1.0"

_TABLE_KEYS = {
    "name",
    "role",
    "description",
    "grain",
    "primary_key",
    "time_column",
    "columns",
    "notes",
}
_COLUMN_KEYS = {"column", "standard_fields"}
_BINDING_KEYS = {"name", "expression", "scale", "unit", "enum_values", "role", "notes"}
_TOP_KEYS = {
    "schema_version",
    "profile",
    "description",
    "database",
    "discovery",
    "relationships",
    "tables",
}
_DISCOVERY_KEYS = {"include", "exclude", "exclude_prefixes", "exclude_roles"}
_RELATION_KEYS = {
    "source_table",
    "source_column",
    "target_table",
    "target_column",
    "relation_type",
    "source_standard_field",
    "target_standard_field",
}
_DATABASE_KEYS = {"dialect", "url_env", "note"}

_VALID_ROLES = {"fact", "dimension", "bridge", "snapshot", "source", "other"}


class MappingError(ValueError):
    """``mapping.yaml`` 不合法（结构或取值错误）。"""


@dataclass
class MappingIssue:
    """一条校验结论。``level`` 为 ``error`` 时映射不可用。"""

    level: str
    message: str
    where: str = ""

    def render(self) -> str:
        prefix = f"[{self.level.upper()}]"
        return f"{prefix} {self.where + '：' if self.where else ''}{self.message}"

    @property
    def is_error(self) -> bool:
        return self.level == "error"


@dataclass
class StandardFieldBinding:
    """一条「客户列 -> 标准字段」绑定。"""

    standard_field: str
    expression: str
    column: str = ""
    scale: float | None = None
    unit: str = ""
    enum_values: tuple[str, ...] = ()
    role: str = ""
    notes: str = ""

    @property
    def is_plain(self) -> bool:
        """是否为「裸列名」（没有单位换算 / 函数包装）。"""
        return self.expression == self.column or self.expression == self.standard_field


@dataclass
class ColumnSpec:
    """客户表里的一列，以及它承载的标准字段。"""

    column: str
    bindings: list[StandardFieldBinding] = field(default_factory=list)

    @property
    def standard_fields(self) -> list[str]:
        return [binding.standard_field for binding in self.bindings]


@dataclass
class TableMappingSpec:
    """一张客户表的映射。"""

    name: str
    role: str = "fact"
    description: str = ""
    grain: str = ""
    primary_key: str = ""
    time_column: str = ""
    columns: list[ColumnSpec] = field(default_factory=list)
    notes: str = ""

    def bindings(self) -> Iterator[StandardFieldBinding]:
        for column in self.columns:
            yield from column.bindings


@dataclass
class MappingProfile:
    """``mapping.yaml`` 的内存表示。"""

    schema_version: str = SCHEMA_VERSION
    profile: str = ""
    description: str = ""
    database: dict[str, Any] = field(default_factory=dict)
    discovery: dict[str, Any] = field(default_factory=dict)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    tables: list[TableMappingSpec] = field(default_factory=list)
    source_path: Path | None = None

    # -- 便捷查询 ------------------------------------------------------
    @property
    def table_names(self) -> list[str]:
        return [table.name for table in self.tables]

    def table(self, name: str) -> TableMappingSpec | None:
        for table in self.tables:
            if table.name == name:
                return table
        return None

    def bindings_for_table(self, table_name: str) -> dict[str, StandardFieldBinding]:
        """``标准字段名 -> 绑定``（同一表内标准字段不允许重复，校验会挡住）。"""
        table = self.table(table_name)
        if table is None:
            return {}
        return {binding.standard_field: binding for binding in table.bindings()}

    def expression_map(self, table_name: str) -> dict[str, str]:
        """``标准字段名 -> 客户侧表达式``，喂给 :class:`ColumnRewriter`。"""
        return {
            name: binding.expression
            for name, binding in self.bindings_for_table(table_name).items()
        }

    def reverse_map(self, table_name: str) -> dict[str, list[str]]:
        """``客户列名 -> [标准字段名]``，用于两侧混写的 SQL 反向改写。"""
        table = self.table(table_name)
        if table is None:
            return {}
        reverse: dict[str, list[str]] = {}
        for column in table.columns:
            for binding in column.bindings:
                bucket = reverse.setdefault(binding.column or column.column, [])
                if binding.standard_field not in bucket:
                    bucket.append(binding.standard_field)
        return reverse

    def grain_of(self, table_name: str) -> str:
        table = self.table(table_name)
        return table.grain if table else ""

    def to_dict(self) -> dict[str, Any]:
        """回写成 YAML 友好的字典（草稿生成器与审核 CLI 用）。"""
        return {
            "schema_version": self.schema_version,
            "profile": self.profile,
            "description": self.description,
            "database": dict(self.database),
            "discovery": dict(self.discovery),
            "relationships": [dict(item) for item in self.relationships],
            "tables": [
                {
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
                                {
                                    key: value
                                    for key, value in (
                                        ("name", binding.standard_field),
                                        ("expression", binding.expression),
                                        (
                                            "scale",
                                            binding.scale,
                                        ),
                                        ("unit", binding.unit),
                                        (
                                            "enum_values",
                                            list(binding.enum_values),
                                        ),
                                        ("role", binding.role),
                                        ("notes", binding.notes),
                                    )
                                    if value not in (None, "", [], ())
                                }
                                for binding in column.bindings
                            ],
                        }
                        for column in table.columns
                    ],
                }
                for table in self.tables
            ],
        }


# ----------------------------------------------------------------------
# 加载
# ----------------------------------------------------------------------
def _require_mapping() -> Any:
    try:
        import yaml  # noqa: PLC0415 - 延迟导入，缺依赖时给出可操作的报错
    except ImportError as exc:  # pragma: no cover - 环境问题
        raise MappingError(
            "缺少 PyYAML，无法读取 mapping.yaml。请执行：pip install PyYAML"
        ) from exc
    return yaml


def load_profile(path: str | Path) -> MappingProfile:
    """从文件加载 ``mapping.yaml``。"""
    source = Path(path)
    if not source.is_file():
        raise MappingError(f"映射文件不存在：{source}")
    yaml = _require_mapping()
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - YAML 语法错误要转成 MappingError
        raise MappingError(f"解析 YAML 失败（{source}）：{exc}") from exc
    if payload is None:
        raise MappingError(f"映射文件为空：{source}")
    if not isinstance(payload, dict):
        raise MappingError(f"映射文件顶层应为映射（dict），实际是 {type(payload).__name__}")

    profile = parse_profile(payload)
    profile.source_path = source
    issues = validate_profile(profile)
    errors = [issue for issue in issues if issue.is_error]
    if errors:
        raise MappingError(
            "映射文件校验未通过：\n"
            + "\n".join(f"  - {issue.render()}" for issue in errors)
        )
    return profile


def _check_keys(
    payload: Mapping[str, Any], allowed: set[str], where: str, issues: list[MappingIssue]
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        issues.append(
            MappingIssue("error", f"未知键 {unknown}（允许：{sorted(allowed)}）", where)
        )


def parse_profile(payload: Mapping[str, Any]) -> MappingProfile:
    """把已解析的 YAML 字典转成 :class:`MappingProfile`（不做业务校验）。"""
    issues: list[MappingIssue] = []
    _check_keys(payload, _TOP_KEYS, "<top>", issues)

    discovery = payload.get("discovery") or {}
    if not isinstance(discovery, dict):
        raise MappingError("discovery 应为映射（dict）")
    _check_keys(discovery, _DISCOVERY_KEYS, "discovery", issues)

    tables: list[TableMappingSpec] = []
    raw_tables = payload.get("tables") or []
    if not isinstance(raw_tables, list):
        raise MappingError("tables 应为列表")

    for index, raw_table in enumerate(raw_tables):
        where = f"tables[{index}]"
        if not isinstance(raw_table, dict):
            raise MappingError(f"{where} 应为映射（dict）")
        _check_keys(raw_table, _TABLE_KEYS, where, issues)

        name = str(raw_table.get("name") or "").strip()
        if not name:
            issues.append(MappingIssue("error", "缺少 name", where))

        columns: list[ColumnSpec] = []
        raw_columns = raw_table.get("columns") or []
        if not isinstance(raw_columns, list):
            raise MappingError(f"{where}.columns 应为列表")

        for column_index, raw_column in enumerate(raw_columns):
            column_where = f"{where}.columns[{column_index}]"
            if not isinstance(raw_column, dict):
                raise MappingError(f"{column_where} 应为映射（dict）")
            _check_keys(raw_column, _COLUMN_KEYS, column_where, issues)

            column_name = str(raw_column.get("column") or "").strip()
            if not column_name:
                issues.append(MappingIssue("error", "缺少 column", column_where))
                continue

            bindings: list[StandardFieldBinding] = []
            raw_bindings = raw_column.get("standard_fields") or []
            if not isinstance(raw_bindings, list):
                raise MappingError(f"{column_where}.standard_fields 应为列表")

            for binding_index, raw_binding in enumerate(raw_bindings):
                binding_where = f"{column_where}.standard_fields[{binding_index}]"
                if not isinstance(raw_binding, dict):
                    raise MappingError(f"{binding_where} 应为映射（dict）")
                _check_keys(raw_binding, _BINDING_KEYS, binding_where, issues)

                standard = str(raw_binding.get("name") or "").strip()
                if not standard:
                    issues.append(MappingIssue("error", "缺少 name", binding_where))
                    continue

                scale = raw_binding.get("scale")
                if scale is not None:
                    try:
                        scale = float(scale)
                    except (TypeError, ValueError):
                        issues.append(
                            MappingIssue("error", f"scale 不是数字：{scale!r}", binding_where)
                        )
                        scale = None

                expression = str(
                    raw_binding.get("expression")
                    or (
                        f"{column_name} * {scale:g}" if scale is not None else column_name
                    )
                ).strip()

                enum_values = raw_binding.get("enum_values") or ()
                if isinstance(enum_values, str):
                    enum_values = (enum_values,)
                if not isinstance(enum_values, (list, tuple)):
                    issues.append(
                        MappingIssue("error", "enum_values 应为列表", binding_where)
                    )
                    enum_values = ()

                bindings.append(
                    StandardFieldBinding(
                        standard_field=standard,
                        expression=expression,
                        column=column_name,
                        scale=scale,
                        unit=str(raw_binding.get("unit") or ""),
                        enum_values=tuple(str(item) for item in enum_values),
                        role=str(raw_binding.get("role") or ""),
                        notes=str(raw_binding.get("notes") or ""),
                    )
                )

            columns.append(ColumnSpec(column=column_name, bindings=bindings))

        role = str(raw_table.get("role") or "fact").strip() or "fact"
        if role not in _VALID_ROLES:
            issues.append(
                MappingIssue(
                    "error", f"role 取值非法：{role!r}（允许 {sorted(_VALID_ROLES)}）", where
                )
            )

        tables.append(
            TableMappingSpec(
                name=name,
                role=role,
                description=str(raw_table.get("description") or ""),
                grain=str(raw_table.get("grain") or ""),
                primary_key=str(raw_table.get("primary_key") or ""),
                time_column=str(raw_table.get("time_column") or ""),
                columns=columns,
                notes=str(raw_table.get("notes") or ""),
            )
        )

    raw_relationships = payload.get("relationships") or []
    if not isinstance(raw_relationships, list):
        raise MappingError("relationships 应为列表")
    relationships: list[dict[str, Any]] = []
    for index, raw_relation in enumerate(raw_relationships):
        where = f"relationships[{index}]"
        if not isinstance(raw_relation, dict):
            raise MappingError(f"{where} 应为映射（dict）")
        _check_keys(raw_relation, _RELATION_KEYS, where, issues)
        relationships.append(dict(raw_relation))

    # 结构层面的问题（未知键、缺 name、scale 非数字……）在此直接失败：
    # 继续往下走只会让错误延后到模型生成的 SQL 里，更难定位。
    errors = [issue for issue in issues if issue.is_error]
    if errors:
        raise MappingError(
            "映射结构错误：\n"
            + "\n".join(f"  - {issue.render()}" for issue in errors)
        )

    return MappingProfile(
        schema_version=str(payload.get("schema_version") or SCHEMA_VERSION),
        profile=str(payload.get("profile") or ""),
        description=str(payload.get("description") or ""),
        database=dict(payload.get("database") or {}),
        discovery=dict(discovery),
        relationships=relationships,
        tables=tables,
    )


# ----------------------------------------------------------------------
# 校验
# ----------------------------------------------------------------------
def validate_profile(
    profile: MappingProfile,
    *,
    live_columns: Mapping[str, Sequence[str]] | None = None,
    standard_field_names: Sequence[str] | None = None,
) -> list[MappingIssue]:
    """校验映射的业务规则。

    参数:
        profile: 待校验的映射
        live_columns: ``表名 -> 真实列名列表``。给了就顺带校验「映射声明的列
            在库里存在」，这是接入时最容易犯的错（列名拼错、表换版本）。
        standard_field_names: 合法的标准字段名，默认取 :mod:`metadata.standard_fields`。
    """
    issues: list[MappingIssue] = []
    valid_fields = set(standard_field_names or canonical_names())
    seen_tables: set[str] = set()

    for table in profile.tables:
        where = f"tables[{table.name}]"
        if table.name in seen_tables:
            issues.append(MappingIssue("error", "表重复声明", where))
        seen_tables.add(table.name)

        seen_standards: dict[str, str] = {}
        seen_columns: set[str] = set()

        for column in table.columns:
            column_where = f"{where}.{column.column}"
            if column.column in seen_columns:
                issues.append(
                    MappingIssue("warning", "同一列被声明了多次", column_where)
                )
            seen_columns.add(column.column)

            for binding in column.bindings:
                binding_where = f"{column_where} -> {binding.standard_field}"
                if binding.standard_field not in valid_fields:
                    issues.append(
                        MappingIssue(
                            "error",
                            f"未知标准字段（不在 standard_fields 词典里）",
                            binding_where,
                        )
                    )
                    continue

                if binding.standard_field in seen_standards:
                    issues.append(
                        MappingIssue(
                            "error",
                            f"标准字段在本表内重复绑定，另一处是 {seen_standards[binding.standard_field]}",
                            binding_where,
                        )
                    )
                else:
                    seen_standards[binding.standard_field] = column_where

                if not binding.expression:
                    issues.append(MappingIssue("error", "expression 为空", binding_where))

                if binding.scale == 0:
                    issues.append(MappingIssue("error", "scale 不能为 0", binding_where))

        if table.primary_key and live_columns is not None:
            columns = live_columns.get(table.name)
            if columns is not None and table.primary_key not in set(columns):
                issues.append(
                    MappingIssue(
                        "error",
                        f"primary_key 声明的列在库里不存在：{table.primary_key}",
                        where,
                    )
                )

    if live_columns is not None:
        for table in profile.tables:
            columns = live_columns.get(table.name)
            if columns is None:
                issues.append(
                    MappingIssue(
                        "error",
                        f"映射声明的表在库里不存在：{table.name}",
                        f"tables[{table.name}]",
                    )
                )
                continue
            known = set(columns)
            for column in table.columns:
                if column.column not in known:
                    issues.append(
                        MappingIssue(
                            "error",
                            f"映射声明的列在库里不存在：{column.column}",
                            f"tables[{table.name}]",
                        )
                    )

    # 排除规则与映射表冲突时给出提示（例：映射了 mes_prod_log 又把它排除了）
    excluded = {str(item) for item in profile.discovery.get("exclude") or []}
    for table in profile.tables:
        if table.name in excluded:
            issues.append(
                MappingIssue(
                    "error",
                    "该表同时出现在 discovery.exclude 与 tables 中，发现模式会把映射目标排除掉",
                    f"tables[{table.name}]",
                )
            )

    return issues


def mapping_coverage(
    profile: MappingProfile, live_columns: Mapping[str, Sequence[str]]
) -> dict[str, Any]:
    """统计映射覆盖率，供审核 CLI 展示「还有多少列没有标准口径」。"""
    report: dict[str, Any] = {"tables": {}}
    for table in profile.tables:
        columns = list(live_columns.get(table.name) or [])
        mapped_columns = {column.column for column in table.columns if column.bindings}
        covered = [name for name in columns if name in mapped_columns]
        report["tables"][table.name] = {
            "columns": len(columns),
            "mapped_columns": len(covered),
            "unmapped_columns": [name for name in columns if name not in mapped_columns],
            "standard_fields": len({b.standard_field for b in table.bindings()}),
        }
    return report
