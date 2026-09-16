# 表名识别、Prompt 拼接与字段映射技术细节

> 本文回答三个问题：**系统怎么知道该用哪些表**、**Prompt 是怎么拼出来的**、**映射是怎么建立并生效的**。
> 所有结论都能在代码里找到对应实现，关键位置给出「文件:行号」。
>
> 配套阅读：`docs/PROMPT_AND_SQL_GUARD.md`（Prompt 段与只读守卫总览）、`docs/DATA_MODEL.md`（数据模型与映射选型）、`docs/EVALUATION.md`（消融评测）。

---

## 0. 一张图看懂数据流

```text
                       ┌──────────────────────────────────────────────┐
                       │ 数据库（标准底座 fact_production_record       │
                       │        或客户库 mes_prod_log）               │
                       └───────────────────┬──────────────────────────┘
                                           │ ① 扫描 + 排除规则
                            metadata/inventory.py::discover_tables()
                                           │
                                           ▼
                       TableInventory(business_tables / excluded / roles)
                                           │ ② 与 mapping.yaml 合并
              ┌────────────────────────────┼──────────────────────────────┐
              │                            │                              │
              ▼                            ▼                              ▼
   mapping/schema.py           metadata/mapping/compiler.py        mapping/draft.py
   （客户接入契约，人审）        （编译成运行时口径）                 （自动草拟 + LLM 建议）
              │                            │
              └────────────┬───────────────┘
                           ▼
            metadata/metadata_service.py::get_metadata_json()
                           │  输出统一 metadata JSON（含 field_map / metric_bindings / inventory）
        ┌──────────────────┼────────────────────────┐
        ▼                  ▼                        ▼
  知识解析              Prompt 四段组装          SQL Agent 表白名单
  knowledge_service.py  prompt/builder.py       ReadOnlySQLDatabase(include_tables=...)
        │                  │                        │
        └──────────────────┴────────┬───────────────┘
                                    ▼
                    core/prompts.py 的 {context_block} 注入
                                    ▼
              LangChain 官方 SQL Agent（工具：list_tables / schema / query）
                                    ▼
        只读守卫 tools/sql_guard.py → 结果回放 → AgentResult（SQL / 行 / 结论 / prompt_usage）
```

一句话概括三者的分工：

| 层 | 解决什么问题 | 代码落点 |
|---|---|---|
| **表名识别** | 「这个库里有哪几张表、哪几张该给模型看」 | `metadata/inventory.py`、`agents/text2sql_agent.py` |
| **Prompt 拼接** | 「在有限的字符预算里，把最相关的上下文喂给模型」 | `prompt/`（base / budget / providers / builder） |
| **字段映射** | 「业务说的『缺陷率』对应客户哪一列、单位一样吗」 | `metadata/mapping/`、`metadata/standard_fields.py` |

---

# 第一部分：数据库表名识别

## 1.1 核心结论：系统**不做**「问题 → 表」的预选路由

这是最先要说清楚的一点，因为它决定了后面所有设计：

- 系统**没有**「先把问题分类到某张表，再喂给模型」这一层；
- 系统做的是**收敛可见表集合**（白名单），然后让**大模型自己通过工具调用**去确认表名与字段名；
- system prompt 的第 1 条工作流约束把这件事写成了硬性要求：

```python
# core/prompts.py:22
1. 先确认数据：需要用到某张表时，必须先用 sql_db_list_tables 查看可用表、用 sql_db_schema
   确认该表的真实字段名与样例值，禁止凭业务别名猜测列名。
```

**为什么这么设计**：业务问题与表名的对应关系是开放的（「最近一个月不良数量最高的产品」到底查哪张表，取决于客户库长什么样）。用规则或小模型做路由，等于在系统里再塞一个会猜错的组件；而模型天然具备「先看有什么、再决定查什么」的能力，配合只读工具是可控的。

系统的职责因此收敛为三件确定性的事：

1. **收敛范围**：把系统表、原始层、等价表挡在门外（发现模式）；
2. **锁死白名单**：把可见表清单固化进 SQL 工具，模型看不到的东西它就不会查；
3. **提示优先级**：在 Prompt 里把最可能相关的表排在前面（相关性排序 + 保底张数）。

## 1.2 发现模式：`metadata/inventory.py`

原来的实现是 `PRESET_BUSINESS_TABLES = ["fact_production_record"]` —— 业务表白名单硬编码在代码里，换一个客户库就得改代码。现在改成「扫库 + 排除规则」。

### 三层过滤流程

```python
# metadata/inventory.py:127-172（简化）
inspector = inspect(engine)
all_tables = sorted(inspector.get_table_names())        # ① 全量扫描

profile_discovery = dict(profile.discovery) if profile else {}
include_names = include if include is not None else profile_discovery.get("include") or []
exclude_names = {exclude...} | {profile_discovery.get("exclude")...}
exclude_prefixes_all = DEFAULT_EXCLUDE_PREFIXES + exclude_prefixes + profile前缀

candidates = all_tables
if include_names:                                        # ② include 是硬收敛
    candidates = [name for name in include_names if name in all_tables]
    for name in include_names 中不存在的:
        inventory.excluded[name] = "discovery.include 声明了但库里不存在"

for table in candidates:                                 # ③ 逐表判定排除
    reason = _exclusion_reason(table, excluded_tables, exclude_prefixes_all)
    if reason:
        inventory.excluded[table] = reason               # 记账：为什么被排除
        continue
    inventory.business_tables.append(table)
    inventory.roles[table] = role_map.get(table, "other")
```

### 排除规则的三个来源（取并集）

| 来源 | 位置 | 定位 |
|---|---|---|
| 内置通用前缀 | `inventory.py:44-55` `DEFAULT_EXCLUDE_PREFIXES` | 任何客户库都不该暴露的系统表：`information_schema`、`performance_schema`、`mysql.`、`sys.`、`pg_`、`sqlite_`、`_prisma`、`alembic_version`、`django_`、`flyway_schema_history` |
| 内置精确名 | `inventory.py:58-63` `DEFAULT_EXCLUDE_TABLES` | `alembic_version`、`schema_migrations`、`flyway_schema_history`、`sqlite_sequence` |
| 跨客户配置 | `core/config.py:43-44` `DISCOVERY_EXCLUDE_TABLES` / `DISCOVERY_EXCLUDE_PREFIXES` | 通用的、与客户无关的兜底 |
| **客户特有** | `mapping.yaml` 的 `discovery.exclude` / `discovery.exclude_prefixes` | 具体客户的原始层、等价表 |

> **设计理由（写进注释里的约定）**：客户特有的排除项**必须**写在 `mapping.yaml`，不能写进 `core.config`。
> 因为「换个客户就要改代码」正是发现模式要消灭的问题。
> `metadata/mapping/cli.py:227-230` 的 `discover` 输出会主动打印这条提示。

### 角色标注

```python
_VALID_ROLES = {"fact", "dimension", "bridge", "snapshot", "source", "other"}
# metadata/mapping/schema.py:69

ROLE_LABELS = {"fact": "事实表", "dimension": "维表", "bridge": "桥接表",
               "snapshot": "快照表", "source": "原始层", "other": "其他"}
# metadata/inventory.py:66-73
```

角色有两个下游用途：

1. **指标口径落表顺序**：`compiler.py::_compile_metrics` 优先在 `role == "fact"` 的表上解析指标字段；
2. **RAG 示例改写的目标表**：`providers.py::RagExampleProvider._rewrite_for_source` 优先选 fact 表。

### 为什么排除原始层是「正确性要求」而不是洁癖

`DATA_MODEL.md` 记录过一个真实的静默错误：原始层 `intelligent_production_iiot` 是同一个 CSV 的原样导入，**11 个数值指标以 `TEXT` 存储**。一旦它和治理后的 `fact_production_record` 同时可见：

- 模型会在两张内容等价的表之间随机挑一张；
- 挑到 TEXT 那张时，`ORDER BY` / `MIN` / `MAX` 按**字典序**计算，SQL 不报错，但答案是错的。

因此 `mappings/*.mapping.yaml` 的 `discovery.exclude` 都把原始层排除掉。

## 1.3 白名单如何锁进 SQL 工具

```python
# agents/text2sql_agent.py:124-141
self.metadata_json = metadata_json or get_metadata_json()
business_tables = [table["table_name"] for table in self.metadata_json.get("tables", [])]
if not business_tables:
    # 白名单为空必须显式失败：否则 include_tables=None 会让 Agent 看到全部表
    raise RuntimeError(
        "未从元数据中解析到任何业务表，拒绝在缺少表白名单的情况下启动 SQL Agent。"
        "请先执行 scripts/init_preset_schema.py，或检查 DATABASE_URL 配置。"
    )

self.db = ReadOnlySQLDatabase(
    engine=get_readonly_engine(),
    include_tables=business_tables,          # ← 关键：白名单
    sample_rows_in_table_info=settings.sql_sample_rows,
)
```

两个要点：

- **`include_tables` 就是 SQLDatabase 层的表白名单**。LangChain 的 `sql_db_list_tables` 与 `sql_db_schema` 都基于它，所以模型「看得见的表」严格等于 `metadata_json["tables"]`；
- **空白名单必须失败**。如果写成 `include_tables=business_tables or None`，元数据读取失败时会静默退化成「放开全部表」——这是最危险的一种降级。

## 1.4 表级相关性排序与保底张数（Prompt 层）

发现模式决定「哪些表可见」，`DataResourceProvider` 决定「**这次问题先给模型看哪几张表的完整结构**」：

```python
# prompt/providers.py:190-216
def _order_tables(self, tables, keywords):
    scored = [(relevance_score(self._table_text(table), keywords), position, table)
              for position, table in enumerate(tables)]
    relevant = sorted((item for item in scored if item[0] > 0),
                      key=lambda item: (-item[0], item[1]))   # 得分降序，同分保持原序
    chosen = list(relevant)
    chosen_positions = {item[1] for item in chosen}

    if len(chosen) < self.min_tables:                          # 保底：至少 min_tables 张
        for item in scored:
            if item[1] in chosen_positions: continue
            chosen.append(item); chosen_positions.add(item[1])
            if len(chosen) >= self.min_tables: break

    rest = [item for item in scored if item[1] not in chosen_positions]
    return [item[2] for item in chosen + rest]                 # 其余按原顺序排后面
```

- **相关性文本** = 表名 + 表描述 + 每一列的（列名 + 列描述）（`_table_text`，`providers.py:218-224`）；
- **保底张数** = `PROMPT_METADATA_MIN_TABLES`（默认 3）。目的是：即使问句里一个表名关键词都没命中，模型也能看到基础数据资源，而不是面对一个空上下文；
- **其余表仍排在后面**：预算够就会被带上，不够则被 `pack_blocks` 丢弃并提示「另有 N 张表因预算未展示，可用 `sql_db_list_tables` 查看全部表」。

## 1.5 字段说明的来源优先级（三层）

`metadata_service._describe_table` 里，每一列的 `description` 按以下顺序取第一个非空的：

```python
# metadata/metadata_service.py:156-160
description = (
    _mapping_description(mapping, table_name, standards)      # ① mapping.yaml 的标准字段口径
    or str(col.get("comment") or "").strip()                  # ② DDL COMMENT
    or get_column_description(table_name, col_name)           # ③ preset_metadata.py 人工兜底
)
```

**第 ① 条优先于第 ② 条是刻意的**：客户库几乎没有列注释（本次评测的 `mes_prod_log` 45 列全空），而映射里的口径是人审过的、还带单位换算与枚举取值，信息量严格更大。

`_mapping_description` 生成的文案形如：

```text
缺陷率（标准字段 defect_rate），SQL 请写 def_rate * 100，单位 比例(0-1)；...
# metadata/metadata_service.py:195-214
```

表级描述同理：`mapping.table_hints[table]["description"]` → 表 COMMENT → `TABLE_DESCRIPTIONS`（`metadata_service.py:174-180`）。

## 1.6 小结：表名识别链路的五个不变式

1. 库里全部表 → 发现模式 → `business_tables`（唯一入口，没有第二条路径）；
2. `business_tables` 同时喂给三处：`include_tables` 白名单、`metadata_json["tables"]`、Prompt 元数据段；
3. 空白名单 = 启动失败，绝不退化成「全部表」；
4. 被排除的表**一定带原因**（`inventory.excluded`），供接入排障；
5. 具体用哪张表由模型通过只读工具自己确认，系统只负责让正确的表可见、并把它排在前面。

---

# 第二部分：Prompt 拼接

## 2.1 分层结构

Prompt 由**静态模板**与**动态上下文**两部分组成，通过一个 Prompt 变量对接（`prompt/builder.py:1-12`）。

| 部分 | 内容 | 位置 | 何时确定 |
|---|---|---|---|
| 静态 system 模板 | 角色设定、6 条工作流约束、`{dialect}` / `{top_k}` / `{context_block}` 占位符 | `core/prompts.py:17-31` | 代码里写死 |
| 动态上下文 | 元数据 / 指标 / 知识 / RAG 四段 | `prompt/builder.py` 每次请求组装 | 每次提问时 |

```python
# agents/text2sql_agent.py:76-84 —— 消息序列
prompt = ChatPromptTemplate.from_messages([
    ("system", prompt_builder.system_template()),                    # 静态模板 + {context_block}
    MessagesPlaceholder(variable_name="chat_history", optional=True),# 多轮历史
    ("human", "{input}"),                                            # 用户问题
    ("ai", SQL_FUNCTIONS_SUFFIX),                                    # LangChain 官方工具调用前后缀
    MessagesPlaceholder(variable_name="agent_scratchpad"),           # 工具轨迹占位符
])
```

真正把变量填进去的地方：

```python
# agents/text2sql_agent.py:306-312
response = self.agent.invoke({
    "input": user_input,
    "context_block": build.context_block,   # ← 四段上下文在这里注入
    "chat_history": history,
})
```

### 为什么必须用 `{context_block}` 注入，而不是 `str.format()` 直接拼

历史实现是：

```python
SQL_AGENT_PREFIX.format(data_resources, knowledge_summary, metrics, business_rules)
```

它有两个致命问题（`docs/PROMPT_AND_SQL_GUARD.md:5-11`）：

1. **没有预算控制**：上下文有多少塞多少；
2. **业务文本里的 `{` 会炸**：表结构、字段说明、样例值都来自数据库，客户库里出现 `{1: 正常, 0: 停用}` 这种取值时，`str.format()` 会抛 `KeyError` 或把内容吃掉。

现在动态上下文是**运行时变量**，不参与模板解析，任何花括号都安全。回归测试见 `tests/test_prompt_providers.py::test_system_template_renders_context_block_with_braces`。

## 2.2 四段 Provider 体系

### 抽象契约：`prompt/base.py`

```python
# prompt/base.py:87-153（结构）
class PromptSectionProvider(ABC):
    name: str = "section"          # 段标识，用于 result.section("rag") 取用
    title: str = "Section"         # 渲染进 Prompt 的小标题
    priority: int = 50             # 数值越大越重要；总预算不足时先压缩数值小的
    default_max_chars: int = 1500

    def provide(self, context, budget=None) -> PromptSection:   # ← 模板方法
        effective = self._effective_budget(budget)               # 不超过本段上限，也不超过调用方分配值
        if not self.enabled:                                     # 消融开关：返回空段但不报错
            return PromptSection(name=self.name, title=self.title, max_chars=effective)
        try:
            section = self.build(context, effective)
        except Exception as exc:                                 # 失败开放：单段异常不阻断整次请求
            logger.warning("Prompt 段 %s 构建失败，已跳过：%s", self.name, exc)
            section = PromptSection(..., error=str(exc))
        return self._finalize(section, effective)                 # 预算兜底 + 记录用量

    @abstractmethod
    def build(self, context: PromptContext, budget: int) -> PromptSection: ...
```

三个由模板方法统一保证的不变式：

| 不变式 | 实现 | 意义 |
|---|---|---|
| **任何段都不超预算** | `_finalize` 里 `truncate_text` | Provider 自己算错也不会撑爆 Prompt |
| **单段失败不影响整次请求** | `try/except` + `error` 字段 | 比如 Milvus 挂了，RAG 段为空，问答照常 |
| **用量可观测** | `PromptSection.to_dict()` | 前端与日志能看到「哪一段吃掉了预算」 |

### 四段的位置与优先级

| 段 | class | `name` | `priority` | 默认预算 | 配置文件项 |
|---|---|---|---|---|---|
| 元数据 | `DataResourceProvider` | `metadata` | **90** | 3000 | `PROMPT_METADATA_BUDGET` |
| 指标 | `MetricsProvider` | `metrics` | **80** | 1500 | `PROMPT_METRICS_BUDGET` |
| 知识 | `KnowledgeProvider` | `knowledge` | **70** | 1800 | `PROMPT_KNOWLEDGE_BUDGET` |
| RAG 示例 | `RagExampleProvider` | `rag` | **40** | 1500 | `PROMPT_RAG_BUDGET` |

```python
# prompt/builder.py:99-128
@classmethod
def from_settings(cls) -> "SQLAgentPromptBuilder":
    return cls(
        providers=[
            DataResourceProvider(settings.prompt_metadata_budget,
                                 min_tables=settings.prompt_metadata_min_tables,
                                 max_columns_per_table=settings.sql_max_columns_per_table,
                                 sample_value_tables=settings.prompt_metadata_sample_tables,
                                 field_map_budget=settings.prompt_field_map_budget),
            MetricsProvider(settings.prompt_metrics_budget, enabled=settings.prompt_metrics_enabled),
            KnowledgeProvider(settings.prompt_knowledge_budget),
            RagExampleProvider(settings.prompt_rag_budget, top_k=settings.rag_top_k,
                               min_score=settings.rag_min_score, enabled=settings.rag_enabled),
        ],
        total_budget=settings.prompt_total_budget,       # 默认 8000
    )
```

> 注意 `priority` 的相对关系：**RAG 段最低**。这是刻意的——
> RAG 示例是「锦上添花」，元数据是「没有它模型就在猜列名」。预算紧张时先牺牲 RAG。

## 2.3 相关性原语：`prompt/budget.py`

四段共用同一套「抽关键词 → 打分 → 排序 → 装预算」原语，避免每段各写一套截断逻辑。

### 关键词抽取

```python
# prompt/budget.py:46-61
def extract_keywords(text: str | None) -> set[str]:
    lowered = text.lower()
    keywords = {word for word in _ASCII_WORD_RE.findall(lowered)   # [a-z_][a-z0-9_]{1,}
                if word not in _STOPWORDS}
    for run in _CJK_RUN_RE.findall(text):                          # 连续中文串
        for size in (2, 3):                                        # 2-gram + 3-gram
            for start in range(len(run) - size + 1):
                gram = run[start:start + size]
                if gram not in _STOPWORDS:
                    keywords.add(gram)
    return keywords
```

- **中文用 2/3 字切分**：中文没有空格，「各产线的平均缺陷率」需要切出「缺陷」「缺陷率」这样的词；
- **停用词表**（`budget.py:32-43`）同时覆盖中英文，且把「统计/分析/查询/展示」这类**动作词**也删掉——它们在每个问题里都出现，只会引入噪音。

### 相关性打分

```python
# prompt/budget.py:64-73
def relevance_score(text: str | None, keywords: Iterable[str]) -> float:
    lowered = text.lower()
    score = 0.0
    for keyword in keywords:
        if keyword and keyword in lowered:
            score += len(keyword) ** 2      # ← 长词命中权重更高
    return score
```

`len ** 2` 的含义：命中「缺陷率」（3 字）得 9 分，命中「缺陷」（2 字）得 4 分。长词更具体、更能说明相关性，所以权重平方放大。

### 装预算 + 记录丢弃

```python
# prompt/budget.py:132-202（语义摘要）
def pack_blocks(blocks, max_chars, *, drop_note, keep_last=False):
    """
    - blocks 已由调用方按相关性排好序 → 因此丢弃的永远是相关性最低的块
    - keep_last=True 时先扣下最后一个块，保证它一定出现（用于表间关系）
    - 为「…另有 N 项因预算未展示」提示预留空间（提示本身也计预算）
    - 一个块都放不下时：截断第一个块并保留提示，而不是返回空
    """
```

**这是整套预算机制的核心原则：预算不够时丢掉的是最不相关的项，而不是把文本从中间切断。**
被丢弃时还会追加一句提示，告诉模型「还有内容没展示、可以用工具去看完整结构」——不会让模型误以为信息就是这些。

## 2.4 总预算回收：`_shrink_to_total`

四段各自裁剪后仍可能合计超预算，此时按优先级从低到高逐段压缩：

```python
# prompt/builder.py:155-174
def _shrink_to_total(self, context, sections):
    overflow = self._total_used(sections) - self.total_budget
    order = sorted(range(len(sections)), key=lambda i: self.providers[i].priority)  # 优先级升序

    for index in order:
        if overflow <= 0: break
        current = sections[index]
        target = max(self.min_section_chars, current.used_chars - overflow)   # 每段至少留 200 字符
        if target >= current.used_chars: continue
        sections[index] = self.providers[index].provide(context, budget=target)  # 用更小预算重建该段
        overflow = self._total_used(sections) - self.total_budget
    return sections
```

压缩顺序因此是：**RAG(40) → 知识(70) → 指标(80) → 元数据(90)**，即最后才动元数据段。
`min_section_chars` 默认 200，保证任何段都不会被压成 0 而失去存在感。

> 注意：RAG 段可能被**重复构建**（第一遍用原预算、回收时用更小预算），
> 所以 `RagExampleProvider` 内部对检索结果做了一层缓存（`providers.py:530, 636-651`），避免二次调用 Milvus。

## 2.5 四段各自的内容与裁剪策略

### 段一：元数据段 `DataResourceProvider`（`providers.py:33-301`）

**输入**：`context.metadata_json["tables"]`、`["relationships"]`、`["field_map"]`。

**渲染格式**：

```text
### 数据资源（可用表与字段）
- fact_production_record：生产记录事实宽表
  - fact_production_record.record_id INTEGER [主键]：记录唯一编号（样例: 1）
  - fact_production_record.machine_id VARCHAR(16)：设备编号（样例: M07）
  ...
  - …另有 20 个字段未展示，可用 sql_db_schema 查看完整字段
- mes_prod_log：客户 MES 生产日志宽表
  ...

标准业务字段与当前数据源实际列的对应关系（业务问题用的是标准字段名，生成 SQL 必须使用实际列名或给出的表达式）：
[mes_prod_log]
- 缺陷率(defect_rate) -> 列 def_rate，SQL 必须写成 def_rate * 100（客户单位 比例(0-1)，口径单位 百分比）
- 生产班次(shift) -> 列 sft；取值：Morning / Afternoon / Night

需要单位换算的字段（漏掉换算会让结果整体差一个倍数）：
- 缺陷率：客户列 def_rate × 100 -> 写成 def_rate * 100
- 设备利用率：客户列 util × 100 -> 写成 util * 100

表间关系：
- a.x -> b.y（many_to_one）
```

**裁剪策略**（`providers.py:33-48` 的 docstring 就是设计说明）：

| 规则 | 参数 | 理由 |
|---|---|---|
| 表按相关性排序 + `min_tables` 保底 | `PROMPT_METADATA_MIN_TABLES` | 避免模型完全看不到数据资源 |
| 单表内字段按相关性排序，只展示前 N 个，其余折叠并提示用 `sql_db_schema` | `SQL_MAX_COLUMNS_PER_TABLE`（25） | 45 列宽表会吃掉全部预算 |
| 样例值只给相关性最高的 K 张表 | `PROMPT_METADATA_SAMPLE_TABLES`（2） | 样例值最吃预算，而它主要用来识别枚举列 |
| 字段映射块单独预算 | `PROMPT_FIELD_MAP_BUDGET`（900） | 客户库列名与业务词不对应时，这一块是正确率关键 |
| 尾块 `keep_last=True` 保证出现 | — | 有表间关系时输出关系；单表模型下改为「不要生成 JOIN」提示 |

尾块的两种形态：

```python
# prompt/providers.py:98-107
tail_block = self._format_relationships(context)
if not tail_block and len(ordered) == 1:
    tail_block = (f"当前数据底座为单表模型：所有分析都在 "
                  f"{ordered[0].get('table_name')} 内完成，不要生成 JOIN。")
```

单表模型下这条提示是在挡一个具体问题：冗余维度编码（`production_line=Line_A`）在单表里也存着，
模型可能自己造一个自连接，把行数放大后算出错误结果。

**没有映射时的行为完全不变**：`_format_field_map` 在 `field_map` 为空时返回 `""`（`providers.py:137-139`），
该块不出现，其余逻辑与加入映射之前一模一样。

### 段二：指标段 `MetricsProvider`（`providers.py:304-417`）

**两条数据来源，优先用前者**：

```python
# prompt/providers.py:323-350 + 371-384
def build(self, context, budget):
    field_to_table = self._field_to_table(context)
    mapped = self._mapped_metrics(context)
    if mapped is not None:                      # ① 有映射：用编译产物
        metrics = mapped
    elif context.available_columns:             # ② 无映射：启发式匹配
        metrics = resolve_metrics(context.available_columns)
    else:                                       # ③ 连列名都没有：列全部预置指标
        metrics = list(BUSINESS_METRICS)
    ...

@staticmethod
def _mapped_metrics(context):
    field_map = context.metadata_json.get("field_map")
    if not field_map:
        return None                             # None = 请走旧路径（与「空列表」语义严格区分）
    precomputed = context.metadata_json.get("metric_bindings")
    if precomputed:
        return list(precomputed)                # 编译期已算好最终表达式，直接用
    flattened = [item for items in field_map.values() for item in (items or [])]
    return resolve_metrics_from_field_map(flattened)
```

- **路径 ①**（`metric_bindings`）：`compiler.py::_compile_metrics` 产出的、**已落到客户侧表达式**的指标口径，例如「缺陷率 → `AVG(def_rate * 100)`」；
- **路径 ②**（`core/metrics.resolve_metrics`）：按标准候选字段名去撞实际列名的启发式匹配，也就是本项目原先的行为；
- 两个路径返回**同构的 dict**，所以 `MetricsProvider` 不需要关心数据源是标准模型还是客户映射（`core/metrics.py:9-11`）。

**渲染格式**：

```text
### 业务指标口径
- 缺陷率
  业务别名：不良率、缺陷比例、defect rate、NG率
  当前数据源字段：mes_prod_log.def_rate
  SQL 表达式：def_rate * 100（已含单位换算，必须照写）
  含义：反映生产质量缺陷水平，数值越高表示质量越差。
  计算口径：统计平均值可用 AVG(def_rate * 100)，按产线/产品/班次/设备分组时配合 GROUP BY 使用。
```

**匹配不到指标时给的是「防幻觉提示」而不是空段**：

```python
# prompt/providers.py:330-348
return PromptSection(..., content=(
    "当前数据源未匹配到预置业务指标字段，"
    "请严格按真实表结构分析，不要按业务别名猜测列名。"))
```

这一条很重要：如果只给空段，模型会倾向于按「缺陷率」这个业务词**臆造一个列名**。

### 段三：知识段 `KnowledgeProvider`（`providers.py:420-492`）

**输入**：`knowledge/knowledge_service.resolve_knowledge()` 的输出（主题 / 业务对象 / 指标规则）。

```python
# knowledge/knowledge_service.py:43-106（解析逻辑）
# 业务对象：default_table 存在于当前库才保留
# 指标规则：先在 prefer_table 里按 candidate_fields 匹配，再退到其他表；匹配不到则显式标记缺口
# 分析主题：related_tables 过滤为当前库真实存在的表
```

**关键设计：主题 / 对象 / 规则被压平成一个 block 列表，统一按相关性排序**（`providers.py:432-444`）。
这样预算紧张时丢掉的是「与当前问题最不相关的条目」，而不是整类知识（比如整段丢失「业务对象」）。

三种条目的渲染：

```text
- [分析主题] 质量分析：围绕缺陷率、良率、质量得分等进行分析。（相关表：fact_production_record）
- [分析主题] 库存分析：...（当前数据底座未接入该主题的数据表，不要为其生成查询）   ← 预留主题显式说明
- [业务对象] 设备：默认表 fact_production_record，关键字段 machine_id。...
- [指标规则] 缺陷率 -> fact_production_record.defect_rate：AVG(defect_rate)
- [指标规则] 库存周转率：当前数据源缺少对应字段，不要臆造该指标                  ← 缺口显式说明
```

两处「显式说明」是同一思路：**告诉模型「这里没有」，比让它自己去猜要安全得多**。

### 段四：RAG 示例段 `RagExampleProvider`（`providers.py:495-698`）

**检索链路**：

```python
# providers.py:633-651 → rag/retriever.py:19-34 → rag/sql_example_store.py
result = search_sql_examples(question, k=self.top_k, min_score=self.min_score)
filtered = [ex for ex in raw if self._passes_threshold(ex)]     # 低于 min_score 直接丢
filtered.sort(key=lambda ex: -self._score_of(ex))               # 相似度降序
filtered = filtered[: self.top_k]
```

四个行为约束（都写进了 `providers.py:503-508` 的 docstring）：

| 行为 | 实现 | 理由 |
|---|---|---|
| 检索失败输出空段 | `except Exception → logger.warning + return []` | Milvus / 嵌入服务不可用不能影响主流程（fail-open） |
| 低于 `RAG_MIN_SCORE` 丢弃 | `_passes_threshold` | 不相似的示例是污染源，不是帮助 |
| 超预算丢弃相似度最低的 | `sort_by_relevance` 二次排序 + `pack_blocks` | 关键词相关性做同分二次排序 |
| 结果缓存 | `self._cache = (question, filtered)` | 总预算回收阶段会重复构建该段 |

**渲染格式**：

```text
### 相似问题与 SQL 示例
1. 相似问题：统计各产线的平均缺陷率
   参考 SQL：SELECT production_line, AVG(def_rate * 100) FROM mes_prod_log GROUP BY production_line
   涉及表：mes_prod_log
   涉及指标：缺陷率
   相似度：0.732
以上示例仅供参考，必须结合上面给出的真实表结构确认字段后才能使用。        ← 固定尾注
```

**最重要的部分是「示例改写」**，见第四部分 §4.5。

## 2.6 组装结果与可观测性

```python
# prompt/builder.py:134-153
def build_context(self, context: PromptContext) -> PromptBuildResult:
    sections = [provider.provide(context) for provider in self.providers]
    if self.total_budget and self._total_used(sections) > self.total_budget:
        sections = self._shrink_to_total(context, sections)
    rendered = [section.render() for section in sections]        # 空段返回 ""，不占位
    body = "\n\n".join(part for part in rendered if part)
    if not body:
        body = f"### 业务上下文\n{EMPTY_CONTEXT_HINT}"           # 兜底：告诉模型先用工具看表
    ...
```

`PromptSection.render()` 的输出格式是 `### {title}\n{content}`，空段直接返回空串——
**空段不占位、不留标题**，这是「段开关（消融）」能干净生效的前提。

用量信息有两个出口（`builder.py:57-80`）：

```python
def usage(self) -> dict:
    return {"total_budget": ..., "used_chars": ..., "over_budget": ...,
            "sections": [section.to_dict() for section in self.sections]}

def summary(self) -> str:
    # "总计 6421/8000 字符：metadata=2870/3000，metrics=1120/1500，knowledge=980/1800，rag=1451/1500(丢弃1)"
```

- 进日志：`agents/text2sql_agent.py:299` `logger.info("Prompt 段用量：%s", build.summary())`
- 进结果：`agents/text2sql_agent.py:292` `result.prompt_usage = build.usage()`
- 进前端：`frontend/src/pages/ChatPage.tsx:380-386` 展示「PROMPT 预算用量 · used/total 字符」与逐段列表

## 2.7 降级与失败开放

```python
# agents/text2sql_agent.py:338-346
def _safe_build_context(self, question: str) -> PromptBuildResult:
    """Prompt 组装失败不应该阻断查询。"""
    try:
        return self.build_context(question)
    except Exception as exc:
        logger.warning("Prompt 上下文构建失败，降级为空上下文：%s", exc)
        return PromptBuildResult(context_block="", sections=[], total_budget=0, used_chars=0)
```

三层降级形成一个完整的失败开放链：

```text
单个 Provider.build() 抛异常  → 该段为空（error 记录），其余三段正常
      ↓
整次 build_context() 抛异常   → 空上下文，模型仍可用工具看表（system prompt 仍完整）
      ↓
Prompt 段没有内容             → context_block 注入 EMPTY_CONTEXT_HINT，引导模型先用工具
```

---

# 第三部分：字段映射（Mapping）

## 3.1 要解决的问题

系统的目标是面向**未知的客户数据库**。客户表里可能是 `def_rate`、`不良率`、`NG_RATE`，也可能一列注释都没有。业界常见做法是把表结构直接丢给模型让它猜，正确率会随客户命名习惯剧烈波动。

本项目的方案是引入一个稳定的中间层：

```text
标准字段（metadata/standard_fields.py，稳定、有词典）
        +
mapping.yaml（每个客户一份，人审、可 diff）
        ↓
可执行的字段口径（列名 + 单位换算表达式 + 枚举取值 + grain）
```

一句话：**词典是稳定的，客户列是可变的，两者通过一份契约文件对齐。**

## 3.2 标准字段词典：`metadata/standard_fields.py`

`STANDARD_FIELDS` 定义 **45 个标准字段**（`tests/test_mapping.py::test_standard_field_count_is_45` 守着这个数字），每个字段：

```python
# metadata/standard_fields.py:59-92
@dataclass(frozen=True)
class StandardField:
    name: str                              # 规范字段名（英文标识符），也是标准口径 SQL 里的列名
    label: str                             # 中文名，渲染进 Prompt，也是业务人员提问时用的词
    unit: str                              # 规范单位（客户列不同则必须声明 scale）
    data_kind: str                         # measure / dimension / key / time
    column_candidates: tuple[str, ...] = ()# 客户库里该字段可能叫什么（用于自动草拟与反向匹配）
    aliases: tuple[str, ...] = ()          # 业务别名
    group: str = "其他"                     # 业务分组，用于 Prompt 分组展示

    def candidate_set(self) -> set[str]:
        raw = (self.name, self.label, *self.column_candidates, *self.aliases)
        return {normalize_identifier(item) for item in raw if item}
```

业务分组（`STANDARD_FIELD_GROUPS`）：
`主键与维度 / 环境与设备传感器 / 产量效率能耗 / 质量与设备健康 / 改善类指标 / 生产模式`。

**为什么 `unit` 是必需字段**：真实客户库里同一个业务量常有两种存法——缺陷率有的存 `3.9`（百分数），有的存 `0.039`（比例）。标准字段固定一个规范单位，客户列用 `scale` 声明换算系数，生成的 SQL 里带上换算，结果才与标准口径可比。没有这一层，「平均缺陷率」会**静默**差 100 倍。

### 业务指标口径

`STANDARD_METRICS`（7 条：缺陷率、良率/直通率、质量得分、停机时长、故障次数、产量、设备利用率）指向标准字段：

```python
# metadata/standard_fields.py:95-107 + 590-596
@dataclass(frozen=True)
class StandardMetric:
    name: str                # 「缺陷率」
    aliases: tuple[str, ...] # 不良率 / 缺陷比例 / defect rate / NG率
    standard_field: str      # defect_rate
    description: str
    calculation: str         # "统计平均值可用 AVG({field})，..."  ← {field} 会被替换成客户侧表达式

    def render_calculation(self, field_expression: str) -> str:
        return self.calculation.format(field=field_expression)
```

历史上指标口径散在 `core/metrics.py` 与 `knowledge/knowledge_base.py` 两处，现在统一到这里，`core/metrics.py` 只是 Prompt 层适配器（`core/metrics.py:3-11`）。

### 归一化与反向匹配

```python
# metadata/standard_fields.py:132-142
def normalize_identifier(value: str) -> str:
    """defect_rate / defectRate / Defect-Rate / defect rate / DEFECTRATE → defectrate"""
    text = str(value or "").strip().lower()
    for char in ("_", "-", " ", ".", "（", "）", "(", ")", "/", "\\"):
        text = text.replace(char, "")
    return text
```

```python
# metadata/standard_fields.py:682-724
def match_columns(columns, *, table="", fields=STANDARD_FIELDS, index=None) -> list[ColumnMatch]:
    lookup = index or build_match_index(fields)          # 归一化候选名 -> [标准字段名]
    for column in columns:
        candidates = lookup.get(normalize_identifier(column), [])
        if len(candidates) == 1:  status = MATCHED       # 唯一命中
        elif len(candidates) > 1: status = AMBIGUOUS     # 歧义，交给人
        else:                     status = UNMAPPED      # 客户特有列
```

**只做唯一匹配是刻意的**：自动草拟映射时「猜错」比「标为歧义」代价大得多——一个错误的字段映射会让生成的 SQL **静默算错**，而不是报错。
典型歧义例子：`util` 同时像「设备利用率」和「资源利用率」（`build_match_index` 会把它记成多对多，`standard_fields.py:665-679`）。

## 3.3 客户接入契约：`mapping.yaml`

### 完整结构

```yaml
# mappings/mes_prod_log.mapping.yaml（节选）
schema_version: "1.0"
profile: mes_prod_log                          # profile 名，进 metadata_json["mapping_profile"]
description: 某厂 MES 生产日志（无列注释，缩写字列名，比率型单位）

database:                                      # 元信息，仅供人读 / 排障
  dialect: mysql
  url_env: DATABASE_URL
  note: 与标准底座同库；本表是客户 MES 侧的生产日志视图

discovery:                                     # 客户特有的发现模式配置
  exclude:
    - intelligent_production_iiot
    - fact_production_record

relationships: []                              # 表间关系（单表模型为空）

tables:
  - name: mes_prod_log
    role: fact                                 # fact / dimension / bridge / snapshot / source / other
    description: 客户 MES 生产日志宽表，一行代表一次生产运行。
    grain: 一行 = 一次生产运行（rec_no 唯一）
    primary_key: rec_no
    time_column: ""
    notes: 45 列全部没有数据库注释，字段语义完全来自本映射。

    columns:
      - column: def_rate                       # 客户物理列名
        standard_fields:
          - name: defect_rate                  # 对应哪个标准字段（必须在词典里）
            scale: 100                         # 换算系数
            expression: def_rate * 100         # SQL 里必须写成这个
            unit: 比例(0-1)                     # 客户列单位
            notes: 客户列存 0.039，标准口径为 3.9；SQL 必须写成 def_rate * 100

      - column: sft
        standard_fields:
          - name: shift
            role: dimension
            enum_values: [Morning, Afternoon, Night]

      - column: env_t
        standard_fields:
          - name: ambient_temperature
            unit: 摄氏度
```

### 各键的允许集合（未知键一律报错）

```python
# metadata/mapping/schema.py:36-69
_TABLE_KEYS   = {"name","role","description","grain","primary_key","time_column","columns","notes"}
_COLUMN_KEYS  = {"column","standard_fields"}
_BINDING_KEYS = {"name","expression","scale","unit","enum_values","role","notes"}
_TOP_KEYS     = {"schema_version","profile","description","database","discovery","relationships","tables"}
_DISCOVERY_KEYS = {"include","exclude","exclude_prefixes","exclude_roles"}
_RELATION_KEYS  = {"source_table","source_column","target_table","target_column",
                   "relation_type","source_standard_field","target_standard_field"}
_VALID_ROLES  = {"fact","dimension","bridge","snapshot","source","other"}
```

**为什么未知键要报错**：写错键名（比如把 `expression` 写成 `expr`）如果被静默忽略，后果是一个没有单位换算的绑定悄悄上线。

### 校验规则（两层）

**第一层：结构校验**（`parse_profile`，`schema.py:299-446`）

| 规则 | 结果 |
|---|---|
| 顶层不是 dict / `tables` 不是 list | 抛 `MappingError` |
| 出现未知键 | error（附允许集合） |
| 表/列/绑定缺 `name` / `column` | error |
| `scale` 不是数字 / `enum_values` 不是列表 | error |
| `role` 不在允许集合 | error |

**第二层：业务校验**（`validate_profile`，`schema.py:452-562`）

| 规则 | 级别 | 意图 |
|---|---|---|
| 标准字段名不在词典里 | **error** | 防止接入时自造字段名——否则有人会为了「让它过」去改词典 |
| 同一标准字段在**本表内**重复绑定 | **error** | 会造成口径二义 |
| `expression` 为空 | **error** | 绑定必须可执行 |
| `scale == 0` | **error** | 换算系数为 0 一定是写错了 |
| 表重复声明 | **error** | — |
| 同一列被声明多次 | warning | 可能是复制粘贴 |
| `--against-db` 时表/列/主键在库里不存在 | **error** | 接入时最常见的错（拼错列名、表换版本） |
| 表同时出现在 `discovery.exclude` 与 `tables` 里 | **error** | 发现模式会把映射目标排除掉，属于自相矛盾 |

**缺失映射不报错**：「客户表里有 12 列没有标准口径」是正常情况，由 `mapping_coverage` 统计出来展示给接入工程师（`schema.py:565-580`），而不是当成失败。

## 3.4 编译：从契约到运行时口径

`mapping.yaml` 只说「哪一列是什么」，`compiler.py::compile_profile` 把它和**数据库真实结构**合起来，产出一份自洽的运行时视图。

```python
# metadata/mapping/compiler.py:84-125
@dataclass
class CompiledMapping:
    profile: MappingProfile
    tables: list[str]                                   # 映射声明的表名
    fields: dict[str, list[ResolvedField]]              # 表名 -> 已解析字段
    table_hints: dict[str, dict[str, Any]]              # 表级语义（role/description/grain/主键/时间列/notes）
    metrics: list[dict[str, Any]]                       # 业务指标口径，已落到客户侧表达式
    unmatched_metrics: list[str]                        # 客户表里找不到字段的指标名

    def expression_map(self, table) -> dict[str, str]:  # 标准字段 -> 客户侧表达式
    def rewriter(self, table) -> ColumnRewriter:        # 构造该表的 SQL 改写器
    def unmapped_standard_fields(self, table) -> list[str]
    def mentions_unmapped(self, table, sql) -> list[str]
```

### 字段口径的解析（`_resolve_field`）

```python
# metadata/mapping/compiler.py:41-81 + 173-202
@dataclass
class ResolvedField:
    standard_field: str
    table: str
    column: str          # 客户物理列
    expression: str      # SQL 里要写的写法（可能含 ×100）
    label: str           # 标准字段中文名
    unit: str            # 标准口径单位
    data_kind: str       # measure / dimension / key / time
    group: str
    customer_unit: str   # 客户侧单位
    scale: float | None
    enum_values: tuple[str, ...]
    notes: str

    @property
    def is_plain(self) -> bool:      return self.expression == self.column
    @property
    def needs_conversion(self) -> bool: return not self.is_plain
```

客户单位在未显式声明时按换算系数**推断**，仅用于向模型解释差异：

```python
# compiler.py:195-202
def _infer_customer_unit(standard_unit: str, scale: float) -> str:
    if standard_unit == "百分比":
        if scale == 100:  return "比例(0-1)"
        if scale == 0.01: return "百分比(0-100)"
    return f"×{scale:g}"
```

### 指标口径的落表顺序（`_compile_metrics`）

```python
# metadata/mapping/compiler.py:236-278（语义摘要）
fact_tables = [t.name for t in profile.tables if table_hints[t.name]["role"] == "fact"]
search_order = fact_tables + [其余表按声明顺序]

for metric in STANDARD_METRICS:
    matched = None
    for table_name in search_order:
        item = compiled.field(table_name, metric.standard_field)
        if item is not None:
            matched = (table_name, item); break
    if matched is None:
        compiled.unmatched_metrics.append(metric.name)     # 显式记账：本数据源没有这个指标
        continue
    compiled.metrics.append({
        "name": metric.name, "aliases": list(metric.aliases),
        "table": table_name, "standard_field": metric.standard_field,
        "column": item.column, "expression": item.expression,
        "location": f"{table_name}.{item.column}",
        "description": metric.description,
        "calculation": metric.render_calculation(item.expression),  # {field} → 客户侧表达式
        "needs_conversion": item.needs_conversion,
    })
```

例子：标准口径 `AVG(缺陷率)` 在客户表上编译成 `AVG(def_rate * 100)`——单位换算已经嵌进计算口径里，
模型只要照抄就不会错。

### 缓存

```python
# metadata/mapping/__init__.py:76-105
@lru_cache(maxsize=8)
def _cached_mapping(path_text: str) -> CompiledMapping: ...

def resolve_mapping(path=None, *, use_cache=True) -> CompiledMapping | None:
    """未配置映射时返回 None（调用方退回标准模型）。"""
```

`use_cache=False` 是给 CLI 审核与评测用的——改了 YAML 必须立刻看到效果。

## 3.5 映射如何进入元数据 JSON

`metadata_service.get_metadata_json` 是唯一的汇合点（`metadata_service.py:63-118`）：

```python
mapping = mapping or resolve_mapping(use_cache=use_mapping_cache)
profile = mapping.profile if mapping else None

inventory = discover_tables(engine, exclude=..., exclude_prefixes=..., profile=profile)
tables = [_describe_table(engine, inspector, t, mapping=mapping, inventory=inventory)
          for t in inventory.business_tables]
relationships = _relationships(inventory, profile)

return {
    "schema_version": "1.1",
    "database_type": engine.dialect.name,
    "mapping_profile": profile.profile if profile else "",
    "mapping_source": str(profile.source_path) if profile and profile.source_path else "",
    "tables": tables,
    "relationships": relationships,
    "field_map": _field_map(mapping, inventory),
    "metric_bindings": list(mapping.metrics) if mapping else [],
    "unmatched_metrics": list(mapping.unmatched_metrics) if mapping else [],
    "inventory": { "all_tables": ..., "business_tables": ..., "excluded": ..., "roles": ... },
}
```

**向后兼容策略：只增不改**。`tables` / `relationships` / `sample_rows` 的结构与含义完全不变，
新增的四个字段（`field_map` / `metric_bindings` / `unmatched_metrics` / `inventory`）都是附加项。
没有映射时 `field_map = {}`、`metric_bindings = []`，系统行为与本项目原先完全相同。

### `field_map` 的结构

```python
# metadata/metadata_service.py:217-244
{
  "<表名>": [
    {
      "standard_field": "defect_rate",
      "label": "缺陷率",
      "column": "def_rate",
      "expression": "def_rate * 100",
      "unit": "百分比",
      "customer_unit": "比例(0-1)",
      "scale": 100.0,
      "data_kind": "measure",
      "group": "质量与设备健康",
      "enum_values": [],
      "needs_conversion": True
    }, ...
  ]
}
```

同时每张表的每个列对象里还带了 `standard_fields: [...]`（该列承载哪些标准字段），
由 `_describe_table` 的 `column_to_standard` 反向索引生成（`metadata_service.py:146-172`）。

## 3.6 映射在运行时的四个落点

这是映射体系最关键的一张表——**一份 YAML 通过四条独立通道影响模型的输出**：

| # | 通道 | 代码位置 | 对模型的作用 |
|---|---|---|---|
| 1 | **元数据段的「标准字段口径」块** | `providers.py:130-184` | 「业务词 → 客户列 → 必须写的表达式」三列对照，含单位换算与枚举取值 |
| 2 | **指标段的编译表达式** | `providers.py:323-384` + `compiler._compile_metrics` | 「要算缺陷率就写 `AVG(def_rate * 100)`」，换算已嵌入 |
| 3 | **列的 `description`** | `metadata_service._mapping_description` | 字段说明优先级最高的一层，直接显示在表结构行上 |
| 4 | **RAG 示例改写** | `providers.py:566-628` + `mapping/columns.py` | 把标准口径示例 SQL 翻译成客户列口径，使示例可执行 |

第 4 条是最容易忽略但影响很大的一条，单独展开见下一节。

---

# 第四部分：SQL 改写器（`metadata/mapping/columns.py`）

## 4.1 为什么需要它

RAG 示例库（`rag/examples/sql_examples.json`）与评测参照 SQL 都是按**标准字段**写成的（`AVG(defect_rate)`）。
当数据源是客户表（列名 `def_rate`）时：

- 原样注入 → 模型照着编出客户表里并不存在的列名，SQL 报 `Unknown column`；
- **示例从帮助变成污染源**。

所以注入前必须先翻译。`ColumnRewriter` 同时服务两个场景（`columns.py:9-14`）：

1. **改写 RAG 示例**：注入 Prompt 前翻译成客户列名；
2. **生成客户侧参照 SQL**：评测里把标准参照 SQL 翻译后执行，用来验证映射本身是否语义完整（`scripts/build_customer_cases.py`）。

## 4.2 实现要点一：等长掩码（`mask_literals`）

```python
# metadata/mapping/columns.py:46-113（语义摘要）
def mask_literals(sql: str) -> str:
    """构造与 sql 逐字符等长的标识符掩码。
    字符串字面量内容、反引号标识符内容、注释内容替换为 \x01；
    定界符本身保留，便于识别结构。"""
```

掩码用**逐字符等长**的构造，于是：

```text
掩码上的下标  ==  原文上的下标
   ↓
可以在掩码上定位（哪里是真标识符），在原文上取值（拿到原始大小写）
```

### 一个踩过的坑（写进了代码注释）

**不能**直接用 `tools/sql_guard.strip_sql_literals_and_comments` 的返回值当掩码：
它只清空字面量内容、保留定界符，因此长度会变（`'Product_A'` → `''`），偏移会全部错位。
实测代价（`columns.py:22-25`）：

```text
GROUP BY production_line  →  被改成  GROUP BY produc + 残留 tion_line
                              SQL 直接报 Unknown column
```

### 状态机细节

| 处理对象 | 行为 | 理由 |
|---|---|---|
| `'...'` / `"..."` / `` `...` `` | 内容掩码，含 `\` 转义与双写转义（`''`） | 字面量里的内容不是标识符 |
| `-- ...` / `# ...` | 整行掩码 | 注释里的内容不是标识符 |
| `/* ... */` | 内容掩码 | 同上 |
| **`/*! ... */`** | **不掩码，当代码** | MySQL 会真正执行可执行注释里的语句（`columns.py:106`）——当注释删掉就是一个绕过口子，与 `tools/sql_guard.py` 保持同构 |

## 4.3 实现要点二：命中即跳过

```python
# columns.py:187-198
for match in self._pattern.finditer(mask):
    if mask[match.start()] == HOLE:
        continue          # ← 落在字面量/注释内部，不是真标识符
    token = text[match.start():match.end()]
    expression = self._lookup.get(token.lower())
    if expression is None: continue
    out.append(text[cursor:match.start()]); out.append(expression)
    cursor = match.end(); applied[token] = expression
```

`WHERE remark = 'defect_rate 异常'` 里的 `defect_rate` 不会被改写，因为它在掩码里以 `\x01` 开头。

## 4.4 实现要点三：最长优先 + 词边界

```python
# columns.py:161-174
self._ordered = sorted(merged.items(), key=lambda item: -len(item[0]))   # 长名先试
keys = [key for key, _ in self._ordered if key]
self._pattern = re.compile(
    r"(?<![A-Za-z0-9_$])(?:" + "|".join(re.escape(key) for key in keys) + r")(?![A-Za-z0-9_$])",
    re.IGNORECASE,
)
```

两个细节：

- **正则分支顺序 = 匹配优先级**，所以长名必须排在前面：否则 `def_rate` 会吃掉 `avg_def_rate` 的一部分；
- **`(?<![\w$]) ... (?![\w$])` 词边界**：`def_rate` 不会匹配到 `my_def_rate_x` 的内部。

### 反向映射与歧义保护

```python
# columns.py:153-159
for customer_column, standard_names in (reverse or {}).items():
    names = [str(name) for name in standard_names]
    if len(names) != 1:
        continue                       # ← 歧义列不反向改写
    expression = merged.get(names[0])
    if expression is not None:
        merged.setdefault(str(customer_column), expression)
```

`reverse` 是「客户列名 → [可能的标准字段名]」，**多对多**。
同一个客户列可能对应多个标准字段（`util` 既像设备利用率又像资源利用率），
这种列**不参与反向改写**——宁可不改，也不要静默改错。

## 4.5 RAG 示例改写全流程（`RagExampleProvider._rewrite_for_source`）

```python
# prompt/providers.py:566-628（逐段说明）
# 1) 没有映射 → 原样返回（标准模型下示例本来就是对的）
field_map = context.metadata_json.get("field_map") or {}
if not field_map: return examples

# 2) 选目标表：优先 role == "fact"，否则第一张
target = next((t["table_name"] for t in tables if t.get("role") == "fact"), tables[0]["table_name"])

# 3) 从 field_map[target] 构造替换表
replacements: dict[str, str] = {}      # 标准字段 -> 客户侧表达式（defect_rate -> def_rate * 100）
reverse: dict[str, list[str]] = {}     # 客户列 -> [标准字段]
for item in field_map.get(target) or []:
    standard = item["standard_field"]; expression = item["expression"] or item["column"]
    if standard and expression: replacements[standard] = expression
    if item["column"] and standard: reverse.setdefault(item["column"], []).append(standard)
if not replacements: return examples

rewriter = ColumnRewriter(replacements, reverse=reverse)

# 4) 逐条改写 + 可执行性检查
mapped_standards = set(replacements)
unmapped = {f.name for f in STANDARD_FIELDS} - mapped_standards      # 本表没映射到的标准字段
result = []
for example in examples:
    rewritten = rewriter.rewrite(example["sql"])
    if find_identifiers(rewritten.sql, unmapped):
        # 改写后仍带未映射标准字段 → 该示例在当前数据源上不可执行，直接丢弃
        logger.debug("丢弃与当前数据源不兼容的 RAG 示例：%s", example.get("question"))
        continue
    example = {**example, "sql": rewritten.sql, "rewritten": True} if rewritten.changed else dict(example)
    example["tables"] = target          # 涉及表也改成目标表，避免表名误导
    result.append(example)
```

**关键取舍：改写后仍然引用未映射字段的示例，直接丢弃而不是保留。**
理由写在 docstring 里：*与其给模型一个跑不通的例子，不如不给*——它在客户表上会直接报 `Unknown column`。

`find_identifiers`（`columns.py:204-218`）复用了同一套掩码逻辑，只统计**真实出现**的标识符（不看字面量与注释内部）。

## 4.6 映射语义的零成本验证

`scripts/build_customer_cases.py` 用一句话证明了映射的正确性（`build_customer_cases.py:1-16`）：

> **如果映射完整，那么把标准参照 SQL 按映射翻译后执行，结果集必须与原文完全一致。**

流程：

```python
# build_customer_cases.py:111-120
rewriter = mapping.rewriter(target_table)
for case in cases:
    rewritten = rewriter.rewrite(case.reference_sql)          # 列名改写 + 单位换算
    target_sql = _replace_table(rewritten.sql, source_table, target_table)  # 表名替换
    # 在客户表上执行，与标准表结果做 rows_match 比较（行数/列数/取值，行序无关）
```

输出：`evals/<profile>.cases.jsonl`（供评测使用的客户侧用例集）+ 一份等价性报告。
实测结果（`evals/reports/mes_prod_log_mapping_check.md`）：

```text
用例数：40    结果集等价：40/40（100.0%）    触发列改写的用例：40 条
c01  指标聚合  等价  1/1   defect_rate→def_rate * 100
c09  分组对比  等价  5/5   production_line→line_cd，defect_rate→def_rate * 100
...
```

**这一步是消融评测可信度的前提**：只有先证明映射本身语义等价，
`mapping_on` 与 `mapping_off` 之间的差异才能归因于模型，而不是映射 bug。
（`docs/EVALUATION.md:199-200` 明确写了这条推理。）

---

# 第五部分：映射的接入流程（CLI）

`metadata/mapping/cli.py` 把接入拆成五步，核心思想是：**映射不能只有「生成」，还必须有人签字确认。**

```text
python -m metadata.mapping.cli discover --json
python -m metadata.mapping.cli draft  --table <表名> --out data/draft.yaml --no-llm
python -m metadata.mapping.cli validate --mapping mappings/<客户>.mapping.yaml --against-db
python -m metadata.mapping.cli review   --mapping mappings/<客户>.mapping.yaml --against-db
python -m metadata.mapping.cli diff     --old mappings/a.yaml --new mappings/b.yaml
```

## 5.1 `discover`：先看库里有什么

```python
# cli.py:164-231
inventory = discover_tables(engine, profile=profile)
# 逐表打印：[可见/已排除] 表名  列数 角色 主键
#       已排除的额外打印「排除原因：...」
```

**为什么第一步是 discover**：表范围错了，后面全错。而且这是最便宜的一步——
`discover` 输出的 `excluded` 账本会明确告诉工程师「为什么看不到这张表」（命中哪个前缀 / 在排除清单中 / include 声明了但库里不存在）。

## 5.2 `draft`：自动草拟（规则 + 可选大模型）

### 两个来源，各司其职（`draft.py:11-23`）

| 来源 | 能做什么 | 局限 |
|---|---|---|
| **规则匹配** `match_columns` | 列名与标准字段的候选名/别名归一化比对，不需要网络、不会编造 | 只认名字，`util` 这种歧义列会标 `ambiguous` |
| **大模型** | 看每列的**取值画像**（样例值、取值范围、枚举取值）判断语义 | 缺上下文时爱「补全」出看起来合理但错误的绑定 |

### 取值画像（`ColumnProfile`）

```python
# metadata/mapping/draft.py:95-135 + 249-319
@dataclass
class ColumnProfile:
    table: str; column: str; data_type: str; nullable: bool; is_primary_key: bool
    comment: str
    sample_values: list[Any]        # 去重取样（重复值会占满额度、掩盖真实分布）
    distinct_count: int | None      # 近似去重值数，取不到为 None（不伪造 0）
    min_value: Any; max_value: Any; numeric: bool
```

三个刻意的工程取舍：

| 取舍 | 实现 | 理由 |
|---|---|---|
| 不写 `COUNT(DISTINCT col)` | 按 200 行采样（`draft.py:225-246`） | 客户库可能是千万行宽表，精确去重会拖垮接入流程；草稿只需要「像枚举还是像连续度量」这个量级判断 |
| 单列失败只影响那一列 | 每步独立 `try/except`（`draft.py:276-317`） | 草稿质量可以打折，但接入流程不能因为一列而中断 |
| 日期/Decimal 转字符串 | `_to_jsonable`（`draft.py:172-188`） | 进不了 JSON，且数值化会丢掉「这是日期型维度」这一关键信息 |

### 草稿 Prompt 的结构（`build_draft_prompt`，`draft.py:392-453`）

```text
## 一、标准字段词典（只能使用下列 name，不得自造）
[主键与维度]
- record_id（记录编号）单位：—；类型：key；业务别名：—；见过的客户列名：record_id、rec_no、id
[环境与设备传感器]
- ambient_temperature（环境温度）单位：摄氏度；类型：measure；业务别名：—；见过的客户列名：...
...

## 二、客户库结构与取值画像
数据库类型：mysql
### 表 mes_prod_log
- `def_rate` DOUBLE；非空；无注释；去重值数≈4987；样例：0.039、0.012、0.051；范围：0.0 ~ 0.3
- `sft` VARCHAR(16)；非空；无注释；去重值数≈3；样例：'Morning'、'Afternoon'、'Night'
...

## 三、输出要求
1. 只输出一个 JSON 对象，不要任何解释文字、不要 Markdown 代码围栏。
2. JSON 结构如下（键名必须完全一致）：{...}
3. 没有标准字段可对应的列，直接省略 standard_fields 或整条 columns 项，不要硬塞一个相近但语义不同的标准字段。
4. 当客户列的存储单位与标准口径不同时，必须同时给出 scale 与 expression，并在 notes 里说明。
   示例：客户列 def_rate 存的是比例 0.039 ... 写 {"name": "defect_rate", "unit": "比例(0-1)",
   "scale": 100, "expression": "def_rate * 100", "notes": "客户列存 0.039，标准口径为 3.9"}。
   漏掉这一步会让所有指标静默差 100 倍，务必逐列检查。
5. 单位一致时 expression 写裸列名，不要加多余函数包装。
6. 拿不准的列：宁可省略 standard_fields（留给人审），也不要猜。
7. 每个标准字段在同一张表内只能绑定一次。
```

**为什么整段 Prompt 用 `join` 拼接而不用 `str.format()`**（`draft.py:396-399`）：
列名、注释、样例值都来自客户数据库，任何一个花括号都会让 `format` 抛 `KeyError` 或把内容吃掉。
这是本项目的硬约定：**模型/外部数据不参与格式化**（与第二部分 §2.1 是同一个道理）。

### 容错 JSON 解析（`parse_llm_json`）

模型极爱输出 ` ```json ... ``` ` 或「先说一段废话再给 JSON」，所以解析做了三步容错（`draft.py:459-547`）：

1. **剥 Markdown 围栏**：按行状态机推进，围栏行丢弃、围栏内保留；围栏不对称时退化为「丢掉所有围栏行」；
2. **括号配平扫描**：从第一个 `{` 开始做深度配平，扫描时跳过字符串内部与转义字符——比朴素地「取第一个 `{` 到最后一个 `}`」可靠（后者在模型输出两段 JSON 时会拼出非法串，且报错位置失真）；
3. **仍然失败就抛 `MappingError`，并把原文片段带进报错信息**——接入失败时人最需要看到的就是「模型到底返回了什么」。

### 合并：规则赢在哪里（`merge_llm_into_profile`，`draft.py:625-796`）

三条硬规则：

| # | 规则 | 实现 | 理由 |
|---|---|---|---|
| 1 | **规则唯一命中优先** | 列已有规则绑定且模型提议同一标准字段 → 补齐字段；提议另一个 → 丢弃并记 issue | 规则唯一命中时错判概率很低，模型爱「补全」 |
| 2 | **不存在的标准字段一律丢弃** | `if standard not in standard_names: issues.append(...); continue` | 写进 YAML 会让 `validate_profile` 失败，更糟的是有人为「让它过」去改词典 |
| 3 | **发现/排除声明保持不动** | `merge` 只改 `tables`，`discovery` / `relationships` / `database` 原样保留 | 模型看不到库里有几张表，最容易把 discovery 写坏；一旦写坏，Agent 要么看不到目标表，要么在等价表之间随机挑 |

其余保护：

- **客户列以扫库结果为准**：模型写错的列名不进草稿（否则 `validate` 会失败）；
- **模型凭空多给的表**：只在它确实带了列时补一张，且**角色收紧为 `other`**，避免随手写的 `fact` 把指标口径落到空表上；
- **角色不由模型改**：只记一条 info（「大模型认为该表角色是 X，草稿保留 Y」），因为角色影响指标口径落在哪张表上；
- **主键必须真实存在**：模型给的主键不在列里则忽略并记 warning。

**草稿带上不可忽视的标记**：

```python
# metadata/mapping/draft.py:88-89
DRAFT_HEADER = "# DRAFT — 未经人工审核，请勿直接投入生产"
```

文件头还附三段审核要点（`_meta_header`，`draft.py:1226-1239`）：

```yaml
# 由 metadata.mapping.draft 自动生成，profile=...
# 覆盖表：mes_prod_log
# 审核要点：
#   1. 逐列确认标准字段绑定是否正确（规则命中与模型提议都可能有错）；
#   2. 逐条确认 scale / expression 代表的单位换算（漏一条就整体差一个倍数）；
#   3. 确认 discovery.exclude 没有把需要暴露的表挡在门外。
# 审核后请删除本文件头，并把文件改名去掉 draft 字样。
```

理由（`draft.py:1200-1205`）：*一份没有标记的草稿被误当成生效映射，后果是未经人审的单位换算直接进生产 SQL——这是最贵的一类错误。*

### 失败开放

```python
# metadata/mapping/draft.py:1056-1057（docstring）
模型调用失败**不**让草稿失败：把异常记进 DraftResult.llm_error，退回规则草稿。
接入工程师拿到的不该是一次崩溃，而是「一份能用的兜底 + 一条原因」。
```

覆盖三种失败：拿不到模型实例（`draft.py:1094-1097`）、调用异常（`1101-1104`）、返回内容无法解析（`1107-1112`）。
`use_llm=False` 时**完全不碰模型**（离线、无 Key、CI 都走这条路）。

## 5.3 `validate`：结构与存在性

```python
# cli.py:290-330
issues = validate_profile(profile, live_columns=live)      # --against-db 时传真实列名
if inventory is not None:
    issues.extend(_against_db_issues(profile, inventory))
```

一个容易忽略的细节（`cli.py:111-118`）：

```python
def _live_columns(inventory: TableInventory) -> dict[str, list[str]]:
    """注意：映射声明的表**被 discovery 排除了**时也要给出它的列名 —— 否则
    validate_profile 会谎报「表在库里不存在」，而真实原因是排除规则，
    两者的修法完全不同（一个改列名，一个改 discovery.exclude）。"""
    return {table: list(columns) for table, columns in inventory.columns.items()}
```

## 5.4 `review`：把有风险的部分挑出来给人看

这是整个 CLI 的核心。`cli.py:17-28` 明确说明了它针对的三类风险：

| 风险 | 为什么危险 | review 怎么做 |
|---|---|---|
| **单位换算** | 客户列存 `0.039`、标准口径是 `3.9`，漏了 `scale: 100` 会让「平均缺陷率」差 100 倍，而结果集**任何数值都合法**，人一眼看不出错 | 把所有含 `scale`/`expression` 的列**逐条**列出，并提示「若取样值明显小于标准口径的数量级，就是漏了换算」 |
| **未映射列** | 客户表里 12 列没有标准口径是正常的，但如果 **Agent 常问的字段**恰好没被认领，模型会自己编列名 | 输出未映射列清单 + 规则侧的歧义/未命中账本（交叉验证） |
| **歧义匹配** | `util` 同时像「设备利用率」和「资源利用率」，规则不猜 | 必须把它顶到人面前，并显示「映射里选了 X / 仍是空缺」 |

输出四个部分（`cli.py:472-560`）：

```text
一、逐表覆盖率（每列的映射情况）
[mes_prod_log] 角色 fact（事实表）  列 45/45 已映射（100.0%）  标准字段 45 个
    grain：一行 = 一次生产运行（rec_no 唯一）
    主键：rec_no    时间列：—
    未映射列：无

二、本数据源缺失的标准字段（涉及这些字段的问题应回答「数据不足」）
[mes_prod_log] 缺 0/45 个标准字段

三、单位换算逐条确认（口径 ≠ 裸列名，写错会静默差一个倍数）
[mes_prod_log] def_rate -> defect_rate
    客户列单位：比例(0-1)    标准口径单位：百分比
    换算系数  ：100.0
    SQL 写法  ：def_rate * 100
    备注      ：客户列存 0.039，标准口径为 3.9；SQL 必须写成 def_rate * 100
    → 请核对该列真实取值：若取样值明显小于标准口径的数量级，就是漏了换算。
[mes_prod_log] util -> machine_utilization
    ...

四、列名规则的匹配账本（规则看不到取值，仅供交叉验证）
[mes_prod_log] 规则命中 43 列，歧义 1 列，未命中 1 列
    ? util：候选 machine_utilization、resource_utilization，映射里选了 machine_utilization
```

加 `--json` 可输出机器可读版本（`_review_payload`，`cli.py:357-430`），供 CI 流水线消费：

```json
{
  "mapping": "mappings/mes_prod_log.mapping.yaml",
  "profile": "mes_prod_log",
  "tables": [{ "name": "...", "role": "fact", "conversions": [...],
               "ambiguous_columns": [...], "unmapped_rule_columns": [...],
               "standard_fields_missing": [...] }],
  "issues": [{ "level": "warning", "where": "...", "message": "..." }],
  "summary": { "tables": 1, "conversions": 2, "errors": 0, "warnings": 3 }
}
```

## 5.5 `diff`：改了一版映射后看清动了什么

逐绑定对比新旧映射（`cli.py:596-713`），输出「新增 / 删除 / 变更」的绑定差异。目的是让映射的 Code Review 变成一件低成本的日常动作——**映射是代码，就该有 diff**。

## 5.6 排障细节：Windows 控制台编码

```python
# cli.py:81-90
def _configure_stdout() -> None:
    """Windows 控制台可能是 GBK：遇到无法编码的字符时替换而不是直接崩溃。
    本 CLI 的输出全是中文 + 客户列名/样例值... 缺了这一步，接入工程师看到的是
    UnicodeEncodeError 而不是报告。"""
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError): pass
```

---

# 第六部分：三者如何协同（端到端时序）

以「各产线的平均缺陷率」在客户库 `mes_prod_log` 上为例（`ANALYSIS_MAPPING=mappings/mes_prod_log.mapping.yaml`）：

```text
① 启动：Text2SQLAgent.__init__
   get_metadata_json()
     ├─ resolve_mapping()            → 加载 + 校验 + 编译 mapping.yaml（lru_cache）
     ├─ discover_tables(profile=...) → business_tables = ["mes_prod_log"]
     │                                 （fact_production_record / intelligent_production_iiot 被 exclude）
     ├─ _describe_table(...)         → 45 列，每列带 standard_fields 与映射口径描述
     └─ _field_map() / metric_bindings
   → business_tables 锁进 ReadOnlySQLDatabase(include_tables=["mes_prod_log"])
   → resolve_knowledge(metadata)     解析主题/对象/规则到真实表字段

② 提问：ask("各产线的平均缺陷率")
   build_context(question)
     ├─ extract_keywords("各产线的平均缺陷率")
     │    → {"产线", "产线的", "缺陷", "缺陷率", ...}（去掉「各」「的」「平均」等停用词）
     ├─ DataResourceProvider  → 表相关内容 + 标准字段口径块 + 「单表模型不要 JOIN」尾块
     ├─ MetricsProvider       → 缺陷率 → mes_prod_log.def_rate → AVG(def_rate * 100)
     ├─ KnowledgeProvider     → 质量分析主题等条目（按相关性排序）
     └─ RagExampleProvider    → 检索示例 → ColumnRewriter 改写 → defect_rate→def_rate * 100
   → context_block 注入 system prompt 的 {context_block}
   → result.prompt_usage 记录各段用量

③ Agent 循环（LangChain tool-calling）
   sql_db_list_tables  → 只看到 mes_prod_log（白名单生效）
   sql_db_schema       → 确认 rec_no / line_cd / def_rate 等真实列名与样例值
   sql_db_query        → SELECT line_cd, AVG(def_rate * 100) AS 平均缺陷率
                         FROM mes_prod_log GROUP BY line_cd LIMIT 50
     └─ ReadOnlySQLDatabase.run → validate_readonly_sql（只读守卫）

④ 结果回放：_extract_sql_from_steps → execute_sql(sql) → (columns, rows)
   失败只记 sql_error，不影响已生成的分析结论

⑤ 输出：AgentResult(
     sql, columns, rows, analysis_text,
     prompt_usage={"total_budget":8000,"used_chars":6421,"sections":[...]},
     rag_context="..."（RAG 段文本，前端「分析过程」展示）
   )
```

**贯穿全链的关键点**：模型看到的列名（`def_rate`）与业务词（「缺陷率」）之间的对应关系，
**只在 Prompt 里说明一次**（元数据段的字段映射块 + 指标段），模型照抄即可。
系统没有在 SQL 生成后做「再翻译」——因为那样就变成了「猜 + 修补」，
而现在的做法是「把口径讲清楚，一次写对」。

## 6.1 配置开关一览

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `ANALYSIS_MAPPING` | 空 | mapping.yaml 路径；空 / `none` / `off` / `false` / `0` → 不使用映射，退回单表标准模型 |
| `DISCOVERY_EXCLUDE_TABLES` | 空 | 跨客户通用的排除表名（逗号分隔） |
| `DISCOVERY_EXCLUDE_PREFIXES` | 空 | 跨客户通用的排除前缀 |
| `PROMPT_TOTAL_BUDGET` | 8000 | 四段合计上限，超出后按段优先级回收 |
| `PROMPT_METADATA_BUDGET` | 3000 | 元数据段预算 |
| `PROMPT_METRICS_BUDGET` | 1500 | 指标段预算 |
| `PROMPT_KNOWLEDGE_BUDGET` | 1800 | 知识段预算 |
| `PROMPT_RAG_BUDGET` | 1500 | RAG 段预算 |
| `PROMPT_FIELD_MAP_BUDGET` | 900 | 元数据段内「标准字段口径」子块的预算 |
| `PROMPT_METADATA_MIN_TABLES` | 3 | 元数据段至少展示几张表 |
| `PROMPT_METADATA_SAMPLE_TABLES` | 2 | 附样例值的表数 |
| `SQL_MAX_COLUMNS_PER_TABLE` | 25 | 单表最多展示字段数 |
| `RAG_ENABLED` | true | 关掉 = 评测里的 `rag_off` |
| `PROMPT_METRICS_ENABLED` | true | 关掉 = 评测里的 `metrics_off` |
| `RAG_TOP_K` | 3 | 检索示例条数 |
| `RAG_MIN_SCORE` | 0.45 | 示例相似度下限（COSINE） |

消融评测对映射维度的三态控制（`evals/runner.py:93-138`）：

```python
class EvalConfig:
    """mapping 为三态：
       True  → 显式启用字段映射（名字里带 +mapping_on）
       False → 显式关闭（+mapping_off）
       None  → 不参与映射维度，沿用环境里 ANALYSIS_MAPPING
       三态是必要的：原本的四配置矩阵不应该因为新增了映射维度就改名，
       否则既有报告与历史数字无法对照。"""
```

并且元数据**由评测工厂显式注入**，而不是让 Agent 自己去读（`runner.py:211-225`）：

```python
"""元数据由本工厂显式注入，而不是让 Agent 自己去读 —— 这样 mapping_on 与
mapping_off 两个配置的差异严格等于映射本身，不会混进缓存或环境变量的时序问题。"""
return Text2SQLAgent(metadata_json=build_agent_metadata(config),
                     prompt_builder=build_prompt_builder(config), ...)
```

## 6.2 映射到接口层

| 接口 | 内容 | 代码 |
|---|---|---|
| `GET /api/v1/metadata/tables` | 表/字段/类型/说明/样例值 + `field_map` + `metric_bindings` + `inventory` | `server/routes.py` → `metadataService` |
| `GET /api/v1/metadata/field-map` | 标准字段口径映射（含单位换算与枚举取值） | `server/routes.py:84-93` |
| `GET /api/v1/metadata/relationships` | 表间关系（单表模型下通常为空） | `server/routes.py` |
| `POST /api/v1/agent/ask/async` + `GET .../stream` | 问答，返回 `sql` / `columns` / `rows` / `analysis_text` / `prompt_usage` / `rag_context` / `metric_bindings` | `server/schemas.py:379-387` |

`prompt_usage` 与 `rag_context` 直接来自 `AgentResult`（`server/schemas.py:116-117, 379-380`），
被组装成前端的「分析过程」步骤（`server/deps.py:218-269`）：

```python
if result.rag_context:     步骤「检索相似示例（RAG）」    kind="prompt"
if result.metric_bindings: 步骤「对齐业务指标口径」        kind="prompt"
if result.sql:             步骤「生成并执行只读 SQL」      kind="sql"
if result.columns:         步骤「获取查询结果」            kind="result"
if result.analysis_text:   步骤「生成分析结论」            kind="report"
```

指标绑定还有一个额外用途——从最终 SQL 里反查本次用到了哪些指标口径（`server/schemas.py:405-439`）：

```python
def _match_metric_bindings(sql):
    """匹配策略（按可靠性排序）：
       1. 标准字段口径表达式（AVG(defect_rate)）—— 客户库列名与业务词不一致时模型写的是表达式；
       2. 客户真实列名（defect_rate）；
       3. 中文指标名（缺陷率）—— 少数场景下模型会在注释里写出指标名。
       对短列名（如 id）做长度保护，避免子串误命中。"""
```

前端消费现状（`frontend/src/`）：

| 入口 | 消费的接口 | 展示内容 |
|---|---|---|
| `MetadataPage.tsx:23-24` | `/metadata/tables`、`/metadata/relationships` | 表 / 字段 / 类型 / 说明 / 样例值 / 行数 |
| `DashboardPage.tsx:63` | `/metadata/tables` | 概览统计 |
| `ChatPage.tsx:380-386` | `prompt_usage`（来自问答响应） | 「PROMPT 预算用量 · used/total 字符」+ 逐段用量与丢弃数 |
| `api/client.ts:129-135` | `/metadata/field-map` | 已封装 `metadataFieldMap()`，**当前页面尚未消费**（字段映射目前主要在 Prompt 侧生效） |

Java 网关（`backend/agent-server`）原样透传这些字段：`MetadataController` 提供 `/metadata/tables`
与 `/metadata/field-map`（`MetadataController.java:40, 66`），`AskResponseVO` 带
`ragContext` / `promptUsage`（`AskResponseVO.java:78, 81`），响应统一信封 `{code, msg, data}`。

---

# 第七部分：设计约束清单（写代码时必须遵守）

按重要性排序，这些是整套机制的「不变式」，改动时最容易踩：

1. **动态内容不进 `str.format()`**。业务文本（表结构、字段说明、样例值、知识条目、客户列名）一律通过
   `{context_block}` 变量注入，或在草稿 Prompt 里用 `join` 拼接。任何一处退回 `format()`，
   客户库里的一个花括号就会让整次请求失败。
2. **空白名单必须显式失败**。`include_tables` 绝不能写 `business_tables or None`。
3. **客户特有的排除项写在 `mapping.yaml`，不写在 `core.config`**。
4. **裁剪必须按相关性丢弃，不能从中间截断**。统一走 `pack_blocks`。
5. **失败开放**。Provider 单段失败 → 空段；`build_context` 失败 → 空上下文；检索失败 → 空段；
   模型草拟失败 → 规则草稿。任何一处都不许把整次问答打断。
6. **空段不占位**。`PromptSection.render()` 在内容为空时返回空串，这是消融开关能干净生效的前提。
7. **映射编译产物只增不改地并入 metadata JSON**。`tables` / `relationships` / `sample_rows` 结构保持稳定。
8. **改写标识符必须用等长掩码**，不能复用会改变长度的 `strip_sql_literals_and_comments`。
9. **歧义不猜**。`match_columns` 只认唯一命中；`ColumnRewriter` 的多对多客户列不反向改写。
10. **模型无权修改 `discovery` / `relationships` / 表角色**。合并只允许往空位补信息。
11. **草稿必须自报未审核**（`# DRAFT` 文件头）。
12. **"没有这个字段"要说出来**，而不是留空——`unmatched_metrics`、缺失标准字段、
    「当前数据源缺少该指标字段，不要臆造」都是这条原则的实例。

---

# 附录 A：关键文件索引

| 主题 | 文件 | 关键符号 |
|---|---|---|
| 表发现与排除 | `metadata/inventory.py` | `discover_tables`、`TableInventory`、`DEFAULT_EXCLUDE_PREFIXES`、`ROLE_LABELS` |
| 元数据汇合 | `metadata/metadata_service.py` | `get_metadata_json`、`_describe_table`、`_field_map`、`_relationships` |
| 标准字段词典 | `metadata/standard_fields.py` | `STANDARD_FIELDS`(45)、`STANDARD_METRICS`(7)、`match_columns`、`normalize_identifier` |
| 映射契约 | `metadata/mapping/schema.py` | `MappingProfile`、`parse_profile`、`validate_profile`、`mapping_coverage` |
| 映射编译 | `metadata/mapping/compiler.py` | `compile_profile`、`CompiledMapping`、`ResolvedField`、`_compile_metrics` |
| SQL 改写 | `metadata/mapping/columns.py` | `mask_literals`、`ColumnRewriter`、`find_identifiers` |
| 自动草拟 | `metadata/mapping/draft.py` | `draft_mapping`、`build_draft_prompt`、`parse_llm_json`、`merge_llm_into_profile` |
| 接入 CLI | `metadata/mapping/cli.py` | `_cmd_discover/draft/validate/review/diff` |
| 映射加载入口 | `metadata/mapping/__init__.py` | `resolve_mapping`、`default_mapping_path`、`MAPPING_ENV` |
| Prompt 抽象 | `prompt/base.py` | `PromptSection`、`PromptContext`、`PromptSectionProvider` |
| Prompt 原语 | `prompt/budget.py` | `extract_keywords`、`relevance_score`、`pack_blocks`、`truncate_text` |
| Prompt 四段 | `prompt/providers.py` | `DataResourceProvider`、`MetricsProvider`、`KnowledgeProvider`、`RagExampleProvider` |
| Prompt 组装 | `prompt/builder.py` | `SQLAgentPromptBuilder`、`build_context`、`_shrink_to_total` |
| 静态模板 | `core/prompts.py` | `SQL_AGENT_SYSTEM_TEMPLATE`、`REPORT_SYSTEM_PROMPT` |
| Agent 装配 | `agents/text2sql_agent.py` | `Text2SQLAgent`、`create_readonly_sql_agent`、`_safe_build_context` |
| 指标适配 | `core/metrics.py` | `resolve_metrics`、`resolve_metrics_from_field_map` |
| 知识解析 | `knowledge/knowledge_service.py` | `resolve_knowledge` |
| RAG 检索 | `rag/retriever.py`、`rag/sql_example_store.py` | `search_sql_examples` |
| 只读守卫 | `tools/sql_guard.py`、`tools/sql_database.py` | `validate_readonly_sql`、`ReadOnlySQLDatabase` |
| 映射等价性验证 | `scripts/build_customer_cases.py` | `build_checks`、`_replace_table` |
| 消融评测 | `evals/runner.py`、`evals/report.py` | `EvalConfig`、`build_agent_metadata`、`build_prompt_builder` |
| 接口层 | `server/routes.py`、`server/schemas.py`、`server/deps.py` | `metadata_field_map`、`_match_metric_bindings`、`build_analysis_steps` |

# 附录 B：两份映射的对照（同一机制的两种形态）

| 维度 | `standard_production.mapping.yaml` | `mes_prod_log.mapping.yaml` |
|---|---|---|
| 表名 | `fact_production_record`（标准底座） | `mes_prod_log`（客户 MES 日志） |
| 列名风格 | 列名 = 标准字段名 | MES 缩写（`def_rate` / `stop_min` / `fpy` / `alarm_cnt`） |
| 列注释 | 每列都有 COMMENT | **45 列全无注释** |
| 单位 | 就是标准单位，无换算 | 2 列需换算（`def_rate`、`util` 均 ×100） |
| `discovery.exclude` | `intelligent_production_iiot`、`mes_prod_log` | `intelligent_production_iiot`、`fact_production_record` |
| 用途 | 让「标准模型」也走同一条发现+映射路径（代码里不再有第二条分支）；作为评测 baseline | 客户侧对照实验：**数据没变，只换了命名、注释与单位** |

后者存在的意义正是让「换一张表」的对照实验有干净的因果解释：
两份映射背后的数据逐行对应，差异只有列名与两个列的单位，因此评测分数的差异只能归因于映射层。

# 附录 C：测试覆盖（不需要 MySQL / Milvus / API Key）

| 测试文件 | 覆盖内容 |
|---|---|
| `tests/test_mapping.py`（44 个用例） | 草稿渲染往返、键序稳定、LLM JSON 容错解析、规则匹配歧义、取值画像容错、草稿合并三条硬规则、改写器的字面量/词边界/大小写/幂等、发现模式排除与 include、CLI 五个子命令 |
| `tests/test_prompt_providers.py` | 四段裁剪与预算、相关性排序、总预算回收、失败开放、模板大括号回归 |
| `tests/test_sql_agent_wiring.py` | 端到端装配：四段进入 system prompt、写操作被拦且未落库、只读 SELECT 正常跑完循环 |
| `tests/test_metadata_service.py` | 元数据输出结构与字段说明优先级 |
| `tests/test_route_b_model.py` | 单表标准模型（route B）行为 |
| `tests/test_server_adapter.py` | 接口契约与统一信封 |

```powershell
cd agent
D:\Anaconda\envs\sqllangchain\python.exe -m pytest tests -q
```
