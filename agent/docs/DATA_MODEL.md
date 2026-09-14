# 数据模型说明（单表宽表模型 · 路线 B）

> 本文同时可作为参赛材料里的「数据资源说明」：说明系统使用的数据表、字段、样例数据与选型理由。

## 1. 结论

预置数据底座采用**单表宽表模型**：

| 分层 | 表 | 谁在用 | 说明 |
|---|---|---|---|
| 原始层 | `intelligent_production_iiot` | 装载脚本 | `data/Intelligent_Production_IIoT.csv` 导入的原样数据，字段类型未经治理，**只读** |
| 服务层 | `fact_production_record` | Agent / 建模 / 前端 | 显式 DDL 治理后的宽表，15000 行 × 45 列，**Agent 只查这一张** |

维度（产线 / 设备 / 产品 / 批次 / 班次）以**编码列**的形式冗余存放在宽表里，不再派生 `dim_*` 维表。

## 2. 为什么不再派生维表

历史实现是「把源宽表整表复制成 fact 表，再用 `SELECT DISTINCT` 从同一张宽表抽出 4 张维表」，
即在物理上是宽表（OBT），在元数据上却声明成星型模型。实测结果：

| 事实 | 数据 |
|---|---|
| fact 表就是源表的 1:1 副本 | `CREATE TABLE ... LIKE` + `INSERT ... SELECT *`，15000 行 45 列 |
| 维表没有带来任何信息 | `dim_line.description` 非空 **0/5**，`dim_product.product_name` 非空 **0/4**，`dim_batch` 只有 1 个编码列 |
| JOIN 与不 JOIN 完全等价 | `fact JOIN dim_machine / dim_line / dim_product / dim_batch` 全部 15000 → 15000，取值不变 |
| 声明了 FK 反而有害 | LLM 会为此生成无收益的 JOIN；一旦编码列出现 NULL，INNER JOIN 还会静默丢行 |

因此选择路线 B：**保留宽表，删除派生维表**。数据没有丢失——原始层不动，随时可以重新派生。

### 什么时候应该反过来选星型模型（路线 A）

- 维度属性确实有价值（产线名称、产品名称、客户、时间维），且会被反复按维度筛选/展示；
- 事实表的粒度与维度属性不是 1:1（例如一条记录关联多个产品）；
- 需要演示「表间关系 / 知识图谱」：那就必须**真拆**——fact 只留外键与度量，并把传递依赖
  （如 `production_line` 可由 `machine_id` 推出）从 fact 移除，让 JOIN 真正有意义。

## 3. 表结构

`fact_production_record`（45 列，一行 = 一次生产运行）：

| 分组 | 列 | 类型规则 |
|---|---|---|
| 主键与维度编码 | `record_id`、`machine_id`、`production_line`、`batch_id`、`shift`、`product_type` | 主键与计数用 `INT`，编码与枚举用 `VARCHAR` |
| 环境与设备传感器 | `ambient_temperature`、`humidity`、`air_pressure`、`ambient_vibration`、`motor_temperature`、`spindle_speed`、`motor_current`、`torque`、`vibration`、`acoustic_level`、`bearing_temperature`、`process_temperature`、`hydraulic_pressure`、`flow_rate`、`coolant_temperature`、`material_feed_rate`、`feed_pressure` | 测量值用 `DOUBLE`，**允许 NULL**（原始数据缺失率约 2.3%~2.7%） |
| 产量 / 效率 / 能耗 | `cycle_time`、`throughput_rate`、`production_volume`、`machine_utilization`、`resource_utilization`、`operator_load`、`power_consumption`、`energy_per_unit` | `DOUBLE`、`NOT NULL` |
| 质量与设备健康指标 | `defect_rate`、`quality_score`、`first_pass_yield`、`fault_event_count`、`downtime_minutes`、`maintenance_frequency`、`production_cost_per_unit` | 比率/得分用 `DOUBLE`，计数用 `INT`，金额用 `DECIMAL(12,4)` |
| 改善类指标 | `resource_efficiency`、`production_efficiency`、`energy_saving_pct`、`downtime_reduction_pct`、`cost_reduction_pct`、`benefit_score` | `DOUBLE`、`NOT NULL` |
| 生产模式 | `operation_mode` | `VARCHAR(64)` |

类型选择规则：**编码 → VARCHAR，主键与计数 → INT，金额 → DECIMAL，测量值/比率/得分 → DOUBLE**。
每个列都带 `COMMENT`，它同时是 Prompt 里字段说明的来源。

## 4. 粒度（grain）与主键

- **grain：一行 = 一次生产运行**（写在表 COMMENT 与 `record_id` 列 COMMENT 里）。
- `record_id` 为 `PRIMARY KEY`，实际数据 15000 行 / 15000 个唯一值 / 0 个 NULL。

旧实现里 `record_id` 既不是主键也没有唯一约束，grain 只写在注释里，重复装载会静默翻倍。

## 5. 字段类型治理（本次修复的核心问题）

旧表由 `CREATE TABLE fact_production_record LIKE intelligent_production_iiot` 建出来，
把 CSV 导入时推断的脏类型一起继承：45 列里 **17 列是 `TEXT`，其中 11 列其实是数值指标**。

TEXT 存数值的后果是排序与最值按**字典序**计算。实测：

```sql
-- 修复前
SELECT downtime_minutes FROM fact_production_record ORDER BY downtime_minutes DESC LIMIT 3;
-- 9.999086271325403 / 9.993789880359603 / 9.98989053444435
-- 但同一列里存在 25.08105870816925：'9' > '2'，字典序把 25.08 排到了后面
```

「找出停机时间最长的设备」「良率最高的产品」这类查询会**静默返回错误答案**。
`MIN` / `MAX` 同理；`AVG` / `SUM` 靠 MySQL 隐式转换能算，但走全表扫描且不稳定。

修复内容：11 列在装载时显式 `CAST` 为数值类型，并已核对源数据**没有空值、没有非数字脏值**
（转换不会把脏值静默变成 0）。

## 6. 加载与幂等

`sql/` 目录下的 SQL 是**表结构的唯一事实来源**（可 review、可 diff、可单独执行）：

| 文件 | 作用 |
|---|---|
| `01_drop_legacy_dim_tables.sql` | 删除历史派生维表 |
| `02_create_fact_production_record.sql` | 显式 DDL：类型 / 主键 / NOT NULL / COMMENT / 索引 |
| `03_load_fact_production_record.sql` | `TRUNCATE` + 显式列清单 + 显式 `CAST` |

要点：

- **不再用 `CREATE TABLE ... LIKE`**：意图写进 DDL 才能 review，上游变更也不会静默改结构；
- **不再用 `SELECT *`**：显式列清单，上游加列或改列序不会静默影响服务层；
- **幂等**：`TRUNCATE` + 全量重灌，源表只读，脚本可重复执行；
- `metadata/schema_ddl.py` 解析 DDL，供装载脚本与健康检查共用，避免「DDL 一套、代码一套」。

```powershell
cd agent
D:\Anaconda\envs\sqllangchain\python.exe scripts\init_preset_schema.py
```

## 7. 数据质量校验

```powershell
cd agent
D:\Anaconda\envs\sqllangchain\python.exe scripts\check_schema_health.py
```

9 项检查（全部以 DDL 为期望值，退出码非 0 即失败，可用于演示前自检）：

1. 表清单符合单表模型：只有原始层 + 服务层，没有历史派生维表
2. 列清单与顺序与 DDL 一致
3. 列类型与 DDL 一致，且不存在 `TEXT` 类型列
4. 主键与 grain：`record_id` 为主键、唯一、非空
5. 可空性与 DDL 一致，且 `NOT NULL` 列实际没有空值
6. 行数与原始层一致，且 11 个转换列的**均值保真**（源表文本 vs 服务层数值）
7. 数值排序回归：列的 `MIN`/`MAX` 必须等于「按数值解释后的 `MIN`/`MAX`」
   （TEXT 存储时是字典序，两者不等；不能只比较 `ORDER BY` 与 `MAX`，那样在 TEXT 上会自洽而漏判）
8. 每个列都有 `COMMENT`
9. `preset_metadata` 不引用不存在的表或列

## 8. 与 Agent 的关系

- **元数据段（Prompt）**：由 `metadata_service` 读取列 `COMMENT` 与样例值生成；
  单表模型下 `DataResourceProvider` 会在段尾追加「当前数据底座为单表模型，不要生成 JOIN」，
  防止模型在冗余的维度编码上做自连接。
- **指标段（Prompt）**：`core/metrics.py` 把业务指标解析到本表的真实列，
  例如 缺陷率 → `fact_production_record.defect_rate`。
- **知识段（Prompt）**：`knowledge/knowledge_base.py` 的业务对象（产线/设备/产品/批次/班次）
  全部指向本表的对应编码列；预留主题（库存分析）会明确标注「未接入数据表」。
- **建模取数**：`tools/modeling.py` 直接读本表，类型统一为数值后可省去文本到数值的转换。
- **SQL 守卫**：Agent 只允许对本表做只读查询（见 `docs/PROMPT_AND_SQL_GUARD.md`）。

## 9. 如何扩展

- **新增一张数据表**：在 `sql/` 下新增 DDL 与装载 SQL → 加入 `metadata/schema_ddl.py::SCHEMA_FILES`
  → 加入 `preset_metadata.PRESET_BUSINESS_TABLES` → 跑初始化与健康检查。
  （多表后 `DataResourceProvider` 会自动改为输出「表间关系」而不是单表提示。）
- **切回星型模型**：从原始层重新派生维表并补上真实属性，在 `preset_metadata.RELATIONSHIPS`
  里补充关系，同时把传递依赖从 fact 移除。
- **新增指标**：在 `core/metrics.py` 的 `BUSINESS_METRICS` 与 `knowledge_base.BUSINESS_RULES`
  里补口径，健康检查会校验字段确实存在。
