# 映射语义等价性检查：`mes_prod_log`

- 标准表：`fact_production_record` → 客户表：`mes_prod_log`
- 用例数：40
- **结果集等价：40/40（100.0%）**
- 触发列改写的用例：40 条

判定方式：把标准参照 SQL 按映射翻译后执行，与标准表上的结果集比较（行数、列数、取值；行顺序无关，数值按 0.1% 相对容差）。
全部等价即说明**映射语义完整** —— 评测里再出现的偏差都只能归因于模型，而不是映射本身。

## 一、逐条结果

| 用例 | 类别 | 结果 | 行数(标准/客户) | 改写 |
|---|---|---|---|---|
| c01 | 指标聚合 | 等价 | 1/1 | defect_rate→def_rate * 100 |
| c02 | 指标聚合 | 等价 | 1/1 | first_pass_yield→fpy |
| c03 | 指标聚合 | 等价 | 1/1 | quality_score→q_score |
| c04 | 指标聚合 | 等价 | 1/1 | downtime_minutes→stop_min |
| c05 | 指标聚合 | 等价 | 1/1 | fault_event_count→alarm_cnt |
| c06 | 指标聚合 | 等价 | 1/1 | production_volume→out_qty |
| c07 | 指标聚合 | 等价 | 1/1 | machine_utilization→util * 100 |
| c08 | 指标聚合 | 等价 | 1/1 | cycle_time→ct |
| c09 | 分组对比 | 等价 | 5/5 | production_line→line_cd，defect_rate→def_rate * 100 |
| c10 | 分组对比 | 等价 | 4/4 | product_type→prod_tp，first_pass_yield→fpy |
| c11 | 分组对比 | 等价 | 5/5 | production_line→line_cd，quality_score→q_score |
| c12 | 分组对比 | 等价 | 3/3 | shift→sft，quality_score→q_score |
| c13 | 分组对比 | 等价 | 5/5 | production_line→line_cd，production_volume→out_qty |
| c14 | 分组对比 | 等价 | 5/5 | production_line→line_cd，downtime_minutes→stop_min |
| c15 | 分组对比 | 等价 | 5/5 | production_line→line_cd，machine_utilization→util * 100 |
| c16 | 分组对比 | 等价 | 4/4 | operation_mode→op_mode，defect_rate→def_rate * 100 |
| c17 | 分组对比 | 等价 | 5/5 | production_line→line_cd，machine_id→mach_no |
| c18 | 分组对比 | 等价 | 10/10 | batch_id→lot_no，production_volume→out_qty |
| c19 | 筛选计数 | 等价 | 1/1 | defect_rate→def_rate * 100 |
| c20 | 筛选计数 | 等价 | 1/1 | first_pass_yield→fpy |
| c21 | 筛选计数 | 等价 | 1/1 | downtime_minutes→stop_min |
| c22 | 筛选计数 | 等价 | 20/20 | machine_id→mach_no，machine_utilization→util * 100 |
| c23 | 筛选计数 | 等价 | 1/1 | fault_event_count→alarm_cnt |
| c24 | 筛选计数 | 等价 | 1/1 | quality_score→q_score |
| c25 | TopN | 等价 | 10/10 | machine_id→mach_no，downtime_minutes→stop_min |
| c26 | TopN | 等价 | 5/5 | record_id→rec_no，first_pass_yield→fpy |
| c27 | TopN | 等价 | 10/10 | machine_id→mach_no，fault_event_count→alarm_cnt |
| c28 | TopN | 等价 | 10/10 | record_id→rec_no，quality_score→q_score |
| c29 | TopN | 等价 | 5/5 | record_id→rec_no，production_volume→out_qty |
| c30 | TopN | 等价 | 10/10 | record_id→rec_no，defect_rate→def_rate * 100 |
| c31 | 多条件 | 等价 | 1/1 | defect_rate→def_rate * 100，production_line→line_cd |
| c32 | 多条件 | 等价 | 1/1 | first_pass_yield→fpy，product_type→prod_tp，production_line→line_cd |
| c33 | 多条件 | 等价 | 1/1 | quality_score→q_score，shift→sft |
| c34 | 多条件 | 等价 | 1/1 | downtime_minutes→stop_min，machine_id→mach_no |
| c35 | 多条件 | 等价 | 5/5 | production_line→line_cd，production_volume→out_qty，product_type→prod_tp |
| c36 | 统计极值 | 等价 | 1/1 | defect_rate→def_rate * 100 |
| c37 | 统计极值 | 等价 | 5/5 | production_line→line_cd，defect_rate→def_rate * 100 |
| c38 | 统计极值 | 等价 | 1/1 | production_cost_per_unit→unit_cost |
| c39 | 派生综合 | 等价 | 1/1 | benefit_score→benefit |
| c40 | 派生综合 | 等价 | 4/4 | product_type→prod_tp，defect_rate→def_rate * 100，first_pass_yield→fpy |

## 三、口径说明

- 本检查只验证**映射**（列名、单位换算、表名），不涉及大模型。
- 复用评测的比较函数 `evals.runner.rows_match`，因此这里的「等价」与评测里的「结果一致」是同一个判定标准。
- 单位换算字段（如 `def_rate * 100`）若写错，会在这里就被拦下，不会污染后续的模型评测数字。
