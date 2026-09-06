# AI Agent 模块

面向企业数据底座智能问析系统的 Python Agent 工程目录。

## 目录结构

```text
agent/
├── agents/                  # Agent 层：各类智能体
│   ├── text2sql_agent.py    # Text-to-SQL 核心 Agent
├── core/                    # 核心配置与通用能力
│   ├── config.py            # 从 .env 读取全局配置
│   ├── llm.py               # 大模型实例工厂
│   └── prompts.py           # Prompt 统一管理
├── tools/                   # 工具层：数据库、SQL 执行、元数据读取
│   ├── database.py          # 数据库连接
│   ├── metadata.py          # 数据库元数据读取
│   └── sql_executor.py      # 只读 SQL 执行器
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

## 下一步计划

- [ ] 加载项目根目录 `data/Intelligent_Production_IIoT.csv` 到数据库
- [ ] 补充表结构、字段说明、样例值元数据
- [ ] 补充业务指标口径，例如良率、不良率、停机时长
- [ ] 优化 Text-to-SQL Prompt
- [ ] 增加结果自动纠错
- [ ] 增加图表生成
- [ ] 增加 Python 代码执行和建模能力
