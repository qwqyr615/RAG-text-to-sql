"""把 ``mapping.yaml`` 编译成运行时用的「已解析字段口径」。

职责
----
``mapping.yaml`` 只说「哪一列是什么」，本模块把它和**数据库真实结构**合起来，
产出一份自洽的运行时视图：

- ``field_map``：每个标准字段的最终口径（客户列、表达式、单位换算、枚举取值），
  直接塞进 metadata JSON 交给 Prompt 渲染；
- ``expression_map`` / ``reverse_map``：喂给 :class:`ColumnRewriter` 改写 RAG 示例
  与参照 SQL；
- ``metric_bindings``：把业务指标口径落到**客户侧表达式**上，供指标段直接展示
  「要算缺陷率就写 AVG(def_rate * 100)」；
- ``table_hints``：grain、主键、时间列、枚举取值 —— 这些是数据库里读不出来、
  但对写对 SQL 极其关键的语义。

这样一来，「客户表怎么接进系统」这件事只有一个落点：改 YAML，不用改代码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from metadata.mapping.columns import ColumnRewriter, find_identifiers
from metadata.mapping.schema import (
    MappingProfile,
    StandardFieldBinding,
    TableMappingSpec,
)
from metadata.standard_fields import (
    STANDARD_FIELDS,
    STANDARD_METRICS,
    StandardField,
    field_by_name,
)

__all__ = ["CompiledMapping", "ResolvedField", "compile_profile"]


@dataclass
class ResolvedField:
    """一个标准字段在客户表上的最终口径。"""

    standard_field: str
    table: str
    column: str
    expression: str
    label: str = ""
    unit: str = ""
    data_kind: str = "measure"
    group: str = "其他"
    customer_unit: str = ""
    scale: float | None = None
    enum_values: tuple[str, ...] = ()
    notes: str = ""

    @property
    def is_plain(self) -> bool:
        return self.expression == self.column

    @property
    def needs_conversion(self) -> bool:
        return not self.is_plain

    def render_line(self) -> str:
        """渲染成 Prompt 里的一行说明。"""
        text = f"- {self.standard_field}（{self.label}）"
        if self.is_plain:
            text += f" = `{self.column}`"
        else:
            text += f" = `{self.column}`，SQL 中请写 `{self.expression}`"
        if self.scale is not None and self.customer_unit:
            text += f"（客户列单位 {self.customer_unit}，标准口径为{self.unit}，已含换算）"
        elif self.unit and self.unit != "—":
            text += f"（单位：{self.unit}）"
        if self.enum_values:
            text += f"；取值：{' / '.join(self.enum_values)}"
        if self.notes:
            text += f"；{self.notes}"
        return text


@dataclass
class CompiledMapping:
    """编译产物：Prompt 段、SQL 改写器与统计信息。"""

    profile: MappingProfile
    tables: list[str] = field(default_factory=list)
    fields: dict[str, list[ResolvedField]] = field(default_factory=dict)
    """``表名 -> 已解析字段``。"""

    table_hints: dict[str, dict[str, Any]] = field(default_factory=dict)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    """业务指标口径，已落到客户侧表达式。"""

    unmatched_metrics: list[str] = field(default_factory=list)
    """客户表里找不到字段的指标名 —— 要显式告诉模型「本数据源没有这个指标」。"""

    def table(self, name: str) -> list[ResolvedField]:
        return self.fields.get(name, [])

    def field(self, table: str, standard_field: str) -> ResolvedField | None:
        for item in self.fields.get(table, []):
            if item.standard_field == standard_field:
                return item
        return None

    def expression_map(self, table: str) -> dict[str, str]:
        return {item.standard_field: item.expression for item in self.table(table)}

    def rewriter(self, table: str) -> ColumnRewriter:
        """构造该表的 SQL 改写器。"""
        return ColumnRewriter(
            self.expression_map(table), reverse=self.profile.reverse_map(table)
        )

    def unmapped_standard_fields(self, table: str) -> list[str]:
        """本表没有映射到的标准字段名（用于判断 SQL 是否引用了未接入字段）。"""
        mapped = {item.standard_field for item in self.table(table)}
        return [name for name in (f.name for f in STANDARD_FIELDS) if name not in mapped]

    def mentions_unmapped(self, table: str, sql: str) -> list[str]:
        """SQL 里出现的、但本表未映射的标准字段名。"""
        return find_identifiers(sql, self.unmapped_standard_fields(table))

    # -- Prompt 渲染 ---------------------------------------------------
    def fields_text(self, table: str, *, only: Sequence[str] | None = None) -> str:
        """按业务分组渲染标准字段口径，供 Prompt 的字段映射段使用。"""
        items = self.table(table)
        if only is not None:
            wanted = set(only)
            items = [item for item in items if item.standard_field in wanted]
        if not items:
            return ""

        by_group: dict[str, list[ResolvedField]] = {}
        for item in items:
            by_group.setdefault(item.group or "其他", []).append(item)

        lines: list[str] = []
        for group, group_items in by_group.items():
            lines.append(f"[{group}]")
            lines.extend(item.render_line() for item in group_items)
        return "\n".join(lines)

    def conversions_text(self, table: str) -> str:
        """只列出**需要单位换算**的字段 —— 这是最容易算错的地方，单独强调。"""
        converted = [item for item in self.table(table) if item.needs_conversion]
        if not converted:
            return ""
        lines = ["以下字段客户库的存储单位与标准口径不同，SQL 中**必须**按给出的写法换算，"
                 "否则结果会整体差一个倍数："]
        for item in converted:
            lines.append(
                f"- {item.standard_field}：客户列 `{item.column}` × {item.scale:g} "
                f"-> 写成 `{item.expression}`（{item.customer_unit} -> {item.unit}）"
            )
        return "\n".join(lines)

    def grain_text(self, table: str) -> str:
        hints = self.table_hints.get(table) or {}
        parts: list[str] = []
        if hints.get("grain"):
            parts.append(str(hints["grain"]))
        if hints.get("primary_key"):
            parts.append(f"主键 {hints['primary_key']}")
        if hints.get("time_column"):
            parts.append(f"时间列 {hints['time_column']}")
        return "；".join(parts)


def _resolve_field(
    standard: StandardField, binding: StandardFieldBinding, table: str
) -> ResolvedField:
    customer_unit = binding.unit or ""
    if not customer_unit and binding.scale is not None:
        customer_unit = _infer_customer_unit(standard.unit, binding.scale)
    return ResolvedField(
        standard_field=standard.name,
        table=table,
        column=binding.column,
        expression=binding.expression,
        label=standard.label,
        unit=standard.unit,
        data_kind=standard.data_kind,
        group=standard.group,
        customer_unit=customer_unit,
        scale=binding.scale,
        enum_values=binding.enum_values,
        notes=binding.notes,
    )


def _infer_customer_unit(standard_unit: str, scale: float) -> str:
    """按换算系数推断客户侧单位，只用于 Prompt 里向模型解释差异。"""
    if standard_unit == "百分比":
        if scale == 100:
            return "比例(0-1)"
        if scale == 0.01:
            return "百分比(0-100)"
    return f"×{scale:g}"


def compile_profile(profile: MappingProfile) -> CompiledMapping:
    """把映射编译成运行时视图。"""
    compiled = CompiledMapping(profile=profile, tables=profile.table_names)

    for table in profile.tables:
        resolved: list[ResolvedField] = []
        for column in table.columns:
            for binding in column.bindings:
                standard = field_by_name(binding.standard_field)
                if standard is None:
                    # 理论上不会走到：schema.validate_profile 已挡住未知标准字段
                    continue
                resolved.append(_resolve_field(standard, binding, table.name))
        compiled.fields[table.name] = resolved
        compiled.table_hints[table.name] = _table_hints(table)

    _compile_metrics(compiled)
    return compiled


def _table_hints(table: TableMappingSpec) -> dict[str, Any]:
    return {
        "role": table.role,
        "description": table.description,
        "grain": table.grain,
        "primary_key": table.primary_key,
        "time_column": table.time_column,
        "notes": table.notes,
    }


def _compile_metrics(compiled: CompiledMapping) -> None:
    """把业务指标口径落到客户侧表达式。

    优先在 ``role == "fact"`` 的表上解析；没有 fact 表时按声明顺序找第一张有该
    字段的表。找不到的指标记进 ``unmatched_metrics``，由 Prompt 显式告知模型
    「本数据源没有这个指标」，避免它按业务别名臆造列名。
    """
    fact_tables = [
        table.name
        for table in compiled.profile.tables
        if (compiled.table_hints.get(table.name) or {}).get("role") == "fact"
    ]
    search_order = fact_tables + [
        name for name in compiled.tables if name not in fact_tables
    ]

    for metric in STANDARD_METRICS:
        matched: tuple[str, ResolvedField] | None = None
        for table_name in search_order:
            item = compiled.field(table_name, metric.standard_field)
            if item is not None:
                matched = (table_name, item)
                break

        if matched is None:
            compiled.unmatched_metrics.append(metric.name)
            continue

        table_name, item = matched
        compiled.metrics.append(
            {
                "name": metric.name,
                "aliases": list(metric.aliases),
                "table": table_name,
                "standard_field": metric.standard_field,
                "column": item.column,
                "expression": item.expression,
                "location": f"{table_name}.{item.column}",
                "description": metric.description,
                "calculation": metric.render_calculation(item.expression),
                "needs_conversion": item.needs_conversion,
            }
        )
