"""字段映射的接入与审核命令行：``python -m metadata.mapping.cli <子命令>``。

为什么需要这个 CLI
------------------
映射层（``metadata/mapping``）解决的问题是「换一个不认识的客户库」。「不认识」意味着
**所有判断都可能错**：列名是缩写、没有注释、单位可能是比例也可能是百分数。因此接入
流程不能只有「生成」，还必须有人**签字确认**的环节。这个 CLI 把流程拆成五步：

- ``discover``：先看库里有什么（哪些表可见、哪些被排除、为什么）。表范围错了，
  后面全错 —— 这是最便宜也最该先做的一步。
- ``draft``：自动草拟一份 ``mapping.yaml``（规则 + 可选大模型），产物带
  ``# DRAFT`` 标记，明确「未审核」。
- ``validate``：结构 + 业务规则校验，可选 ``--against-db`` 检查声明的表/列真实存在。
- ``review``：**把有风险的部分挑出来给人看** —— 这是本 CLI 的核心。
- ``diff``：改了一版映射后，看清到底动了哪些绑定。

``review`` 为什么最重要
-----------------------
一份映射里最危险的从来不是「明显写错」，而是**看起来对但静默算错**：

1. **单位换算**：客户列存比例 ``0.039``、标准口径是百分数 ``3.9``，映射里漏了
   ``scale: 100``，那么「平均缺陷率」差 100 倍，而结果集**任何数值都合法**，
   人一眼看不出错。所以 review 把所有含 ``scale``/``expression`` 的列**逐条**列出来，
   并且给出「标准区间」做旁证（缺陷率不可能超过 1 却报了 0.039 时一眼可疑）。
2. **未映射列**：客户表里有 12 列没有标准口径是正常的，但如果 **Agent 常问的字段**
   恰好没被认领，模型会自己编一个列名出来，或引用不存在的字段。
3. **歧义匹配**：``util`` 同时像「设备利用率」和「资源利用率」，规则不猜；review
   必须把它顶到人面前。

用法::

    cd agent
    python -m metadata.mapping.cli discover --json
    python -m metadata.mapping.cli discover --mapping mappings/mes_prod_log.mapping.yaml
    python -m metadata.mapping.cli draft --table t --out data/draft.yaml --no-llm
    python -m metadata.mapping.cli validate --mapping mappings/mes_prod_log.mapping.yaml
    python -m metadata.mapping.cli review --mapping mappings/mes_prod_log.mapping.yaml
    python -m metadata.mapping.cli diff --old mappings/a.yaml --new mappings/b.yaml

（``validate`` / ``review`` 可加 ``--against-db`` 对照真实表/列；``review`` 可加
``--json`` 供流水线消费；``draft`` 可加 ``--no-llm`` 走纯规则路径。详见 ``--help``。）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from metadata.inventory import ROLE_LABELS, TableInventory, discover_tables
from metadata.mapping.draft import draft_mapping, write_draft
from metadata.mapping.schema import (
    MappingError,
    MappingIssue,
    MappingProfile,
    StandardFieldBinding,
    load_profile,
    mapping_coverage,
    validate_profile,
)
from metadata.standard_fields import (
    AMBIGUOUS,
    UNMAPPED,
    StandardField,
    canonical_names,
    field_by_name,
    match_columns,
)
from tools.database import get_engine

__all__ = ["build_parser", "main"]

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


# ----------------------------------------------------------------------
# 公共工具
# ----------------------------------------------------------------------
def _configure_stdout() -> None:
    """Windows 控制台可能是 GBK：遇到无法编码的字符时替换而不是直接崩溃。

    本 CLI 的输出全是中文 + 客户列名/样例值，客户库里的中文列名与生僻符号都会
    走到这里。缺了这一步，接入工程师看到的是 ``UnicodeEncodeError`` 而不是报告。
    """
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover - 非常规 stdout
        pass


def _print_banner(title: str) -> None:
    print("=" * 72)
    print(title)
    print("=" * 72)


def _load_mapping(path: str | Path) -> MappingProfile:
    """加载映射文件；失败时抛 :class:`MappingError`（由各命令转成退出码 1）。"""
    return load_profile(path)


def _profile_arg(args: argparse.Namespace) -> MappingProfile | None:
    raw = getattr(args, "mapping", "") or ""
    if not str(raw).strip():
        return None
    return _load_mapping(raw)


def _live_columns(inventory: TableInventory) -> dict[str, list[str]]:
    """``表名 -> 列名``，供 ``validate_profile(live_columns=...)`` 使用。

    注意：映射声明的表**被 discovery 排除了**时也要给出它的列名 —— 否则
    ``validate_profile`` 会谎报「表在库里不存在」，而真实原因是排除规则，
    两者的修法完全不同（一个改列名，一个改 discovery.exclude）。
    """
    return {table: list(columns) for table, columns in inventory.columns.items()}


def _against_db_issues(
    profile: MappingProfile, inventory: TableInventory
) -> list[MappingIssue]:
    """``--against-db`` 的额外检查：映射声明的表/列是否真的能被 Agent 用上。

    比 ``validate_profile(live_columns=...)`` 多查一件事：表在库里存在、但被
    ``discovery`` 排除掉 —— 这时映射写得再对也白搭，Agent 根本看不到这张表。
    这类问题（映射了 ``mes_prod_log`` 而 ``discovery.exclude`` 里恰好有它）
    在 schema 层已经有一条检查，这里补上「被前缀规则挡下」的情形。
    """
    issues: list[MappingIssue] = []
    for table in profile.tables:
        if table.name in inventory.excluded:
            issues.append(
                MappingIssue(
                    "error",
                    "映射声明的表被发现规则排除了，Agent 看不到它："
                    + inventory.excluded[table.name],
                    f"tables[{table.name}]",
                )
            )
    return issues


def _render_issues(issues: Sequence[MappingIssue]) -> list[str]:
    ordered = sorted(issues, key=lambda issue: _SEVERITY_ORDER.get(issue.level, 9))
    return [issue.render() for issue in ordered]


def _ratio(part: int, total: int) -> str:
    if total <= 0:
        return "—"
    return f"{part / total * 100:.0f}%"


def _column_label(item: Any) -> str:
    """``ColumnMatch`` -> ``表.列``（展示用）。"""
    return f"{item.table}.{item.column}"


# ----------------------------------------------------------------------
# discover
# ----------------------------------------------------------------------
def _cmd_discover(args: argparse.Namespace) -> int:
    engine = get_engine()
    profile = _profile_arg(args)
    inventory = discover_tables(engine, profile=profile)

    if args.json:
        payload = {
            "database_type": engine.dialect.name,
            "summary": inventory.summary(),
            "all_tables": inventory.all_tables,
            "business_tables": inventory.business_tables,
            "excluded": inventory.excluded,
            "roles": inventory.roles,
            "tables": [
                {
                    "name": name,
                    "visible": name in inventory.business_tables,
                    "role": inventory.roles.get(name, ""),
                    "role_label": ROLE_LABELS.get(inventory.roles.get(name, ""), ""),
                    "columns": len(inventory.columns.get(name) or []),
                    "primary_key": inventory.primary_keys.get(name) or [],
                    "exclude_reason": inventory.excluded.get(name, ""),
                }
                for name in inventory.all_tables
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    _print_banner("发现模式：数据库表清单")
    print(f"数据库类型：{engine.dialect.name}")
    print(f"映射文件  ：{args.mapping or '（未指定，仅套用通用排除规则）'}")
    if profile is not None:
        print(f"profile   ：{profile.profile or '（未命名）'}")
    print(f"汇总      ：{inventory.summary()}")
    print("-" * 72)

    if not inventory.all_tables:
        print("库里没有任何表。")
        return 0

    for name in inventory.all_tables:
        columns = inventory.columns.get(name) or []
        primary_keys = inventory.primary_keys.get(name) or []
        visible = name in inventory.business_tables
        mark = "可见  " if visible else "已排除"
        role = inventory.roles.get(name, "")
        role_text = f"{role}（{ROLE_LABELS.get(role, '')}）" if role else "—"
        print(
            f"  [{mark}] {name}  列数 {len(columns)}  角色 {role_text}  "
            f"主键 {('、'.join(primary_keys)) or '—'}"
        )
        if not visible:
            print(f"           排除原因：{inventory.excluded.get(name, '（未知）')}")
            continue
        if args.columns:
            print(f"           列：{'、'.join(columns)}")

    print("-" * 72)
    print(
        f"可见业务表 {len(inventory.business_tables)} 张："
        + ("、".join(inventory.business_tables) or "无")
    )
    print(
        "提示：客户特有的排除项应写在 mapping.yaml 的 discovery.exclude，"
        "不要写进 core.config（那里只放跨客户通用的兜底规则）。"
    )
    return 0


# ----------------------------------------------------------------------
# draft
# ----------------------------------------------------------------------
def _cmd_draft(args: argparse.Namespace) -> int:
    engine = get_engine()
    tables = list(args.table or [])
    if not tables:
        print("请至少用 --table 指定一张表。")
        return 1

    try:
        result = draft_mapping(
            engine,
            tables,
            profile_name=args.profile or tables[0],
            use_llm=not args.no_llm,
            sample_rows=args.sample_rows,
        )
    except MappingError as exc:
        print(f"草拟失败：{exc}")
        return 1

    _print_banner("自动草拟字段映射")
    print(f"目标表    ：{'、'.join(tables)}")
    print(f"profile   ：{result.profile.profile}")
    print(f"规则命中  ：{len(result.matched)} 列")
    print(f"歧义待定  ：{len(result.ambiguous)} 列")
    print(f"未映射    ：{len(result.unmapped)} 列")
    print(f"大模型    ：{'已使用' if result.llm_used else '未使用'}")
    if result.llm_error:
        print(f"模型降级  ：{result.llm_error}")
    if result.ambiguous:
        print("-" * 72)
        print("以下列有多个候选标准字段，草稿里保持未绑定，请人工判定：")
        for item in result.ambiguous:
            print(f"  {item.table}.{item.column} -> 候选：{'、'.join(item.candidates)}")
    if result.unmapped:
        print("-" * 72)
        print(f"没有标准字段认领的列（{len(result.unmapped)} 列，属正常情况）：")
        print("  " + "、".join(_column_label(item) for item in result.unmapped))
    if result.issues:
        print("-" * 72)
        print("校验结论：")
        for line in _render_issues(result.issues):
            print(f"  {line}")

    out_path = write_draft(result.profile, args.out)
    print("-" * 72)
    print(f"草稿已写出：{out_path}")
    print("提醒：草稿未经人审，请先执行 review 逐条确认再改名启用。")
    return 1 if result.has_errors else 0


# ----------------------------------------------------------------------
# validate
# ----------------------------------------------------------------------
def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        profile = _load_mapping(args.mapping)
    except MappingError as exc:
        print(f"加载失败：{exc}")
        return 1

    live: dict[str, list[str]] | None = None
    inventory: TableInventory | None = None
    if args.against_db:
        engine = get_engine()
        inventory = discover_tables(engine, profile=profile)
        live = _live_columns(inventory)

    issues = validate_profile(profile, live_columns=live)
    if inventory is not None:
        issues.extend(_against_db_issues(profile, inventory))

    bound_fields = {
        binding.standard_field
        for table in profile.tables
        for binding in table.bindings()
    }
    _print_banner("字段映射校验")
    print(f"映射文件  ：{args.mapping}")
    print(f"profile   ：{profile.profile or '（未命名）'}")
    print(f"表        ：{len(profile.tables)} 张")
    print(f"标准字段  ：{len(bound_fields)} 个")
    print(f"对照库    ：{'是' if args.against_db else '否'}")
    print("-" * 72)

    if not issues:
        print("校验通过：结构、标准字段、表/列存在性均无问题。")
        return 0

    for line in _render_issues(issues):
        print(f"  {line}")
    errors = sum(1 for issue in issues if issue.is_error)
    print("-" * 72)
    print(f"共 {len(issues)} 条结论，其中 error {errors} 条。")
    return 1 if errors else 0


# ----------------------------------------------------------------------
# review
# ----------------------------------------------------------------------
def _conversion_items(
    profile: MappingProfile,
) -> list[tuple[str, str, StandardFieldBinding, StandardField | None]]:
    """所有「口径 ≠ 裸列名」的绑定：这些地方一旦写错就会静默算错数值。"""
    items: list[tuple[str, str, StandardFieldBinding, StandardField | None]] = []
    for table in profile.tables:
        for column in table.columns:
            for binding in column.bindings:
                if binding.scale is None and binding.expression == column.column:
                    continue
                items.append(
                    (
                        table.name,
                        column.column,
                        binding,
                        field_by_name(binding.standard_field),
                    )
                )
    return items


def _review_payload(
    profile: MappingProfile,
    *,
    mapping_path: str,
    coverage: Mapping[str, Any] | None,
    issues: Sequence[MappingIssue],
    inventory: TableInventory | None,
) -> dict[str, Any]:
    tables: list[dict[str, Any]] = []
    for table in profile.tables:
        stats = (coverage or {}).get("tables", {}).get(table.name, {})
        bindings = list(table.bindings())
        mapped_fields = {binding.standard_field for binding in bindings}
        verdicts = (
            match_columns(
                list(inventory.columns.get(table.name) or []), table=table.name
            )
            if inventory is not None
            else []
        )
        conversions = [
            {
                "column": column,
                "standard_field": binding.standard_field,
                "label": standard.label if standard else "",
                "standard_unit": standard.unit if standard else "",
                "customer_unit": binding.unit,
                "scale": binding.scale,
                "expression": binding.expression,
                "notes": binding.notes,
            }
            for _table, column, binding, standard in _conversion_items(profile)
            if _table == table.name
        ]
        tables.append(
            {
                "name": table.name,
                "role": table.role,
                "grain": table.grain,
                "primary_key": table.primary_key,
                "columns": stats.get("columns", len(table.columns)),
                "mapped_columns": stats.get("mapped_columns", 0),
                "unmapped_columns": stats.get("unmapped_columns", []),
                "standard_fields_mapped": len(mapped_fields),
                "standard_fields_missing": [
                    name for name in canonical_names() if name not in mapped_fields
                ],
                "conversions": conversions,
                "ambiguous_columns": [
                    {"column": item.column, "candidates": list(item.candidates)}
                    for item in verdicts
                    if item.status == AMBIGUOUS
                ],
                "unmapped_rule_columns": [
                    item.column for item in verdicts if item.status == UNMAPPED
                ],
            }
        )
    return {
        "mapping": mapping_path,
        "profile": profile.profile,
        "database": dict(profile.database),
        "tables": tables,
        "issues": [
            {"level": issue.level, "where": issue.where, "message": issue.message}
            for issue in issues
        ],
        "summary": {
            "tables": len(profile.tables),
            "conversions": len(_conversion_items(profile)),
            "errors": sum(1 for issue in issues if issue.is_error),
            "warnings": sum(1 for issue in issues if issue.level == "warning"),
        },
    }


def _cmd_review(args: argparse.Namespace) -> int:
    try:
        profile = _load_mapping(args.mapping)
    except MappingError as exc:
        print(f"加载失败：{exc}")
        return 1

    live: dict[str, list[str]] | None = None
    inventory: TableInventory | None = None
    if args.against_db:
        engine = get_engine()
        inventory = discover_tables(engine, profile=profile)
        live = _live_columns(inventory)

    coverage = mapping_coverage(profile, live) if live else None
    issues = validate_profile(profile, live_columns=live)
    if inventory is not None:
        issues.extend(_against_db_issues(profile, inventory))

    if args.json:
        payload = _review_payload(
            profile,
            mapping_path=str(args.mapping),
            coverage=coverage,
            issues=issues,
            inventory=inventory,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if payload["summary"]["errors"] else 0

    _print_banner("字段映射人审报告")
    print(f"映射文件  ：{args.mapping}")
    print(f"profile   ：{profile.profile or '（未命名）'}")
    print(f"说明      ：{profile.description or '—'}")
    if profile.database:
        print(f"数据库    ：{profile.database}")
    print(f"对照库    ：{'是' if args.against_db else '否（加 --against-db 可校验表/列真实存在）'}")
    print()

    # ---- 一、逐表覆盖率 ----
    print("一、逐表覆盖率（每列的映射情况）")
    print("-" * 72)
    for table in profile.tables:
        stats = (coverage or {}).get("tables", {}).get(table.name, {})
        bindings = list(table.bindings())
        mapped_fields = {binding.standard_field for binding in bindings}
        total_columns = stats.get("columns", len(table.columns))
        mapped_columns = stats.get("mapped_columns", 0)
        unmapped = list(stats.get("unmapped_columns") or [])
        ratio = _ratio(mapped_columns, total_columns)
        print(
            f"[{table.name}] 角色 {table.role}（{ROLE_LABELS.get(table.role, '')}）  "
            f"列 {mapped_columns}/{total_columns} 已映射（{ratio}）  "
            f"标准字段 {len(mapped_fields)} 个"
        )
        if table.grain:
            print(f"    grain：{table.grain}")
        print(f"    主键：{table.primary_key or '—'}    时间列：{table.time_column or '—'}")
        if unmapped:
            print(f"    未映射列（{len(unmapped)} 个，无标准口径，Agent 无法据其回答指标问题）：")
            for name in unmapped:
                print(f"      - {name}")
        else:
            print("    未映射列：无")
        print()

    # ---- 二、客户表里没有的标准字段 ----
    print("二、本数据源缺失的标准字段（涉及这些字段的问题应回答「数据不足」）")
    print("-" * 72)
    for table in profile.tables:
        mapped_fields = {binding.standard_field for binding in table.bindings()}
        missing = [name for name in canonical_names() if name not in mapped_fields]
        print(f"[{table.name}] 缺 {len(missing)}/{len(canonical_names())} 个标准字段")
        if not missing:
            print("    无缺失。")
        else:
            for name in missing:
                standard = field_by_name(name)
                label = standard.label if standard else ""
                print(f"    - {name}（{label}）")
        print()

    # ---- 三、单位换算（高风险，必须逐条确认）----
    print("三、单位换算逐条确认（口径 ≠ 裸列名，写错会静默差一个倍数）")
    print("-" * 72)
    conversions = _conversion_items(profile)
    if not conversions:
        print("无单位换算：所有绑定表达式都等于客户列名本身。")
    for table_name, column, binding, standard in conversions:
        standard_unit = standard.unit if standard else "（未知标准字段）"
        print(f"[{table_name}] {column} -> {binding.standard_field}")
        print(f"    客户列单位：{binding.unit or '（未声明）'}    标准口径单位：{standard_unit}")
        print(f"    换算系数  ：{binding.scale if binding.scale is not None else '—'}")
        print(f"    SQL 写法  ：{binding.expression}")
        if binding.notes:
            print(f"    备注      ：{binding.notes}")
        if binding.scale is None:
            print("    !! expression 不是裸列名但未声明 scale，请确认换算是否正确")
        print("    → 请核对该列真实取值：若取样值明显小于标准口径的数量级，就是漏了换算。")
    print()

    # ---- 四、规则匹配的歧义/未映射 ----
    if inventory is not None:
        print("四、列名规则的匹配账本（规则看不到取值，仅供交叉验证）")
        print("-" * 72)
        for table in profile.tables:
            columns = list(inventory.columns.get(table.name) or [])
            verdicts = match_columns(columns, table=table.name)
            ambiguous = [item for item in verdicts if item.status == AMBIGUOUS]
            unmapped_rule = [item for item in verdicts if item.status == UNMAPPED]
            mapped = {binding.standard_field for binding in table.bindings()}
            matched_count = len(verdicts) - len(ambiguous) - len(unmapped_rule)
            print(
                f"[{table.name}] 规则命中 {matched_count} 列，"
                f"歧义 {len(ambiguous)} 列，未命中 {len(unmapped_rule)} 列"
            )
            for item in ambiguous:
                chosen = [name for name in item.candidates if name in mapped]
                print(
                    f"    ? {item.column}：候选 {'、'.join(item.candidates)}，"
                    f"映射里选了 {'、'.join(chosen) or '（未绑定，仍是空缺）'}"
                )
            for item in unmapped_rule:
                bound = any(
                    column.column == item.column and column.bindings
                    for column in table.columns
                )
                if bound:
                    print(
                        f"    * {item.column}：规则未命中，但映射已人工绑定 —— 请确认这是刻意的"
                    )
            print()

    # ---- 五、校验结论 ----
    print("五、校验结论（按严重程度排序）")
    print("-" * 72)
    if not issues:
        print("无任何问题。")
    else:
        for line in _render_issues(issues):
            print(f"  {line}")
    errors = sum(1 for issue in issues if issue.is_error)
    warnings = sum(1 for issue in issues if issue.level == "warning")

    print()
    print("六、签字清单")
    print("-" * 72)
    print("  [ ] 一、未映射列里没有 Agent 高频询问的字段（有的话请补映射或明确告知数据不足）")
    print(f"  [ ] 三、{len(conversions)} 条单位换算已逐条对照真实取值确认")
    print("  [ ] 四、歧义列的取舍已由业务确认（不是按列名猜的）")
    print("  [ ] discovery.exclude 只挡系统表与等价表，没有误挡业务表")
    print("  [ ] 确认后删除文件头的 DRAFT 标记，并把 profile 名写进配置项 analysis_mapping")
    print("-" * 72)
    print(
        f"汇总：表 {len(profile.tables)} 张，单位换算 {len(conversions)} 条，"
        f"error {errors} 条，warning {warnings} 条。"
    )
    return 1 if errors else 0


# ----------------------------------------------------------------------
# diff
# ----------------------------------------------------------------------
def _binding_index(
    profile: MappingProfile,
) -> dict[tuple[str, str], StandardFieldBinding]:
    index: dict[tuple[str, str], StandardFieldBinding] = {}
    for table in profile.tables:
        for column in table.columns:
            for binding in column.bindings:
                index[(table.name, column.column)] = binding
    return index


def _describe(binding: StandardFieldBinding | None) -> str:
    if binding is None:
        return "—"
    if binding.scale is not None:
        return (
            f"{binding.standard_field} "
            f"[scale={binding.scale:g}, expr={binding.expression}]"
        )
    return f"{binding.standard_field} [expr={binding.expression}]"


def _binding_diff(
    old: StandardFieldBinding | None, new: StandardFieldBinding | None
) -> list[str]:
    changes: list[str] = []
    old_field = old.standard_field if old else None
    new_field = new.standard_field if new else None
    if old_field != new_field:
        changes.append(f"标准字段：{old_field or '—'} -> {new_field or '—'}")
    old_expression = old.expression if old else None
    new_expression = new.expression if new else None
    if old_expression != new_expression:
        changes.append(f"expression：{old_expression or '—'} -> {new_expression or '—'}")
    if (old.scale if old else None) != (new.scale if new else None):
        old_scale = old.scale if old else "—"
        new_scale = new.scale if new else "—"
        changes.append(f"scale：{old_scale} -> {new_scale}")
    if (old.unit if old else "") != (new.unit if new else ""):
        old_unit = old.unit if old else "—"
        new_unit = new.unit if new else "—"
        changes.append(f"unit：{old_unit} -> {new_unit}")
    if (old.enum_values if old else ()) != (new.enum_values if new else ()):
        changes.append(
            f"enum_values：{list(old.enum_values) if old else '—'} -> "
            f"{list(new.enum_values) if new else '—'}"
        )
    return changes


def _cmd_diff(args: argparse.Namespace) -> int:
    try:
        old = _load_mapping(args.old)
        new = _load_mapping(args.new)
    except MappingError as exc:
        print(f"加载失败：{exc}")
        return 1

    old_index = _binding_index(old)
    new_index = _binding_index(new)
    old_keys = set(old_index)
    new_keys = set(new_index)

    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    changed = sorted(
        key
        for key in (old_keys & new_keys)
        if _binding_diff(old_index[key], new_index[key])
    )

    _print_banner("字段映射差异")
    print(f"旧：{args.old}（profile={old.profile or '—'}）")
    print(f"新：{args.new}（profile={new.profile or '—'}）")
    print("-" * 72)

    old_tables = set(old.table_names)
    new_tables = set(new.table_names)
    if old_tables != new_tables:
        print("表级差异：")
        for name in sorted(new_tables - old_tables):
            print(f"  + 新增表 {name}")
        for name in sorted(old_tables - new_tables):
            print(f"  - 删除表 {name}")
        print()

    if not (added or removed or changed):
        print("两份映射的列绑定完全一致。")
        return 0

    if added:
        print(f"新增绑定（{len(added)} 条）：")
        for table, column in added:
            print(f"  + {table}.{column}：{_describe(new_index[(table, column)])}")
        print()
    if removed:
        print(f"删除绑定（{len(removed)} 条）—— 确认没有把 Agent 需要的字段删掉：")
        for table, column in removed:
            print(f"  - {table}.{column}：{_describe(old_index[(table, column)])}")
        print()
    if changed:
        print(f"变更绑定（{len(changed)} 条）—— 逐条确认，尤其是 scale/expression：")
        for table, column in changed:
            print(f"  * {table}.{column}")
            old_binding = old_index[(table, column)]
            new_binding = new_index[(table, column)]
            for line in _binding_diff(old_binding, new_binding):
                print(f"      {line}")
        print()

    print("-" * 72)
    print(f"汇总：新增 {len(added)}，删除 {len(removed)}，变更 {len(changed)}。")
    return 0


# ----------------------------------------------------------------------
# 解析器
# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m metadata.mapping.cli",
        description="字段映射的发现、草拟、校验、审核与比对",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser(
        "discover", help="扫描数据库并展示表清单（含被排除的表与原因）"
    )
    discover.add_argument(
        "--mapping", default="", help="mapping.yaml 路径（可选，用其 discovery 规则）"
    )
    discover.add_argument("--json", action="store_true", help="输出机器可读的 JSON")
    discover.add_argument(
        "--columns", action="store_true", help="同时列出每张可见表的列名"
    )
    discover.set_defaults(func=_cmd_discover)

    draft = subparsers.add_parser(
        "draft", help="自动草拟一份 mapping.yaml（规则 + 可选大模型）"
    )
    draft.add_argument(
        "--table", action="append", required=True, help="目标表名，可重复"
    )
    draft.add_argument("--out", required=True, help="草稿输出路径")
    draft.add_argument("--profile", default="", help="profile 名，默认取第一张表名")
    draft.add_argument(
        "--no-llm", action="store_true", help="只用规则匹配，完全不调用大模型"
    )
    draft.add_argument(
        "--sample-rows", type=int, default=5, help="每列取几个样例值（默认 5）"
    )
    draft.set_defaults(func=_cmd_draft)

    validate = subparsers.add_parser(
        "validate", help="校验映射文件（结构与业务规则）"
    )
    validate.add_argument("--mapping", required=True, help="mapping.yaml 路径")
    validate.add_argument(
        "--against-db", action="store_true", help="额外校验声明的表/列在库里真实存在"
    )
    validate.set_defaults(func=_cmd_validate)

    review = subparsers.add_parser(
        "review", help="人审辅助：覆盖率、缺失字段、单位换算、校验问题"
    )
    review.add_argument("--mapping", required=True, help="mapping.yaml 路径")
    review.add_argument(
        "--against-db", action="store_true", help="同时对照数据库真实表/列与规则匹配"
    )
    review.add_argument("--json", action="store_true", help="输出机器可读的 JSON")
    review.set_defaults(func=_cmd_review)

    diff = subparsers.add_parser("diff", help="比对两份映射的列绑定差异")
    diff.add_argument("--old", required=True, help="旧映射路径")
    diff.add_argument("--new", required=True, help="新映射路径")
    diff.set_defaults(func=_cmd_diff)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except MappingError as exc:
        # 映射问题一律以「可读的中文原因 + 退出码 1」收场，而不是抛栈
        print(f"映射错误：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
