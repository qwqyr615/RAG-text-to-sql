# AI Agent 模块

面向企业数据底座智能问析系统的 Python Agent 工程目录。

## 目录结构

```text
agent/
├── agents/                  # Agent 层：各类智能体
│   ├── enterprise_agent.py  # 总 Agent：问题路由
│   ├── text2sql_agent.py    # Text-to-SQL 核心 Agent（LangChain SQL Agent + 只读守卫）
│   └── report_agent.py      # 报告生成
├── core/                    # 核心配置与通用能力
│   ├── config.py            # 从 .env 读取全局配置（含 Prompt 预算与循环上限）
│   ├── metrics.py           # 业务指标口径
│   ├── llm.py               # 大模型实例工厂
│   └── prompts.py           # Prompt 统一管理（工作流约束 + 报告/解释模板）
├── prompt/                  # 四段 Prompt Provider（各自裁剪与预算）
│   ├── budget.py            # 关键词相关性排序、按预算装块、截断
│   ├── base.py              # PromptSection / PromptContext / Provider 抽象
│   ├── providers.py         # 元数据段 / 指标段 / 知识段 / RAG 段
│   └── builder.py           # 组装成 system prompt 的 {context_block}
├── sql/                     # 建表与装载 SQL（表结构的唯一事实来源）
│   ├── 01_drop_legacy_dim_tables.sql      # 删除历史派生维表
│   ├── 02_create_fact_production_record.sql # 显式 DDL：类型/主键/NOT NULL/COMMENT/索引
│   └── 03_load_fact_production_record.sql   # 幂等装载：TRUNCATE + 显式列清单 + CAST
├── metadata/                # 数据资源理解
│   ├── schema_ddl.py        # 解析 sql/ 下的 DDL，提供「期望结构」
│   ├── preset_metadata.py   # 预置表/字段/关系说明（DDL COMMENT 的兜底）
│   └── metadata_service.py  # 动态读取数据库并输出统一 JSON
├── knowledge/               # 知识模型与映射
│   ├── knowledge_base.py    # 主题/对象/规则定义
│   └── knowledge_service.py # 映射到真实表/字段
├── tools/                   # 工具层
│   ├── database.py          # 数据库连接（可写引擎 / Agent 只读引擎）
│   ├── sql_guard.py         # SQL 只读校验（唯一校验入口）
│   ├── sql_database.py      # 在 SQL 真正执行处做校验的 SQLDatabase
│   ├── sql_executor.py      # 只读结果回放取数
│   └── modeling.py          # 异常检测/回归建模
├── rag/                     # RAG 检索
│   ├── embeddings.py        # SiliconFlow 嵌入模型
│   ├── milvus_store.py      # Milvus 连接与集合管理（含维度自愈）
│   ├── sql_example_store.py # 示例导入/检索
│   ├── retriever.py         # RAG 检索门面
│   └── ingest_examples.py   # 导入问题-SQL示例
├── scripts/                 # 初始化与自检脚本
│   ├── init_preset_schema.py    # 建表 + 装载 + 导出 JSON
│   └── check_schema_health.py   # schema 健康检查（演示前跑）
├── outputs/                 # 导出的 JSON
├── schemas/                 # 输入输出数据结构
├── docs/                    # 设计文档
├── data/                    # 本地数据文件
├── logs/                    # 日志目录
├── tests/                   # 单元测试
├── .env                     # 本地环境变量（不要提交到 Git）
├── .env.example             # 环境变量模板
├── requirements.txt         # Python 依赖
└── main.py                  # 本地验证入口
```

## 快速开始

本项目本地使用 Python 虚拟环境：`sqllangchain`

```bash
cd agent

# 1. 使用已配置好的环境安装/确认依赖
D:\Anaconda\envs\sqllangchain\python.exe -m pip install -r requirements.txt

# 2. 配置 .env
# 已有真实配置时直接检查 DEEPSEEK_API_KEY 和 DATABASE_URL

# 3. 启动命令行验证
D:\Anaconda\envs\sqllangchain\python.exe main.py
```

## 数据底座初始化（单表宽表模型）

数据分两层：原始层 `intelligent_production_iiot`（CSV 导入，只读）+ 服务层
`fact_production_record`（显式 DDL 治理后的宽表，Agent 只查这一张）。
**不再派生 `dim_*` 维表**，选型理由见 `docs/DATA_MODEL.md`。

```powershell
cd agent

# 建表 + 装载 + 刷新 metadata/knowledge JSON（可重复执行）
D:\Anaconda\envs\sqllangchain\python.exe scripts\init_preset_schema.py

# schema 健康检查（9 项，失败时退出码非 0）
D:\Anaconda\envs\sqllangchain\python.exe scripts\check_schema_health.py
```

要改表结构就改 `sql/` 下的 SQL 文件，再重跑上面两步——DDL 是表结构的唯一事实来源。

## RAG 检索

在 SQL Agent 生成 SQL 前，先用 Milvus 检索相似问题-SQL 示例，由
`prompt/providers.py` 的 `RagExampleProvider` 按预算裁剪后注入 system prompt。

```powershell
cd agent
D:\Anaconda\envs\sqllangchain\python.exe -m rag.ingest_examples
D:\Anaconda\envs\sqllangchain\python.exe main.py
```

## 单元测试

```powershell
cd agent
D:\Anaconda\envs\sqllangchain\python.exe -m pytest tests -q
```

测试不需要 MySQL、Milvus 与大模型 API Key：用假元数据、假检索与脚本化假 ChatModel 覆盖
只读守卫、四段 Prompt 裁剪、Agent 装配，以及数据模型（DDL 与装载 SQL 一致性、
`preset_metadata` 对齐、单表模型下的 Prompt 提示）。

## 配置说明

所有大模型相关配置都集中在 `.env` 中，不要在代码中硬编码。

### 基础配置

| 环境变量 | 说明 | 示例 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API Key | `sk-xxx` |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com` |
| `LLM_MODEL` | 模型名 | `deepseek-chat` |
| `LLM_TEMPERATURE` | 温度 | `0.1` |
| `LLM_MAX_TOKENS` | 最大生成 token | `4096` |
| `DATABASE_URL` | 数据库连接 | `mysql+pymysql://...` |
| `SQL_SAMPLE_ROWS` | SQL Agent 展示的样例数据行数 | `3` |
| `LANGSMITH_TRACING` | 是否开启 LangSmith | `false` |

### SQL 安全与 Agent 预算

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DB_READONLY_SESSION` | `true` | 是否把数据库会话设为只读（第二道防线） |
| `SQL_RESULT_ROW_LIMIT` | `200` | 结果回放取数最多返回行数 |
| `SQL_AGENT_MAX_ITERATIONS` | `8` | 单次提问最多工具循环轮数 |
| `SQL_AGENT_MAX_EXECUTION_TIME` | `90` | 单次提问最长执行秒数 |
| `SQL_AGENT_TOP_K` | `50` | 提示模型默认返回行数上限 |
| `SQL_AGENT_VERBOSE` | `true` | 是否打印 Agent 中间步骤 |

### Prompt 上下文预算（字符数）

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PROMPT_TOTAL_BUDGET` | `8000` | 四段合计上限，超出后按段优先级回收 |
| `PROMPT_METADATA_BUDGET` | `3000` | 元数据段（表/字段/样例值/表间关系） |
| `PROMPT_METRICS_BUDGET` | `1500` | 指标段（指标口径→字段） |
| `PROMPT_KNOWLEDGE_BUDGET` | `1800` | 知识段（主题/对象/规则） |
| `PROMPT_RAG_BUDGET` | `1500` | RAG 段（相似问题与 SQL 示例） |
| `PROMPT_METADATA_MIN_TABLES` | `3` | 元数据段至少展示的表数（单表模型下无影响） |
| `PROMPT_METADATA_SAMPLE_TABLES` | `2` | 附样例值的表数 |
| `SQL_MAX_COLUMNS_PER_TABLE` | `25` | 单表最多展示字段数 |
| `RAG_MIN_SCORE` | `0.45` | 示例相似度下限，低于则丢弃 |

设计细节见 `docs/PROMPT_AND_SQL_GUARD.md` 与 `docs/DATA_MODEL.md`。

## 当前实现

- 通过 `core/config.py` 统一读取大模型配置
- 通过 `core/llm.py` 统一创建大模型实例
- 使用 LangChain 官方 `create_sql_agent` 构建 SQL Agent
- Agent 的循环、工具选择、SQL 生成、执行、错误重试由 LangChain 框架完成
- 业务代码不手动实现复杂 Agent 逻辑
- **执行期只读守卫**：`ReadOnlySQLDatabase` 在 SQL 真正执行前校验，违规以工具错误
  回灌给模型；`execute_sql` 回放取数走同一套校验；配合数据库只读会话兜底
- **四段 Prompt Provider**：元数据 / 指标 / 知识 / RAG 示例各自按相关性裁剪、各自持有
  字符预算，通过 system prompt 的 `{context_block}` 注入
- **工作流约束**：system prompt 内置 6 条约束（先确认字段、再对齐口径、单条只读查询、
  控制规模、失败重试、中文结论）
- **循环与工具预算**：`max_iterations` / `max_execution_time` / `top_k` 全部可配
- **单表宽表数据模型**：原始层 + 服务层两张表，显式 DDL（类型/主键/NOT NULL/COMMENT/索引），
  幂等装载（`TRUNCATE` + 显式列清单 + `CAST`），不派生维表
- **字段类型治理**：11 个原本以 `TEXT` 存储的数值列改为数值类型，
  修复 `ORDER BY` / `MIN` / `MAX` 按字典序计算导致答案错误的问题
- **schema 健康检查**：9 项自动校验（DDL 一致性、grain 唯一性、数值排序回归、转换保真等）
- 返回结构化结果：`SQL`、`columns` 列名、`rows` 数据行、`analysis_text` 文字结论、
  `prompt_usage` 各段预算用量
- 结果回放取数失败只记录 `sql_error`，不再丢掉已生成的分析结论
- 已增加业务指标口径：缺陷率、良率/直通率、质量得分、停机时长、故障次数、产量、设备利用率
- 已增加 Markdown 报告生成功能
- 已增加 Isolation Forest 异常检测
- 已增加 LinearRegression 简单回归建模
- 已支持 metadata JSON 导出：`outputs/metadata.json`
- 已支持知识模型映射 JSON 导出：`outputs/knowledge.json`
- 已通过 `EnterpriseAgent` 统一路由：SQL/报告/异常检测/回归

## 本地验证示例

```text
统计不同生产线的平均缺陷率
找出停机时间最长的 10 台设备
找出异常数据
用回归模型预测缺陷率
生成一份质量分析报告
```

## 下一步计划

- [ ] 实现 FastAPI 接口（metadata/knowledge/agent）
- [ ] Java/前端接入
- [ ] 增加图表自动生成（`AgentResult.chart_config` 目前仍是预留字段）
- [ ] 增加更多机器学习模型，例如 KMeans、决策树、随机森林
- [ ] 把成功执行的 question/SQL 自动回写 Milvus，形成在线学习闭环
