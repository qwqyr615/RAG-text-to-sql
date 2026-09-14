"""四个 Prompt 段 Provider：元数据 / 业务指标 / 业务知识 / RAG 示例。

每个 provider 自己负责相关性排序与预算裁剪，``build()`` 收到的 ``budget`` 是本次
分配到的字符预算。裁剪原则统一为「按相关性丢弃」：预算不够时丢掉的是与当前问题
最不相关的表 / 指标 / 知识条目 / 示例，而不是把文本从中间切断。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Sequence

from core.metrics import (
    BUSINESS_METRICS,
    resolve_metrics,
    resolve_metrics_from_field_map,
)
from metadata.mapping.columns import ColumnRewriter, find_identifiers
from metadata.standard_fields import STANDARD_FIELDS
from prompt.base import PromptContext, PromptSection, PromptSectionProvider
from prompt.budget import pack_blocks, relevance_score, sort_by_relevance, truncate_text

logger = logging.getLogger(__name__)

__all__ = [
    "DataResourceProvider",
    "KnowledgeProvider",
    "MetricsProvider",
    "RagExampleProvider",
]


class DataResourceProvider(PromptSectionProvider):
    """元数据段：表、字段、样例值、表间关系，以及**标准字段口径映射**。

    裁剪策略：

    - 表按与问题的相关性排序装入预算，至少保留 ``min_tables`` 张（相关性全为 0 时
      按元数据原顺序补齐），避免模型完全看不到数据资源；
    - 每张表内的字段同样按相关性排序，只展示前 ``max_columns_per_table`` 个，
      其余折叠并提示模型用 ``sql_db_schema`` 查看完整结构；
    - 样例值只给相关性最高的 ``sample_value_tables`` 张表，因为样例值最吃预算；
    - 用到字段映射时，额外插入一个「标准字段口径」块，把
      「业务词 -> 客户列 -> 必须写的表达式」讲清楚（含单位换算）。
      没有映射时该块不出现，行为与原先完全一致；
    - 尾块体积小、价值高，保证出现（``keep_last``）：有表间关系时输出关系，
      单表模型下改为输出「不要生成 JOIN」的提示。
    """

    name = "metadata"
    title = "数据资源（可用表与字段）"
    priority = 90
    default_max_chars = 3000

    def __init__(
        self,
        max_chars: int | None = None,
        *,
        min_tables: int = 3,
        max_columns_per_table: int = 25,
        sample_value_tables: int = 2,
        field_map_budget: int = 900,
        enabled: bool = True,
    ) -> None:
        super().__init__(max_chars, enabled=enabled)
        self.min_tables = max(0, int(min_tables))
        self.max_columns_per_table = max(1, int(max_columns_per_table))
        self.sample_value_tables = max(0, int(sample_value_tables))
        self.field_map_budget = max(0, int(field_map_budget))

    def build(self, context: PromptContext, budget: int) -> PromptSection:
        tables = list(context.metadata_json.get("tables") or [])
        if not tables:
            return PromptSection(
                name=self.name,
                title=self.title,
                content=(
                    "当前未读取到表结构，请先调用 sql_db_list_tables 查看可用表，"
                    "再用 sql_db_schema 查看字段。"
                ),
            )

        keywords = context.keywords()
        ordered = self._order_tables(tables, keywords)

        blocks = [
            self._format_table(
                table, keywords, with_samples=rank < self.sample_value_tables
            )
            for rank, table in enumerate(ordered)
        ]

        # 标准字段口径块：有映射时才插入
        field_map_block = self._format_field_map(context)
        if field_map_block:
            blocks.append(field_map_block)

        # 尾块保证出现（keep_last）：有表间关系时输出关系；单表模型下改为
        # 「不要 JOIN」的提示，避免模型自己在冗余的维度编码上做自连接。
        tail_block = self._format_relationships(context)
        if not tail_block and len(ordered) == 1:
            tail_block = (
                f"当前数据底座为单表模型：所有分析都在 "
                f"{ordered[0].get('table_name')} 内完成，不要生成 JOIN。"
            )
        if tail_block:
            blocks = blocks + [tail_block]

        content, dropped, truncated = pack_blocks(
            blocks,
            budget,
            drop_note=(
                "…另有 {dropped} 张表因预算未展示，可用 sql_db_list_tables 查看全部表、"
                "用 sql_db_schema 查看完整字段与样例值"
            ),
            keep_last=bool(tail_block),
        )

        return PromptSection(
            name=self.name,
            title=self.title,
            content=content,
            dropped_items=dropped,
            truncated=truncated,
        )

    # ------------------------------------------------------------------
    # 标准字段口径
    # ------------------------------------------------------------------
    def _format_field_map(self, context: PromptContext) -> str:
        """渲染「标准字段 -> 客户列 -> 表达式」块。

        这个块解决的是**跨命名体系**的问题：业务人员说「缺陷率」，客户库里叫
        ``def_rate``，而且单位是比例（0.039）不是百分数（3.9）。只把表结构丢给模型，
        它猜不准；把这个块给模型，它就知道该写 ``AVG(def_rate * 100)``。
        """
        field_map = context.metadata_json.get("field_map") or {}
        if not field_map or self.field_map_budget <= 0:
            return ""

        lines: list[str] = [
            "标准业务字段与当前数据源实际列的对应关系"
            "（业务问题用的是标准字段名，生成 SQL 必须使用实际列名或给出的表达式）："
        ]
        conversions: list[str] = []

        for table_name, items in field_map.items():
            if not items:
                continue
            lines.append(f"[{table_name}]")
            for item in items:
                column = str(item.get("column") or "")
                expression = str(item.get("expression") or column)
                label = str(item.get("label") or "")
                standard = str(item.get("standard_field") or "")

                line = f"- {label}({standard}) -> 列 {column}"
                if item.get("needs_conversion"):
                    line += f"，SQL 必须写成 {expression}"
                    conversions.append(
                        f"- {label}：客户列 {column} × {item.get('scale')} "
                        f"-> 写成 {expression}"
                    )
                unit = str(item.get("unit") or "")
                customer_unit = str(item.get("customer_unit") or "")
                if item.get("needs_conversion") and customer_unit:
                    line += f"（客户单位 {customer_unit}，口径单位 {unit}）"
                elif unit and unit != "—":
                    line += f"（单位 {unit}）"
                enum_values = item.get("enum_values") or []
                if enum_values:
                    line += f"；取值：{' / '.join(str(v) for v in enum_values)}"
                lines.append(line)

        if conversions:
            lines.append("")
            lines.append("需要单位换算的字段（漏掉换算会让结果整体差一个倍数）：")
            lines.extend(conversions)

        content = "\n".join(lines)
        if len(content) > self.field_map_budget:
            kept, _ = truncate_text(content, self.field_map_budget)
            return kept
        return content


    # ------------------------------------------------------------------
    # 排序
    # ------------------------------------------------------------------
    def _order_tables(
        self, tables: Sequence[dict[str, Any]], keywords: set[str]
    ) -> list[dict[str, Any]]:
        """相关性优先，并用 ``min_tables`` 保底。"""
        scored = [
            (relevance_score(self._table_text(table), keywords), position, table)
            for position, table in enumerate(tables)
        ]
        relevant = sorted(
            (item for item in scored if item[0] > 0),
            key=lambda item: (-item[0], item[1]),
        )
        chosen = list(relevant)
        chosen_positions = {item[1] for item in chosen}

        if len(chosen) < self.min_tables:
            for item in scored:
                if item[1] in chosen_positions:
                    continue
                chosen.append(item)
                chosen_positions.add(item[1])
                if len(chosen) >= self.min_tables:
                    break

        # 其余表按元数据原顺序排在后面，预算够就会被带上
        rest = [item for item in scored if item[1] not in chosen_positions]
        return [item[2] for item in chosen + rest]

    @staticmethod
    def _table_text(table: dict[str, Any]) -> str:
        parts = [str(table.get("table_name") or ""), str(table.get("description") or "")]
        for column in table.get("columns") or []:
            parts.append(str(column.get("name") or ""))
            parts.append(str(column.get("description") or ""))
        return " ".join(parts)

    @staticmethod
    def _column_text(column: dict[str, Any]) -> str:
        return " ".join(
            [
                str(column.get("name") or ""),
                str(column.get("description") or ""),
                str(column.get("type") or ""),
            ]
        )

    # ------------------------------------------------------------------
    # 格式化
    # ------------------------------------------------------------------
    def _format_table(
        self, table: dict[str, Any], keywords: set[str], *, with_samples: bool
    ) -> str:
        table_name = str(table.get("table_name") or "")
        description = str(table.get("description") or "").strip()
        header = f"- {table_name}" + (f"：{description}" if description else "")

        columns = list(table.get("columns") or [])
        ordered = sort_by_relevance(columns, self._column_text, keywords)
        visible = ordered[: self.max_columns_per_table]

        lines = [header]
        lines.extend(
            self._format_column(table_name, column, with_samples=with_samples)
            for column in visible
        )

        hidden = len(ordered) - len(visible)
        if hidden > 0:
            lines.append(
                f"  - …另有 {hidden} 个字段未展示，可用 sql_db_schema 查看完整字段"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_column(
        table_name: str, column: dict[str, Any], *, with_samples: bool
    ) -> str:
        column_name = str(column.get("name") or "")
        text = f"  - {table_name}.{column_name}"

        column_type = str(column.get("type") or "").strip()
        if column_type:
            text += f" {column_type}"
        if column.get("primary_key"):
            text += " [主键]"

        description = str(column.get("description") or "").strip()
        if description:
            text += f"：{description}"

        sample = column.get("sample_value")
        if with_samples and sample is not None and str(sample) != "":
            text += f"（样例: {sample}）"
        return text

    @staticmethod
    def _format_relationships(context: PromptContext) -> str:
        relationships = list(context.metadata_json.get("relationships") or [])
        if not relationships:
            return ""

        lines = ["表间关系："]
        for relation in relationships:
            relation_type = str(relation.get("relation_type") or "").strip()
            line = (
                f"- {relation.get('source_table')}.{relation.get('source_column')}"
                f" -> {relation.get('target_table')}.{relation.get('target_column')}"
            )
            if relation_type:
                line += f"（{relation_type}）"
            lines.append(line)
        return "\n".join(lines)


class MetricsProvider(PromptSectionProvider):
    """指标段：业务指标口径到真实字段的映射。

    两条数据来源，优先用前者：

    1. ``metadata_json["field_map"]`` —— 由 ``mapping.yaml`` 编译而来，**人审过**，
       还带单位换算表达式，于是能直接告诉模型「缺陷率要写 ``AVG(def_rate * 100)``」；
    2. 没有映射时退回 ``core.metrics.resolve_metrics`` 的启发式匹配（按标准候选字段名
       撞实际列名），也就是本项目原先的行为。

    匹配不到的指标单独提示「当前数据源无该字段」，防止模型按业务别名臆造列名。
    条目按与问题的相关性排序。
    """

    name = "metrics"
    title = "业务指标口径"
    priority = 80
    default_max_chars = 1500

    def build(self, context: PromptContext, budget: int) -> PromptSection:
        field_to_table = self._field_to_table(context)
        mapped = self._mapped_metrics(context)

        if mapped is not None:
            metrics = mapped
            if not metrics:
                return PromptSection(
                    name=self.name,
                    title=self.title,
                    content=(
                        "当前数据源未匹配到预置业务指标字段，"
                        "请严格按真实表结构分析，不要按业务别名猜测列名。"
                    ),
                )
        elif context.available_columns:
            metrics = resolve_metrics(context.available_columns)
            if not metrics:
                return PromptSection(
                    name=self.name,
                    title=self.title,
                    content=(
                        "当前数据源未匹配到预置业务指标字段，"
                        "请严格按真实表结构分析，不要按业务别名猜测列名。"
                    ),
                )
        else:
            metrics = list(BUSINESS_METRICS)

        keywords = context.keywords()
        blocks = [
            self._format_metric(metric, field_to_table) for metric in metrics
        ]
        ordered = sort_by_relevance(blocks, lambda block: block, keywords)

        content, dropped, truncated = pack_blocks(
            ordered,
            budget,
            drop_note="…另有 {dropped} 个指标口径因预算未展示",
        )
        return PromptSection(
            name=self.name,
            title=self.title,
            content=content,
            dropped_items=dropped,
            truncated=truncated,
        )

    @staticmethod
    def _mapped_metrics(context: PromptContext) -> list[dict[str, Any]] | None:
        """从字段映射解析指标；没有映射信息时返回 ``None`` 表示「请走旧路径」。"""
        field_map = context.metadata_json.get("field_map")
        if not field_map:
            return None
        # 映射里显式算好的指标优先（含最终表达式）
        precomputed = context.metadata_json.get("metric_bindings")
        if precomputed:
            return list(precomputed)
        flattened: list[dict[str, Any]] = []
        for items in field_map.values():
            flattened.extend(items or [])
        return resolve_metrics_from_field_map(flattened)

    @staticmethod
    def _field_to_table(context: PromptContext) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for table in context.metadata_json.get("tables") or []:
            table_name = str(table.get("table_name") or "")
            for column in table.get("columns") or []:
                mapping.setdefault(str(column.get("name") or ""), table_name)
        return mapping

    @staticmethod
    def _format_metric(metric: dict[str, Any], field_to_table: dict[str, str]) -> str:
        candidates = list(metric.get("candidate_fields") or [])
        field = str(metric.get("field") or (candidates[0] if candidates else ""))
        expression = str(metric.get("expression") or field)
        table = str(metric.get("table") or field_to_table.get(field, ""))
        location = f"{table}.{field}" if table else field

        aliases = "、".join(metric.get("aliases") or [])
        calculation = str(metric.get("calculation") or "").replace("{field}", expression)

        lines = [f"- {metric.get('name', '')}"]
        if aliases:
            lines.append(f"  业务别名：{aliases}")
        lines.append(f"  当前数据源字段：{location}")
        if metric.get("needs_conversion"):
            lines.append(f"  SQL 表达式：{expression}（已含单位换算，必须照写）")
        description = str(metric.get("description") or "").strip()
        if description:
            lines.append(f"  含义：{description}")
        if calculation:
            lines.append(f"  计算口径：{calculation}")
        return "\n".join(lines)


class KnowledgeProvider(PromptSectionProvider):
    """知识段：分析主题 / 业务对象 / 指标规则。

    所有条目压平成一个 block 列表后统一按相关性排序，因此预算紧张时丢掉的是与当前
    问题最不相关的知识条目，而不是整类知识（例如整段丢失「业务对象」）。
    """

    name = "knowledge"
    title = "业务知识（分析主题 / 业务对象 / 指标规则）"
    priority = 70
    default_max_chars = 1800

    def build(self, context: PromptContext, budget: int) -> PromptSection:
        blocks = self._blocks(context.knowledge or {})
        if not blocks:
            return PromptSection(name=self.name, title=self.title, content="")

        keywords = context.keywords()
        ordered = sort_by_relevance(blocks, lambda block: block, keywords)

        content, dropped, truncated = pack_blocks(
            ordered,
            budget,
            drop_note="…另有 {dropped} 条业务知识因预算未展示",
        )
        return PromptSection(
            name=self.name,
            title=self.title,
            content=content,
            dropped_items=dropped,
            truncated=truncated,
        )

    @staticmethod
    def _blocks(knowledge: dict[str, Any]) -> list[str]:
        blocks: list[str] = []

        for theme in knowledge.get("themes") or []:
            tables = "、".join(theme.get("related_tables") or [])
            description = theme.get("description", "")
            if tables:
                blocks.append(
                    f"- [分析主题] {theme.get('name', '')}：{description}"
                    f"（相关表：{tables}）"
                )
            else:
                # 预留主题：明确说明当前无数据支撑，避免模型凭空造查询
                blocks.append(
                    f"- [分析主题] {theme.get('name', '')}：{description}"
                    "（当前数据底座未接入该主题的数据表，不要为其生成查询）"
                )

        for obj in knowledge.get("objects") or []:
            blocks.append(
                f"- [业务对象] {obj.get('name', '')}：默认表 {obj.get('default_table', '')}，"
                f"关键字段 {obj.get('key_field', '')}。{obj.get('description', '')}".rstrip()
            )

        for rule in knowledge.get("rules") or []:
            mapped_field = rule.get("mapped_field")
            if mapped_field:
                blocks.append(
                    f"- [指标规则] {rule.get('name', '')} -> "
                    f"{rule.get('mapped_table', '')}.{mapped_field}："
                    f"{rule.get('resolved_calculation', '')}"
                )
            else:
                blocks.append(
                    f"- [指标规则] {rule.get('name', '')}：当前数据源缺少对应字段，"
                    "不要臆造该指标"
                )

        return blocks


class RagExampleProvider(PromptSectionProvider):
    """RAG 段：相似问题 + 历史 SQL 示例。

    **示例改写**：示例库里的 SQL 是按标准字段写的（``AVG(defect_rate)``）。当数据源是
    客户表（列名 ``def_rate``）时，原样注入会诱导模型编出不存在的列名 —— 示例从帮助
    变成污染源。因此有字段映射时先经 :class:`~metadata.mapping.columns.ColumnRewriter`
    改写，示例立刻变成「在这张客户表上正确可执行」的示范。

    其余行为：

    - 检索失败（Milvus / 嵌入服务不可用）时输出空段，不影响主流程；
    - 低于 ``min_score`` 的示例直接丢弃，避免不相似示例污染 Prompt；
    - 超出预算时丢弃相似度最低的示例；
    - 同一问题在总预算回收阶段可能被重复构建，因此对检索结果做一层缓存。
    """

    name = "rag"
    title = "相似问题与 SQL 示例"
    priority = 40
    default_max_chars = 1500

    def __init__(
        self,
        max_chars: int | None = None,
        *,
        top_k: int = 3,
        min_score: float | None = 0.45,
        search_fn: Callable[..., list[dict[str, Any]]] | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(max_chars, enabled=enabled)
        self.top_k = max(0, int(top_k))
        self.min_score = min_score
        self.search_fn = search_fn
        self.last_examples: list[dict[str, Any]] = []
        self._cache: tuple[str, list[dict[str, Any]]] | None = None

    def build(self, context: PromptContext, budget: int) -> PromptSection:
        examples = self._search(context.question)
        rewritten = self._rewrite_for_source(context, examples)
        self.last_examples = rewritten
        if not rewritten:
            return PromptSection(name=self.name, title=self.title, content="")

        keywords = context.keywords()
        blocks = [
            self._format_example(index, example)
            for index, example in enumerate(rewritten, start=1)
        ]
        # 相似度已由检索层排好序；同一相似度时用关键词相关性做二次排序
        ordered = sort_by_relevance(blocks, lambda block: block, keywords)

        content, dropped, truncated = pack_blocks(
            ordered,
            budget,
            drop_note="…另有 {dropped} 条示例因预算未展示",
        )
        note = "以上示例仅供参考，必须结合上面给出的真实表结构确认字段后才能使用。"
        content = f"{content}\n{note}" if content else note

        return PromptSection(
            name=self.name,
            title=self.title,
            content=content,
            dropped_items=dropped,
            truncated=truncated,
        )

    # ------------------------------------------------------------------
    # 按当前数据源改写示例
    # ------------------------------------------------------------------
    @staticmethod
    def _rewrite_for_source(
        context: PromptContext, examples: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """把示例 SQL 从标准字段口径改写成当前数据源口径。

        没有字段映射时原样返回（标准模型下示例本来就是对的）。
        改写后**丢弃**依然引用未映射字段的示例：与其给模型一个跑不通的例子，
        不如不给 —— 它在客户表上会直接报 Unknown column。
        """
        field_map = context.metadata_json.get("field_map") or {}
        if not field_map:
            return examples

        tables = list(context.metadata_json.get("tables") or [])
        if not tables:
            return examples

        # 目标表：优先 fact 角色，否则第一张
        target = ""
        for table in tables:
            if str(table.get("role") or "") == "fact":
                target = str(table.get("table_name") or "")
                break
        if not target:
            target = str(tables[0].get("table_name") or "")
        if not target:
            return examples

        replacements: dict[str, str] = {}
        reverse: dict[str, list[str]] = {}
        for item in field_map.get(target) or []:
            standard = str(item.get("standard_field") or "")
            expression = str(item.get("expression") or item.get("column") or "")
            if standard and expression:
                replacements[standard] = expression
            column = str(item.get("column") or "")
            if column and standard:
                reverse.setdefault(column, []).append(standard)

        if not replacements:
            return examples

        rewriter = ColumnRewriter(replacements, reverse=reverse)
        mapped_standards = set(replacements)
        all_standards = {field.name for field in STANDARD_FIELDS}
        unmapped = all_standards - mapped_standards

        result: list[dict[str, Any]] = []
        for example in examples:
            sql = str(example.get("sql") or "")
            rewritten = rewriter.rewrite(sql)
            # 改写后仍带未映射标准字段 -> 该示例在当前数据源上不可执行，丢弃
            if find_identifiers(rewritten.sql, unmapped):
                logger.debug("丢弃与当前数据源不兼容的 RAG 示例：%s", example.get("question"))
                continue
            if rewritten.changed:
                example = {**example, "sql": rewritten.sql, "rewritten": True}
            else:
                example = dict(example)
            example["tables"] = target
            result.append(example)
        return result

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def _search(self, question: str) -> list[dict[str, Any]]:
        if not question or not question.strip() or self.top_k == 0:
            return []
        if self._cache is not None and self._cache[0] == question:
            return self._cache[1]

        search = self.search_fn or self._default_search
        try:
            raw = list(search(question, k=self.top_k, min_score=self.min_score) or [])
        except Exception as exc:  # noqa: BLE001 - 检索失败必须失败开放
            logger.warning("RAG 检索失败，本次跳过示例段：%s", exc)
            self._cache = (question, [])
            return []

        filtered = [example for example in raw if self._passes_threshold(example)]
        filtered.sort(key=lambda example: -self._score_of(example))
        filtered = filtered[: self.top_k]
        self._cache = (question, filtered)
        return filtered

    def _passes_threshold(self, example: dict[str, Any]) -> bool:
        if self.min_score is None:
            return True
        score = example.get("score")
        if score is None:
            return True
        try:
            return float(score) >= float(self.min_score)
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _score_of(example: dict[str, Any]) -> float:
        try:
            return float(example.get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _default_search(
        question: str, *, k: int, min_score: float | None
    ) -> list[dict[str, Any]]:
        # 延迟导入：没有安装 pymilvus / 没配置嵌入服务时仍可导入本模块
        from rag.retriever import search_sql_examples

        return search_sql_examples(question, k=k, min_score=min_score)

    @staticmethod
    def _format_example(index: int, example: dict[str, Any]) -> str:
        lines = [
            f"{index}. 相似问题：{example.get('question', '')}",
            f"   参考 SQL：{example.get('sql', '')}",
        ]
        tables = example.get("tables")
        if tables:
            lines.append(f"   涉及表：{tables}")
        metrics = example.get("metrics")
        if metrics:
            lines.append(f"   涉及指标：{metrics}")
        score = example.get("score")
        if score is not None:
            try:
                lines.append(f"   相似度：{float(score):.3f}")
            except (TypeError, ValueError):
                pass
        return "\n".join(lines)
