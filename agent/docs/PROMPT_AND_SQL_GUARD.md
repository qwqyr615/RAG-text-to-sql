# Prompt 组装与 SQL 守卫设计

这套机制解决两个具体问题：

1. **Prompt 一把梭**：原来在 `Text2SQLAgent.__init__` 里用
   `SQL_AGENT_PREFIX.format(data_resources, knowledge_summary, metrics, business_rules)`
   把所有上下文一次性拼进 system prompt，既没有预算控制，业务文本里出现 `{` 还会
   直接 `KeyError`。
2. **只读校验没挂在执行路径上**：`validate_readonly_sql` 只在 `execute_sql`
   （结果回放取数）里被调用，而模型真正执行 SQL 走的是 LangChain 的
   `sql_db_query` 工具，写操作会直接落库。

---

## 一、SQL 只读守卫（两层）

```text
模型生成 SQL
  │
  ├─ 路径 A：LangChain 工具调用（真正执行）
  │    sql_db_query → QuerySQLDatabaseTool._run
  │      → SQLDatabase.run_no_throw → ReadOnlySQLDatabase.run
  │        → validate_readonly_sql()          ← 第 1 层：SQL 文本校验
  │          → 通过：真的查库
  │          → 违规：抛 ReadOnlyViolation(SQLAlchemyError)
  │                   → run_no_throw 转成 "Error: ..." 观测结果回灌给模型
  │                     → 模型据此重写查询（system prompt 工作流约束第 5 条）
  │
  └─ 路径 B：结果回放取数（Agent 跑完后取结构化结果）
       execute_sql → validate_readonly_sql()
         → 通过：用只读引擎取 (列名, 数据行)
         → 违规/失败：写入 AgentResult.sql_error，不影响已生成的分析结论

数据库会话层（第 2 层，DB_READONLY_SESSION=true 时生效）
  MySQL:      SET SESSION TRANSACTION READ ONLY
  PostgreSQL: SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY
  SQLite:     PRAGMA query_only = ON
```

### 关键设计点

| 决策 | 原因 |
|---|---|
| 守卫放在 `ReadOnlySQLDatabase.run/_execute` | 这是 LangChain 所有 SQL 执行的必经之路（`sql_db_query`、`sql_db_schema` 取样例数据都走这里）；只改 `execute_sql` 拦不住模型 |
| `ReadOnlyViolation` 继承 `SQLAlchemyError` | `run_no_throw` 只捕获 `SQLAlchemyError`。若抛普通 `ValueError`，违规会穿透工具直接终止整次 Agent 调用，模型失去重写机会 |
| 先剥离字符串字面量与注释再匹配关键字 | 否则 `WHERE remark = 'DELETE 工单'` 会被误判为写操作 |
| MySQL 可执行注释 `/*! ... */` 展开而不是删除 | MySQL 会真正执行其中的语句，当注释删掉就是一个绕过口子 |
| 分号拼接一律拒绝 | 阻断 `SELECT 1; DROP TABLE t` 这类堆叠语句 |
| 表结构白名单为空时直接报错 | `include_tables=business_tables or None` 会在元数据读取失败时退化成「放开全部表」 |
| 只读引擎与可写引擎分开 | `get_engine()` 仍用于初始化脚本与元数据读取；Agent 只用 `get_readonly_engine()` |

### 校验覆盖范围

允许：`SELECT ...`、`WITH ... SELECT ...`（CTE）、带结尾分号的单条查询。

拒绝：`INSERT / UPDATE / DELETE / REPLACE / MERGE / DROP / ALTER / CREATE /
TRUNCATE / RENAME / GRANT / REVOKE / CALL / EXEC`、`SELECT ... INTO OUTFILE`、
`LOAD DATA`、`FOR UPDATE`、`SLEEP()/BENCHMARK()`、多条语句。

---

## 二、四段 Prompt Provider

```text
用户问题
  │
  ▼
SQLAgentPromptBuilder.build_context(PromptContext)
  │
  ├─ DataResourceProvider   priority=90  元数据段：表 / 字段 / 样例值 / 表间关系
  ├─ MetricsProvider        priority=80  指标段：业务指标口径 → 真实字段
  ├─ KnowledgeProvider      priority=70  知识段：分析主题 / 业务对象 / 指标规则
  └─ RagExampleProvider     priority=40  RAG 段：相似问题 + 历史 SQL 示例
  │
  ├─ 每段：抽取关键词 → 按相关性排序 → 在分配预算内装入 → 记录丢弃数量
  ├─ 合计超总预算时：按 priority 从低到高压缩（RAG → 知识 → 指标 → 元数据）
  ▼
PromptBuildResult(context_block, sections, used_chars, total_budget)
  │
  ▼
AgentResult.prompt_usage（前端/日志可见每段用量）
system prompt 的 {context_block}
```

### 每段自己的裁剪策略

| 段 | 预算配置 | 裁剪策略 |
|---|---|---|
| 元数据段 | `PROMPT_METADATA_BUDGET` | 表按相关性排序；至少保留 `PROMPT_METADATA_MIN_TABLES` 张（相关性全 0 时按原顺序补齐）；单表字段超过 `SQL_MAX_COLUMNS_PER_TABLE` 个则折叠并提示用 `sql_db_schema`；样例值只给相关性最高的 `PROMPT_METADATA_SAMPLE_TABLES` 张表；表间关系作为尾块**保证出现**（体积小、价值高） |
| 指标段 | `PROMPT_METRICS_BUDGET` | 只列能在当前数据源匹配到字段的指标，并给出 `表.字段` 定位；匹配不到的指标不列，防止模型臆造列名；按与问题的相关性排序 |
| 知识段 | `PROMPT_KNOWLEDGE_BUDGET` | 主题 / 对象 / 规则压平成一个列表统一按相关性排序，预算紧张时丢的是最不相关的条目，而不是整类知识 |
| RAG 段 | `PROMPT_RAG_BUDGET` | 低于 `RAG_MIN_SCORE` 的示例直接丢弃；超预算时丢弃相似度最低的示例；检索失败（Milvus / 嵌入服务不可用）输出空段，不影响主流程 |

### 预算回收与「按相关性丢弃」

`pack_blocks()` 负责把已经排好序的块装入预算，并在丢弃时追加
`…另有 N 项因预算未展示` 提示（提示本身也计入预算，不会把预算挤爆）。
`keep_last=True` 用于元数据段的表间关系：它一定会出现。

因此**预算不够时丢掉的是最不相关的内容，而不是把文本从中间切断**。

### 为什么不再用 `str.format()`

动态上下文（表结构、业务知识、检索到的 SQL 示例）通过 `{context_block}`
这个 Prompt 变量注入，属于运行时取值，不参与模板解析：

```python
prompt = ChatPromptTemplate.from_messages([("system", SQL_AGENT_SYSTEM_TEMPLATE), ...])
agent.invoke({"input": question, "context_block": build.context_block})
```

业务文本里出现 `{1: 正常, 0: 停用}` 也不会破坏组装（见
`tests/test_prompt_providers.py::test_system_template_renders_context_block_with_braces`）。

---

## 三、System Prompt 与循环预算

静态模板 `core/prompts.SQL_AGENT_SYSTEM_TEMPLATE` 写「怎么做」，包含 6 条工作流约束：

1. 先确认数据：先用 `sql_db_list_tables` / `sql_db_schema` 确认真实字段名，禁止猜列名；
2. 再对齐口径：指标类问题以「业务指标口径」段的字段映射为准；
3. 然后生成查询：单条只读查询，`SELECT` / `WITH` 开头，禁止任何写操作与 DDL；
4. 控制规模：默认带 `LIMIT {top_k}`，不 `SELECT *`；
5. 失败重试：报错后重看表结构重写，最多重试 2 次，仍失败则如实说明；
6. 得出结论：中文回答，先结论后数据，不粘贴结果原文。

循环与工具预算由 `core/config.py` 控制，全部可通过 `.env` 调整：

| 配置 | 默认 | 作用 |
|---|---|---|
| `SQL_AGENT_MAX_ITERATIONS` | 8 | 单次提问最多几轮「工具调用 → 观测 → 再决策」 |
| `SQL_AGENT_MAX_EXECUTION_TIME` | 90 | 单次提问最长执行秒数 |
| `SQL_AGENT_TOP_K` | 50 | 写入 `{top_k}`，提示模型默认返回行数上限 |
| `SQL_AGENT_VERBOSE` | true | 是否打印 Agent 中间步骤 |

---

## 四、测试

```bash
cd agent
D:\Anaconda\envs\sqllangchain\python.exe -m pytest tests -q
```

| 文件 | 覆盖内容 |
|---|---|
| `tests/test_sql_guard.py` | 只读校验：允许/拒绝清单、字面量与注释误报回归、分号拼接与 MySQL 可执行注释绕过 |
| `tests/test_readonly_database.py` | 执行路径守卫：`db.run` / `sql_db_query` 工具拦截、工具错误可恢复、只读会话兜底 |
| `tests/test_prompt_providers.py` | 四段裁剪与预算、相关性排序、总预算回收、失败开放、模板大括号回归 |
| `tests/test_sql_agent_wiring.py` | 端到端装配：四段进入 system prompt、写操作被拦且未落库、只读 SELECT 正常跑完循环 |

全部测试不需要 MySQL、Milvus 与任何大模型 API Key（用脚本化假 ChatModel 代替）。
