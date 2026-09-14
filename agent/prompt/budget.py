"""Prompt 段的字符预算、相关性排序与裁剪原语。

四个 Prompt 段（元数据 / 指标 / 知识 / RAG 示例）各自持有预算。本模块提供共同的
「抽取关键词 → 按相关性排序 → 装入预算 → 记录丢弃数量」能力，避免每段各写一套截断
逻辑；同时保证裁剪是**按相关性丢弃**而不是把文本从中间切断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

__all__ = [
    "CharBudget",
    "DEFAULT_DROP_NOTE",
    "TRUNCATION_MARKER",
    "extract_keywords",
    "pack_blocks",
    "relevance_score",
    "sort_by_relevance",
    "truncate_text",
]

TRUNCATION_MARKER = "…（已按预算截断）"
DEFAULT_DROP_NOTE = "…另有 {dropped} 项因预算未展示"

_ASCII_WORD_RE = re.compile(r"[a-z_][a-z0-9_]{1,}")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

#: 高频但无区分度的词，参与相关性打分只会引入噪音
_STOPWORDS = frozenset(
    {
        "的", "了", "和", "与", "及", "在", "是", "有", "请", "我", "你", "要",
        "个", "各", "中", "上", "下", "对", "并", "而", "或", "被", "把", "给",
        "从", "到", "为", "以", "哪些", "什么", "多少", "如何", "怎么", "是否",
        "一下", "一个", "这个", "那个", "所有", "分别", "最近", "以及", "根据",
        "进行", "统计", "分析", "查询", "展示", "查看", "生成", "帮我", "计算",
        "对比", "找出", "给出", "输出", "使用", "两个", "三个",
        "the", "and", "for", "with", "from", "that", "this", "show", "give",
        "list", "count", "how", "many", "much", "what", "which", "please",
    }
)


def extract_keywords(text: str | None) -> set[str]:
    """抽取用于相关性排序的关键词：ASCII 词 + 中文 2/3 字切分。"""
    if not text:
        return set()

    lowered = text.lower()
    keywords = {
        word for word in _ASCII_WORD_RE.findall(lowered) if word not in _STOPWORDS
    }
    for run in _CJK_RUN_RE.findall(text):
        for size in (2, 3):
            for start in range(len(run) - size + 1):
                gram = run[start : start + size]
                if gram not in _STOPWORDS:
                    keywords.add(gram)
    return keywords


def relevance_score(text: str | None, keywords: Iterable[str]) -> float:
    """命中关键词的加权得分：长词命中权重更高（``len ** 2``）。"""
    if not text:
        return 0.0
    lowered = text.lower()
    score = 0.0
    for keyword in keywords:
        if keyword and keyword in lowered:
            score += len(keyword) ** 2
    return score


def sort_by_relevance(
    items: Sequence[Any],
    text_of: Callable[[Any], str],
    keywords: Iterable[str],
) -> list[Any]:
    """按相关性稳定排序：得分高的在前，同分保持原顺序。"""
    keywords = tuple(keywords)
    return sorted(items, key=lambda item: -relevance_score(text_of(item), keywords))


@dataclass
class CharBudget:
    """字符预算账本（预留接口）。

    当前各段的用量直接记录在 :class:`prompt.base.PromptSection` 上
    （``used_chars`` / ``max_chars`` / ``dropped_items``），因此这里只保留
    基础能力供外部按需使用。
    """

    max_chars: int
    used_chars: int = 0
    dropped_items: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_chars - self.used_chars)

    @property
    def utilization(self) -> float:
        if self.max_chars <= 0:
            return 0.0
        return round(self.used_chars / self.max_chars, 4)

    def fits(self, text: str) -> bool:
        return len(text or "") <= self.remaining

    def consume(self, text: str) -> None:
        self.used_chars += len(text or "")

    def drop(self, count: int = 1) -> None:
        self.dropped_items += count


def truncate_text(
    text: str, max_chars: int, marker: str = TRUNCATION_MARKER
) -> tuple[str, bool]:
    """按字符裁剪文本，返回 ``(文本, 是否发生截断)``。"""
    text = text or ""
    if len(text) <= max_chars:
        return text, False
    if max_chars <= 0:
        return "", bool(text.strip())
    keep = max(0, max_chars - len(marker))
    return text[:keep].rstrip() + marker, True


def pack_blocks(
    blocks: Sequence[str],
    max_chars: int,
    *,
    drop_note: str = DEFAULT_DROP_NOTE,
    keep_last: bool = False,
) -> tuple[str, int, bool]:
    """按给定顺序把 block 装入预算，并为「已丢弃 N 项」提示预留空间。

    调用方负责先用 :func:`sort_by_relevance` 排好序，因此这里丢弃的永远是相关性
    最低的块。

    Args:
        blocks: 已按相关性排好序的块
        max_chars: 本段可用字符预算
        drop_note: 发生丢弃时追加的提示模板，``{dropped}`` 替换为丢弃数量
        keep_last: 为 True 时保证最后一个块一定出现（用于表间关系这类体积小、
            价值高、缺失会让模型误判关联关系的内容）

    Returns:
        ``(内容, 丢弃数量, 是否发生丢弃)``
    """
    cleaned = [block.rstrip() for block in blocks if block and block.strip()]
    max_chars = max(0, int(max_chars))
    if not cleaned:
        return "", 0, False

    def render_note(dropped: int) -> str:
        if dropped <= 0 or not drop_note:
            return ""
        return "\n" + drop_note.replace("{dropped}", str(dropped))

    tail = ""
    if keep_last and len(cleaned) > 1:
        tail = cleaned[-1]
        cleaned = cleaned[:-1]

    tail_cost = len(tail) + 2 if tail else 0
    if tail and tail_cost > max_chars:
        # 预算连尾块都放不下：只保留尾块本身
        truncated, _ = truncate_text(tail, max_chars)
        return truncated, 1, True

    head_budget = max(0, max_chars - tail_cost)

    kept: list[str] = []
    for block in cleaned:
        candidate = kept + [block]
        dropped = len(cleaned) - len(candidate)
        used = sum(len(item) + 1 for item in candidate)
        if used + len(render_note(dropped)) <= head_budget:
            kept = candidate
            continue
        break

    dropped = len(cleaned) - len(kept)
    content = "\n".join(kept)

    if kept:
        content = f"{content}{render_note(dropped)}"
    elif cleaned:
        # 单个块都放不下：截断第一个块，并保留下方提示
        dropped = len(cleaned) - 1
        note = render_note(dropped)
        truncated, _ = truncate_text(cleaned[0], max(0, head_budget - len(note)))
        content = f"{truncated}{note}"

    if tail:
        content = f"{content}\n\n{tail}" if content.strip() else tail

    return content, dropped, dropped > 0
