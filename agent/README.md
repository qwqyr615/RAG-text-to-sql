# AI Agent 模块

面向企业数据底座智能问析系统的 Python Agent 工程目录。

## 目录结构

```text
agent/
├── agents/                  # Agent 层：各类智能体
│   ├── enterprise_agent.py  # 总 Agent：问题路由
│   ├── text2sql_agent.py    # Text-to-SQL 核心 Agent
│   └── report_agent.py      # 报告生成
├── core/                    # 核心配置与通用能力
│   ├── config.py            # 从 .env 读取全局配置
│   ├── metrics.py           # 业务指标口径
│   ├── llm.py               # 大模型实例工厂
│   └── prompts.py           # Prompt 统一管理
├── metadata/                # 数据资源理解
│   ├── preset_metadata.py   # 预置表/字段/关系说明
│   ├── metadata_service.py  # 动态读取 MySQL 并输出 JSON
│   └── prompt_formatter.py  # metadata JSON → Agent Prompt 文本
├── knowledge/               # 知识模型与映射
│   ├── knowledge_base.py    # 主题/对象/规则定义
│   └── knowledge_service.py # 映射到真实表/字段
├── tools/                   # 工具层
│   ├── database.py          # 数据库连接
│   ├── modeling.py          # 异常检测/回归建模
│   └── sql_executor.py      # 只读 SQL 执行器
├── scripts/                 # 初始化脚本
│   └── init_preset_schema.py
├── outputs/                 # 导出的 JSON
├── schemas/                 # 输入输出数据结构
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

## 配置说明

所有大模型相关配置都集中在 `.env` 中，不要在代码中硬编码。

主要配置项：

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

## 当前实现

- 通过 `core/config.py` 统一读取大模型配置
- 通过 `core/llm.py` 统一创建大模型实例
- 使用 LangChain 官方 `create_sql_agent` 构建 SQL Agent
- Agent 的循环、工具选择、SQL 生成、执行、错误重试由 LangChain 框架完成
- 业务代码不手动实现复杂 Agent 逻辑
- 返回结构化结果：`SQL`、`columns` 列名、`rows` 数据行、`analysis_text` 文字结论
- 已增加业务指标口径：缺陷率、良率/直通率、质量得分、停机时长、故障次数、产量
- 已增加 Markdown 报告生成功能
- 已增加 Isolation Forest 异常检测
- 已增加 LinearRegression 简单回归建模
- 已建立多表预置底座：`dim_line`、`dim_machine`、`dim_product`、`dim_batch`、`fact_production_record`
- 已支持 metadata JSON 导出：`outputs/metadata.json`
- 已支持知识模型映射 JSON 导出：`outputs/knowledge.json`
- 已通过 `EnterpriseAgent` 统一路由：SQL/报告/异常检测/回归

## 本地验证示例

```text
统计不同生产线的平均缺陷率
找出异常数据
用回归模型预测缺陷率
生成一份质量分析报告
```

## 下一步计划

- [ ] 实现 FastAPI 接口（metadata/knowledge/agent）
- [ ] Java/前端接入
- [ ] 增加图表自动生成
- [ ] 增加更多机器学习模型，例如 KMeans、决策树、随机森林
