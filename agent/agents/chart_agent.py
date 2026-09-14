"""图表配置生成（ECharts）。

内核原本预留了 ``AgentResult.chart_config`` 但一直未实现。本模块补上：
把「用户问题 + 生成的 SQL + 查询结果」交给大模型，产出**可直接被 ECharts
渲染的 option 配置**，失败时回退到确定性启发式规则，保证前端一定有图可画。

为什么由大模型出 option 而不是后端猜：
- 题目要求「分析结果展示：支持可视化输出」，图表类型要贴合语义
  （趋势→折线、占比→饼图、对比→柱状），硬编码规则很难覆盖；
- 前端只做渲染，不需要理解业务语义，前后端契约保持简单。

安全约束：模型只允许产出 ECharts option（纯 JSON），不执行任何代码；
解析失败即降级，不会把异常抛给调用方。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: 大模型生成 ECharts option 的提示词。
CHART_SYSTEM_PROMPT = """你是数据可视化专家。请根据用户的业务问题与查询结果，产出一份 ECharts option 配置。

要求：
1. 只输出一个 JSON 对象，不要输出 Markdown 代码块标记，不要任何解释文字。
2. JSON 结构固定为：
   {"chart_type": "...", "title": "...", "reason": "...", "option": {...}}
3. chart_type 取值范围：bar（对比）、line（趋势）、pie（占比）、scatter（相关性）。
4. option 必须是合法的 ECharts option：
   - 必须包含 xAxis / yAxis（pie 与 scatter 除外）；pie 需 series[0].radius 与 center。
   - series 的 data 必须来自真实查询结果，禁止编造数字。
   - 使用简洁的企业级配色，主色 #1863dc，辅助色 #93939f、#d9d9dd。
   - 不要引入图片、外部链接或 js 函数字符串（formatter 用字符串模板）。
5. reason 用一句中文说明为什么选这种图。
6. 若数据不适合画图（例如只有一行一列、或全是文本），chart_type 输出 "table"。

示例输出：
{"chart_type":"bar","title":"各生产线平均缺陷率","reason":"不同产线之间做横向对比，柱状图最直观。","option":{"tooltip":{"trigger":"axis"},"grid":{"left":48,"right":24,"top":48,"bottom":32},"xAxis":{"type":"category","data":["Line_A","Line_B"]},"yAxis":{"type":"value","name":"缺陷率"},"series":[{"type":"bar","data":[3.9,4.2],"itemStyle":{"color":"#1863dc"}}]}}
"""

#: 最多送给模型多少行，避免 Prompt 过长。
MAX_ROWS_FOR_CHART = 50

#: 允许的图表类型。
_ALLOWED_CHART_TYPES = {"bar", "line", "pie", "scatter", "table"}


class ChartGenerator:
    """根据分析结果生成 ECharts 图表配置。"""

    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------
    def generate(
        self,
        question: str,
        columns: list[str],
        rows: list[list[Any]],
        *,
        sql: str | None = None,
    ) -> dict[str, Any] | None:
        """返回 ``{chart_type, title, option, reason, source}``；无法出图时返回 None。"""
        if not columns or not rows:
            return None

        # 先尝试大模型；失败则回退启发式规则，保证「一定有图」
        if self.llm is not None:
            try:
                generated = self._generate_by_llm(question, columns, rows, sql=sql)
                if generated is not None:
                    generated["source"] = "llm"
                    return generated
            except Exception as exc:  # noqa: BLE001 - 图表失败不应影响主流程
                logger.warning("大模型生成图表配置失败，回退启发式规则：%s", exc)

        fallback = self._fallback(question, columns, rows)
        if fallback is not None:
            fallback["source"] = "fallback"
        return fallback

    # ------------------------------------------------------------------
    # 大模型路径
    # ------------------------------------------------------------------
    def _generate_by_llm(
        self,
        question: str,
        columns: list[str],
        rows: list[list[Any]],
        *,
        sql: str | None,
    ) -> dict[str, Any] | None:
        """调用大模型生成 ECharts option。

        注意：这里**不使用 ``ChatPromptTemplate``**。原因是系统提示词里含有
        ECharts option 的 JSON 示例（大量 ``{`` / ``}``），会被 LangChain 当成
        模板变量从而抛 ``INVALID_PROMPT_INPUT``。改为手工拼接字符串后调用
        ``llm.invoke``，行为等价但不受花括号转义问题影响。
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = self.llm if self.llm is not None else self._default_llm()

        human = (
            f"用户问题：{question}\n\n"
            f"执行的 SQL：\n{sql or '（无）'}\n\n"
            f"查询结果列名：{', '.join(str(c) for c in columns)}\n"
            f"查询结果数据（最多 {MAX_ROWS_FOR_CHART} 行，JSON 数组）：\n"
            f"{json.dumps(self._rows_as_dicts(columns, rows), ensure_ascii=False)}"
        )
        response = llm.invoke(
            [SystemMessage(content=CHART_SYSTEM_PROMPT), HumanMessage(content=human)]
        )
        content = getattr(response, "content", None) or str(response)
        return self._parse(content)

    def _default_llm(self) -> Any:
        from core.llm import get_chat_llm

        return get_chat_llm()

    @staticmethod
    def _rows_as_dicts(
        columns: list[str], rows: list[list[Any]]
    ) -> list[dict[str, Any]]:
        """把行数组转成 [{列名: 值}] 形式，模型更容易理解。"""
        result = []
        for row in rows[:MAX_ROWS_FOR_CHART]:
            result.append(
                {
                    str(col): (None if value is None else value)
                    for col, value in zip(columns, row)
                }
            )
        return result

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_json(text: str) -> str | None:
        """从模型输出中抠出第一个平衡的 JSON 对象。"""
        if not text:
            return None

        # 去掉 ```json ... ``` 包裹
        fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)

        start = text.find("{")
        if start < 0:
            return None

        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
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
        return None

    def _parse(self, content: str) -> dict[str, Any] | None:
        """解析模型返回的 JSON，并做结构校验。"""
        raw = self._extract_json(content)
        if raw is None:
            logger.warning("图表配置解析失败：未找到 JSON 对象")
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("图表配置解析失败：%s", exc)
            return None

        if not isinstance(data, dict):
            return None

        chart_type = str(data.get("chart_type") or "").strip().lower()
        if chart_type not in _ALLOWED_CHART_TYPES:
            logger.warning("图表配置结构不合法，丢弃：type=%s", chart_type)
            return None

        # table 类型：前端直接用表格渲染，不需要 ECharts option，
        # 因此必须在「option 必须是 dict」的校验之前返回。
        if chart_type == "table":
            return {
                "chart_type": "table",
                "title": str(data.get("title") or ""),
                "reason": str(data.get("reason") or ""),
                "option": {},
            }

        option = data.get("option")
        if not isinstance(option, dict) or not option:
            logger.warning("图表配置缺少有效 option，丢弃：type=%s", chart_type)
            return None

        return {
            "chart_type": chart_type,
            "title": str(data.get("title") or ""),
            "reason": str(data.get("reason") or ""),
            "option": option,
        }

    # ------------------------------------------------------------------
    # 启发式兜底
    # ------------------------------------------------------------------
    @staticmethod
    def _is_number(value: Any) -> bool:
        if isinstance(value, bool) or value is None:
            return False
        if isinstance(value, (int, float)):
            return True
        try:
            float(str(value))
            return True
        except (TypeError, ValueError):
            return False

    def _fallback(
        self, question: str, columns: list[str], rows: list[list[Any]]
    ) -> dict[str, Any] | None:
        """确定性兜底：第一列当维度，其余数值列当度量。"""
        if len(columns) < 2:
            return None

        # 找出数值列的索引（至少第一行可转数字）
        numeric_indexes = [
            index
            for index in range(1, len(columns))
            if rows and self._is_number(rows[0][index])
        ]
        if not numeric_indexes:
            return None

        categories = [str(row[0]) for row in rows]

        # 名称含「趋势/变化/最近/每天/每月」→ 折线；否则柱状对比
        trend_words = ("趋势", "变化", "最近", "每天", "每月", "逐月", "走势")
        is_trend = any(word in question for word in trend_words)
        chart_type = "line" if is_trend else "bar"

        series = []
        for index in numeric_indexes:
            series.append(
                {
                    "name": str(columns[index]),
                    "type": chart_type,
                    "data": [row[index] for row in rows],
                    "smooth": True,
                    "itemStyle": {"color": "#1863dc"},
                }
            )

        option = {
            "tooltip": {"trigger": "axis"},
            "legend": {"data": [str(columns[i]) for i in numeric_indexes], "top": 0},
            "grid": {"left": 48, "right": 24, "top": 48, "bottom": 40},
            "xAxis": {
                "type": "category",
                "data": categories,
                "axisLabel": {"color": "#93939f", "rotate": 30},
                "axisLine": {"lineStyle": {"color": "#d9d9dd"}},
            },
            "yAxis": {
                "type": "value",
                "axisLabel": {"color": "#93939f"},
                "splitLine": {"lineStyle": {"color": "#f2f2f2"}},
            },
            "series": series,
        }

        return {
            "chart_type": chart_type,
            "title": str(columns[0]) + " 分析",
            "reason": "按维度列与数值列自动推断的兜底图表。",
            "option": option,
        }
