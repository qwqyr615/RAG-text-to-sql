# Text-to-SQL 评测报告：RAG / 指标段 / 字段映射的消融对照

- 生成时间：2026-09-14 20:19:36
- 配置数：1
- 用例集：`cases.jsonl`
- 字段映射：`mappings/standard_production.mapping.yaml`
- 用例总数：40
- 其中 RAG 示例库中有同型示例的：23 条，全新问题：17 条
- 类别分布：TopN 6, 分组对比 10, 多条件 5, 指标聚合 8, 派生综合 2, 筛选计数 6, 统计极值 3

## 一、配置对照表

判定标准为**执行结果一致率**：生成的 SQL 与人工参照 SQL 都真正执行，比较结果集（行数、列数、取值；行顺序无关，数值按 0.1% 相对容差）。

| 配置 | 可执行率 | 结果一致率 | 列容错一致率 | 相似问题一致率 | 新问题一致率 | 平均耗时 | 平均 Prompt 字符 |
|---|---|---|---|---|---|---|---|
| `rag_on+metrics_on+mapping_on` | 100.0% | 80.0% | 95.0% | 87.0% | 70.6% | 5464 ms | 5763 |

严格口径判错、但列容错口径判对的用例（即「答案对、多带了几列上下文」）：

- `rag_on+metrics_on+mapping_on`：c26、c28、c29、c31、c34、c37


以 `rag_on+metrics_on+mapping_on` 为基线的增量：

| 配置 | 结果一致率增量 | 相似问题 | 新问题 |
|---|---|---|---|

## 二、逐例明细

| 用例 | 问题 | 类别 | 在示例库 | `rag_on+metrics_on+mapping_on` |
|---|---|---|---|---|
| c01 | 平均缺陷率是多少 | 指标聚合 | 否 | 一致 |
| c02 | 整体平均良率是多少 | 指标聚合 | 否 | 一致 |
| c03 | 平均质量得分是多少 | 指标聚合 | 否 | 一致 |
| c04 | 总停机时长是多少分钟 | 指标聚合 | 否 | 一致 |
| c05 | 一共发生了多少次故障 | 指标聚合 | 否 | 一致 |
| c06 | 总产量是多少 | 指标聚合 | 否 | 一致 |
| c07 | 平均设备利用率是多少 | 指标聚合 | 否 | 一致 |
| c08 | 平均生产节拍是多少秒 | 指标聚合 | 否 | 一致 |
| c09 | 各生产线的平均缺陷率，从高到低排列 | 分组对比 | 是 | 一致 |
| c10 | 每种产品的良率平均值分别是多少 | 分组对比 | 是 | 一致 |
| c11 | 各生产线的平均质量得分排序 | 分组对比 | 否 | 一致 |
| c12 | 各班组的平均质量得分 | 分组对比 | 是 | 一致 |
| c13 | 各生产线的总产量分别是多少 | 分组对比 | 是 | 一致 |
| c14 | 各生产线的停机总时长 | 分组对比 | 否 | 一致 |
| c15 | 各生产线的设备利用率平均水平 | 分组对比 | 是 | 一致 |
| c16 | 不同生产模式下的平均缺陷率 | 分组对比 | 是 | 一致 |
| c17 | 每条生产线涉及多少台设备 | 分组对比 | 是 | 一致 |
| c18 | 产量最高的十个批次 | 分组对比 | 是 | 一致 |
| c19 | 缺陷率超过 5 的记录有多少条 | 筛选计数 | 是 | 一致 |
| c20 | 良率不足 90 的生产记录有几条 | 筛选计数 | 是 | 一致 |
| c21 | 停机超过半小时的记录数量 | 筛选计数 | 是 | 一致 |
| c22 | 利用率不到 60 的设备编号有哪些 | 筛选计数 | 是 | 一致 |
| c23 | 出现过故障的记录占比是多少 | 筛选计数 | 否 | 结果不符 |
| c24 | 质量得分高于 95 的记录数 | 筛选计数 | 否 | 一致 |
| c25 | 停机时间最长的 10 台设备 | TopN | 是 | 一致 |
| c26 | 良率最高的 5 条生产记录 | TopN | 否 | 结果不符 |
| c27 | 故障次数最多的 10 台设备 | TopN | 是 | 一致 |
| c28 | 质量得分最低的 10 条记录 | TopN | 否 | 结果不符 |
| c29 | 产量最高的 5 条记录 | TopN | 否 | 结果不符 |
| c30 | 缺陷率最高的 10 条记录 | TopN | 否 | 结果不符 |
| c31 | Line_A 这条产线的平均缺陷率 | 多条件 | 是 | 结果不符 |
| c32 | Product_B 在 Line_C 上的平均良率 | 多条件 | 否 | 一致 |
| c33 | Night 班次的质量得分平均是多少 | 多条件 | 是 | 一致 |
| c34 | M01 这台设备累计停机多少分钟 | 多条件 | 是 | 结果不符 |
| c35 | 各生产线 Product_A 的产量合计 | 多条件 | 是 | 一致 |
| c36 | 缺陷率的最大值和最小值分别是多少 | 统计极值 | 是 | 一致 |
| c37 | 各生产线缺陷率的波动程度（标准差） | 统计极值 | 是 | 结果不符 |
| c38 | 单位生产成本的平均值 | 统计极值 | 是 | 一致 |
| c39 | 综合效益得分的平均值 | 派生综合 | 是 | 一致 |
| c40 | 各产品类型的平均缺陷率与平均良率 | 派生综合 | 是 | 一致 |

## 三、不一致用例的 SQL 对照

下面 8 条在**所有配置下都没有一致**，最值得先看。
若生成 SQL 与参照 SQL 语义等价、只是取数范围不同（典型是 ``LIMIT`` 大小差异），
那更可能是评测口径的假阴性，而不是模型算错。

### c23

- 参照 SQL：`SELECT SUM(CASE WHEN fault_event_count > 0 THEN 1 ELSE 0 END) / COUNT(*) AS fault_ratio FROM fact_production_record`

- **`rag_on+metrics_on+mapping_on`**：结果不符，生成 1 行
  - 生成 SQL：`SELECT COUNT(*) AS total_records, SUM(CASE WHEN fault_event_count > 0 THEN 1 ELSE 0 END) AS fault_records, ROUND(SUM(CASE WHEN fault_event_count > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS fault_record_pct FROM fact_production_record`

### c26

- 参照 SQL：`SELECT record_id, first_pass_yield FROM fact_production_record ORDER BY first_pass_yield DESC LIMIT 5`

- **`rag_on+metrics_on+mapping_on`**：结果不符，生成 5 行
  - 生成 SQL：`SELECT record_id, production_line, machine_id, product_type, shift, first_pass_yield FROM fact_production_record ORDER BY first_pass_yield DESC LIMIT 5`

### c28

- 参照 SQL：`SELECT record_id, quality_score FROM fact_production_record ORDER BY quality_score ASC LIMIT 10`

- **`rag_on+metrics_on+mapping_on`**：结果不符，生成 10 行
  - 生成 SQL：`SELECT record_id, machine_id, production_line, batch_id, shift, product_type, quality_score FROM fact_production_record ORDER BY quality_score ASC LIMIT 10`

### c29

- 参照 SQL：`SELECT record_id, production_volume FROM fact_production_record ORDER BY production_volume DESC LIMIT 5`

- **`rag_on+metrics_on+mapping_on`**：结果不符，生成 5 行
  - 生成 SQL：`SELECT record_id, machine_id, production_line, batch_id, shift, product_type, production_volume FROM fact_production_record ORDER BY production_volume DESC LIMIT 5`

### c30

- 参照 SQL：`SELECT record_id, defect_rate FROM fact_production_record ORDER BY defect_rate DESC LIMIT 10`

- **`rag_on+metrics_on+mapping_on`**：结果不符，生成 10 行
  - 生成 SQL：`SELECT record_id, machine_id, production_line, batch_id, shift, product_type, defect_rate, quality_score, first_pass_yield FROM fact_production_record ORDER BY defect_rate DESC LIMIT 10`

### c31

- 参照 SQL：`SELECT AVG(defect_rate) AS avg_defect_rate FROM fact_production_record WHERE production_line = 'Line_A'`

- **`rag_on+metrics_on+mapping_on`**：结果不符，生成 1 行
  - 生成 SQL：`SELECT production_line, AVG(defect_rate) AS avg_defect_rate, COUNT(*) AS record_count FROM fact_production_record WHERE production_line = 'Line_A' GROUP BY production_line`

### c34

- 参照 SQL：`SELECT SUM(downtime_minutes) AS total_downtime_minutes FROM fact_production_record WHERE machine_id = 'M01'`

- **`rag_on+metrics_on+mapping_on`**：结果不符，生成 1 行
  - 生成 SQL：`SELECT SUM(downtime_minutes) AS total_downtime_minutes, COUNT(*) AS record_count, AVG(downtime_minutes) AS avg_downtime_minutes FROM fact_production_record WHERE machine_id = 'M01'`

### c37

- 参照 SQL：`SELECT production_line, STDDEV_POP(defect_rate) AS defect_rate_std FROM fact_production_record GROUP BY production_line ORDER BY defect_rate_std DESC`

- **`rag_on+metrics_on+mapping_on`**：结果不符，生成 5 行
  - 生成 SQL：`SELECT production_line, STDDEV_POP(defect_rate) AS defect_rate_std, AVG(defect_rate) AS avg_defect_rate, COUNT(*) AS record_count FROM fact_production_record GROUP BY production_line ORDER BY defect_rate_std DESC LIMIT 50`


## 三、怎么解读这张表

- 看**相似问题一致率**：RAG 段只有在示例库里存在同型问题时才应该带来提升；如果这一列没提升，说明检索或示例库有问题。
- 看**新问题一致率**：RAG 不应该显著拖累全新问题（示例是参考而不是答案）；如果掉了，通常是 ``RAG_MIN_SCORE`` 太低、把不相似的示例也塞进了 Prompt。
- 看**指标段**：对比 `rag_off+metrics_on` 与 `rag_off+metrics_off`，差异反映业务口径对齐（字段映射、计算口径）的价值。
- 看**字段映射**（`mapping_on` vs `mapping_off`，同表同用例）：差值就是「标准字段口径 + 单位换算 + 示例改写」这一层的净增益。客户库列名与业务词差异越大、单位越不统一，这一层的增益应该越明显。
- 看**可执行率**：它衡量字段名是否写对。`mapping_off` 若可执行率尚可但结果一致率很低，说明模型猜对了列名却算错了口径（典型是漏掉单位换算）。
- 看**平均 Prompt 字符**：增益是否值得付出的上下文成本。

## 四、口径与局限

- 参照 SQL 由人工编写，其执行结果即标准答案；结果集比较不考虑行顺序。
- 换表对照（客户侧用例集）中的参照 SQL 由**映射自动翻译**生成，翻译前已用 `scripts/build_customer_cases.py` 逐条验证「翻译后结果集与标准表完全一致」（见 `evals/reports/*_mapping_check.md`）。因此该对照里出现的偏差只能归因于模型，而不是映射本身。
- 客户表 45 列**没有任何数据库注释**，这正是真实 MES 视图的常见形态；`mapping_off` 配置因此退化为「只给模型列名让它猜」，是映射价值的合理下界。
- 当前数据底座**没有时间列**，因此用例集不含「最近 7 天趋势」这类时间维问题；要覆盖它们需要先给服务层补 `production_time` 之类的字段。
- 单轮评测：每条用例使用独立会话（``session_max_turns=0``），多轮追问能力不在这张表里体现。
