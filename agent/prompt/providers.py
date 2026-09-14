"""四个 Prompt 段 Provider：元数据 / 业务指标 / 业务知识 / RAG 示例。

每个 provider 自己负责相关性排序与预算裁剪，``build()`` 收到的 ``budget`` 是本次
分配到的字符预算。裁剪原则统一为「按相关性丢弃」：预算不够时丢掉的是与当前问题
最不相关的表 / 指标 / 知识条目 / 示例，而不是把文本从中间切断。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Sequence

from core.metrics import BUSINESS_METRICS, resolve_metrics
from prompt.base import PromptContext, PromptSection, PromptSectionProvider
from prompt.budget import pack_blocks, relevance_score, sort_by_relevance

logger = logging.getLogger(__name__)

__all__ = [
    "DataResourceProvider",
    "KnowledgeProvider",
    "MetricsProvider",
    "RagExampleProvider",
]


class DataResourceProvider(PromptSectionProvider):
    """元数据段：表、字段、样例值、表间关系。

    裁剪策略：

    - 表按与问题的相关性排序装入预算，至少保留 ``min_tables`` 张（相关性全为 0 时
      按元数据原顺序补齐），避免模型完全看不到数据资源；
    - 每张表内的字段同样按相关性排序，只展示前 ``max_columns_per_table`` 个，
      其余折叠并提示模型用 ``sql_db_schema`` 查看完整结构；
    - 样例值只给相关性最高的 ``sample_value_tables`` 张表，因为样例值最吃预算；
    - 表间关系体积小、价值高，先预留预算后无条件带上。
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
        enabled: bool = True,
    ) -> None:
        super().__init__(max_chars, enabled=enabled)
        self.min_tables = max(0, int(min_tables))
        self.max_columns_per_table = max(1, int(max_columns_per_table))
        self.sample_value_tables = max(0, int(sample_value_tables))

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

        # 表间关系体积小、价值高，作为尾块保证一定出现（keep_last）
        relationship_block = self._format_relationships(context)
        if relationship_block:
            blocks = blocks + [relationship_block]

        content, dropped, truncated = pack_blocks(
            blocks,
            budget,
            drop_note=(
                "…另有 {dropped} 张表因预算未展示，可用 sql_db_list_tables 查看全部表、"
                "用 sql_db_schema 查看完整字段与样例值"
            ),
            keep_last=bool(relationship_block),
        )

        return PromptSection(
            name=self.name,
            title=self.title,
            content=content,
            dropped_items=dropped,
            truncated=truncated,
        )

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

    只输出能在当前数据源里匹配到字段的指标；匹配不到的指标单独提示「当前数据源无
    该字段」，防止模型按业务别名臆造列名。条目按与问题的相关性排序。
    """

    name = "metrics"
    title = "业务指标口径"
    priority = 80
    default_max_chars = 1500

    def build(self, context: PromptContext, budget: int) -> PromptSection:
        field_to_table = self._field_to_table(context)

        if context.available_columns:
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
        table = field_to_table.get(field, "")
        location = f"{table}.{field}" if table else field

        aliases = "、".join(metric.get("aliases") or [])
        calculation = str(metric.get("calculation") or "").replace("{field}", field)

        lines = [f"- {metric.get('name', '')}"]
        if aliases:
            lines.append(f"  业务别名：{aliases}")
        lines.append(f"  当前数据源字段：{location}")
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
            tables = "、".join(theme.get("related_tables") or []) or "无"
            blocks.append(
                f"- [分析主题] {theme.get('name', '')}：{theme.get('description', '')}"
                f"（相关表：{tables}）"
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
        self.last_examples = examples
        if not examples:
            return PromptSection(name=self.name, title=self.title, content="")

        keywords = context.keywords()
        blocks = [self._format_example(index, example) for index, example in enumerate(examples, start=1)]
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
