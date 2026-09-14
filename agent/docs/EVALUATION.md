# 消融评测方案

> 配合 `scripts/run_eval.py` 生成的报告一起看，可直接用于答辩说明「效果是怎么量化的」。

## 1. 要回答的问题

评测有两条独立的实验轴。

### 轴 1：RAG 段与指标段（原有）

| 问题 | 对比配置 |
|---|---|
| 检索相似问题-SQL 示例，对生成正确率有帮助吗？ | `rag_on+metrics_on` vs `rag_off+metrics_on` |
| 业务指标口径（字段映射 + 计算口径）有帮助吗？ | `metrics_on` vs `metrics_off` |
| 两者一起上会不会互相抵消？ | 四个配置全跑 |

### 轴 2：字段映射（「换一张表」可行性）

| 问题 | 对比配置 |
|---|---|
| 换到一张**列名是客户缩写、45 列全无注释**的表，系统还能用吗？ | `mapping_on` vs `mapping_off`（同表同用例） |
| 映射这一层值多少？ | 上面两者的**结果一致率差值** |
| RAG 示例需要跟着改写吗？ | `rag_on+mapping_on` vs `rag_off+mapping_on` |

## 2. 判定标准：执行结果一致率

不用「SQL 字符串是否相同」这种脆弱标准，而是**把两条 SQL 都真正执行，比较结果集**：

1. 每条用例人工编写一条参照 SQL，它的执行结果就是标准答案；
2. 模型生成的 SQL 也执行一次；
3. 两者结果一致即算正确。

比较规则（`evals/runner.py::rows_match`）：

- **行数必须相同**（能抓住漏写 `LIMIT` 之类的问题）；
- **列数必须相同**（多选/少选列都算错）；
- **行顺序无关**（避免因为 `ORDER BY` 顺序差异误判）；
- **数值按 0.1% 相对容差比较**（`AVG` 等的浮点尾差不算错）。

同时记录三个辅助指标：

- **可执行率**：生成的 SQL 能跑通的比例（衡量字段名/语法正确性）；
- **平均 Prompt 字符**：增益是否值得付出的上下文成本；
- **相似问题 / 新问题**分组一致率（见下）。

## 3. 数据集

### 3.1 标准数据集 `evals/cases.jsonl`（40 条）

| 类别 | 条数 | 示例 |
|---|---|---|
| 指标聚合 | 8 | 平均缺陷率、总停机时长、总产量 |
| 分组对比 | 10 | 各产线缺陷率、各批次产量前十、各产线设备数 |
| 筛选计数 | 6 | 缺陷率 > 5 的记录数、低利用率设备清单、条件占比 |
| TopN | 6 | 停机最长的 10 台设备、质量得分最低的 10 条记录 |
| 多条件 | 5 | Line_A 的平均缺陷率、Product_B 在 Line_C 的良率 |
| 统计极值 | 3 | 最大/最小缺陷率、各产线标准差 |
| 派生综合 | 2 | 单位成本均值、多指标同查 |

每条用例带 `seen_in_rag` 标记：**RAG 示例库里是否存在同型示例**（措辞不同、模式相同）。

- 示例库 `rag/examples/sql_examples.json` 共 24 条，是 23 条用例的同型变体；
- 剩下 17 条是**全新问题**。

这样报告能拆出两列：相似问题一致率（RAG 应在此体现增益）与新问题一致率
（RAG 不该显著拖累）。只有一列总准确率，就无法区分「RAG 真有用」和「RAG 把题背下来了」。

### 3.2 客户数据集 `evals/mes_prod_log.cases.jsonl`（40 条，**自动生成**）

```powershell
python scripts/build_customer_cases.py --mapping mappings/mes_prod_log.mapping.yaml
```

**问题与标准集完全相同**，只有参照 SQL 被映射翻译成客户列口径：

| | 标准集 | 客户集 |
|---|---|---|
| 表 | `fact_production_record` | `mes_prod_log` |
| 列 | `defect_rate` | `def_rate * 100` |
| 列 | `production_line` | `line_cd` |
| 列 | `downtime_minutes` | `stop_min` |

### 3.3 冒烟子集 `evals/smoke.cases.jsonl`（12 条）

```powershell
python scripts/build_smoke_subset.py
```

`--limit 12` 取的是文件前 12 条，会全落在「指标聚合 + 分组对比」，覆盖不到
筛选计数 / TopN / 多条件 / 统计极值 / 派生综合。因此冒烟子集**按类别分层抽样**，
6 个类别各至少 1 条，其中 **8 条含单位换算字段** —— 冒烟的目的正是用最小成本
覆盖所有查询形状，并让映射的价值最容易观察到。

## 4. 关键前提：映射的语义等价性必须先钉死

客户集的参照 SQL 是**自动翻译**来的。如果翻译本身有错（漏映射一列、单位换算写错），
那么评测分低到底该怪模型还是怪映射就说不清了。

所以在跑任何大模型之前，先做一次**零成本**验证：

```powershell
python scripts/build_customer_cases.py --mapping mappings/mes_prod_log.mapping.yaml
```

它对 40 条用例逐条做：翻译 → 在客户表上执行 → 与标准表结果集比较。
输出的等价性报告在 `evals/reports/mes_prod_log_mapping_check.md`。

**当前结果：40/40 等价（100%）**。这带来一个很强的推论：

> 客户集的参照答案与标准集**数值上完全一致**。因此 `mapping_on` 与 baseline 之间、
> 以及 `mapping_on` 与 `mapping_off` 之间的差异，都只能归因于模型，不能归因于映射。

这一步还顺带抓出过两个真实 bug（见本文件第 8 节），成本是零。

## 5. 怎么跑

```powershell
cd agent

# 1) 零成本自检：用例集格式 + 参照 SQL 是否都能执行
python scripts/run_eval.py --validate

# 2) 零成本：映射语义等价性（换表前必做）
python scripts/build_customer_cases.py --mapping mappings/mes_prod_log.mapping.yaml
python scripts/build_smoke_subset.py

# 3) 冒烟：12 条 × 3 配置（约 36 次 Agent 调用）
python scripts/run_eval.py `
  --dataset evals/mes_prod_log.cases.jsonl `
  --mapping mappings/mes_prod_log.mapping.yaml `
  --configs "rag_off+metrics_on+mapping_off,rag_off+metrics_on+mapping_on,rag_on+metrics_on+mapping_on" `
  --tag smoke

# 4) 全量轴 1：40 条 × 4 配置（标准表）
python -m rag.cli ingest        # 先灌示例库，否则 rag_on 与 rag_off 没区别
python scripts/run_eval.py --mapping mappings/standard_production.mapping.yaml --tag baseline

# 5) 全量轴 2：40 条 × 2 配置（客户表，映射开关）
python scripts/run_eval.py `
  --dataset evals/mes_prod_log.cases.jsonl `
  --mapping mappings/mes_prod_log.mapping.yaml `
  --configs "rag_on+metrics_on+mapping_off,rag_on+metrics_on+mapping_on" `
  --tag customer
```

报告打印到控制台并保存为 `evals/reports/eval_YYYYmmdd_HHMMSS[_tag].md`。

**成本估算**：标准轴 40 条 × 4 配置 = 160 次 Agent 调用，每次约 2~3 次大模型请求
（含 `sql_db_schema` 与查询重试），合计约 300~500 次 DeepSeek 调用；RAG 开启时额外
产生 160 次嵌入请求（SiliconFlow）。冒烟档 36 次调用，约 10 分钟。

## 6. 怎么解读

### 轴 1

| 现象 | 含义 | 处理 |
|---|---|---|
| 相似问题一致率提升，新问题持平 | RAG 起作用且无副作用 | 保持阈值与 top_k |
| 新问题一致率下降 | 检索把不相似的示例也塞进了 Prompt | 提高 `RAG_MIN_SCORE`、降低 `RAG_TOP_K` |
| 相似问题一致率没提升 | 示例没被检索到，或检索到了但预算把它裁掉 | 检查 `rag.cli search` 的相似度分数与 `PROMPT_RAG_BUDGET` |
| 指标段提升明显 | 业务口径对齐有价值 | 继续补标准字段词典，让健康检查守住字段存在性 |
| 指标段几乎无提升 | 模型本来就能猜到字段 | 考虑精简指标段，把预算让给元数据段 |
| Prompt 字符明显增加但准确率持平 | 上下文成本不划算 | 下调对应段预算 |

### 轴 2（换表可行性）

| 现象 | 含义 |
|---|---|
| `mapping_on` 一致率接近 baseline | **方案可行**：换一张无注释的客户表，准确率没有实质损失 |
| `mapping_off` 一致率明显低于 `mapping_on` | 映射这一层有真实增益，不是装饰 |
| `mapping_off` 可执行率高但一致率低 | 典型症状：模型猜对了列名，但**漏掉了单位换算**（`def_rate` 没 ×100），结果是整体差 100 倍 —— 不报错，只是算错 |
| `mapping_off` 可执行率也低 | 连列名都没猜对，说明缩写列名确实不可推断 |
| `mapping_on` 里单位换算用例为「结果不符」 | 映射块或指标段的换算指令没被模型采纳，要检查 Prompt 里换算表述是否够显眼 |
| `rag_on` 与 `rag_off` 在客户表上无差别 | RAG 示例改写没生效，或检索没召回同型示例 |

## 7. 口径与局限（写在报告里，避免被追问）

- **单轮评测**：每条用例使用独立会话（`session_max_turns=0`），多轮追问能力不在本表中体现。
- **单表模型**：生成 SQL 与参照 SQL 都在同一张表上，本表衡量「查询构造正确性」，
  不涉及多表 Join 消歧。多表场景的映射与关系声明机制已就位
  （`mapping.yaml` 的 `relationships`），但尚未纳入评测。
- **没有时间维**：当前数据底座没有日期/时间列，因此用例集不含「最近 7 天趋势」这类问题。
  题目示例里的「最近一个月不良数量最高」「本周质量分析」需要在服务层补
  `production_time` 之类字段后才能纳入评测。映射已预留 `time_column` 字段。
- **两套数据源逐行等价**：`mes_prod_log` 与 `fact_production_record` 除两列单位外数值完全相同
  （已全表逐格比对），且 40 条参照 SQL 翻译后结果集 100% 等价。这是本对照实验因果解释的基础，
  但也意味着它**没有检验**「客户数据结构本身不同」的情形（例如粒度不同、需要 JOIN）。
- **参照 SQL 由人工编写**（标准集）或**由映射机械翻译**（客户集）：答案正确性取决于
  两者的质量，因此 `run_eval.py --validate` 与 `build_customer_cases.py` 都会先确认
  每条参照 SQL 都能执行。
- **随机性**：`LLM_TEMPERATURE=0.1` 已较低，但同一配置多次运行仍可能有小幅波动；
  要下结论建议对同一矩阵跑 2~3 次取平均。**冒烟档只有 12 条，单条用例权重 8.3pp，
  只能用来判断方向，不能用来下结论**。

## 8. 这套流程抓到过的真实缺陷（可作为方法论证据）

在**零大模型成本**的映射等价性检查阶段就暴露出来，如果直接跑评测，它们会被
误读成「模型能力不行」：

1. **掩码偏移 bug（SQL 改写器）**：改写器原本复用
   `tools.sql_guard.strip_sql_literals_and_comments` 的返回值当掩码，但那个函数会
   **缩短**字符串字面量（`'Product_A'` → `''`），导致字面量之后的偏移全部错位。
   症状：`GROUP BY production_line` 被改成 `GROUP BY produc` + 残留 `tion_line`，
   运行时报 `Unknown column`。影响 c32 / c35 两条用例。
   修复：改用自带的**逐字符等长掩码**。已加回归测试锁定。

2. **数据源单位差异**：`mes_prod_log` 的 `def_rate` / `util` 存的是比例
   （0.039 / 0.70），标准口径是百分数（3.9 / 70）。若按「列名只是改了名」的假设直接
   翻译，所有涉及这两列的用例（40 条里有 16 条）都会整体差 100 倍且**不报错**。
   修复：映射 schema 增加 `scale` + `expression`，把换算显式写进生成的 SQL，
   并在 Prompt 里专门强调。

这两条都说明：**先把映射钉死，再测模型**是必要的顺序。
