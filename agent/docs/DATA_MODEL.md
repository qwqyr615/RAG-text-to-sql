# 数据模型说明（发现模式 + 字段映射）

> 本文同时可作为参赛材料里的「数据资源说明」：说明系统如何面向**未知的客户数据库**
> 建立数据资源理解，以及本次使用的两套数据源。

## 1. 结论：从「硬编码单表」到「发现 + 映射」

改造前，Agent 可见的业务表被写死在代码里：

```python
PRESET_BUSINESS_TABLES = ["fact_production_record"]   # 换一个客户库就要改代码
```

改造后分成两层，**都不需要改代码**：

| 层次 | 解决的问题 | 落点 |
|---|---|---|
| **发现模式** | 「这个库里有哪几张表能查？」 | `metadata/inventory.py`：扫描全库 + 排除规则 |
| **字段映射** | 「业务说的『缺陷率』对应客户哪一列？单位一样吗？」 | `mappings/*.mapping.yaml` + `metadata/mapping/` |

于是接入一个新客户的完整动作变成：**写一份 `mapping.yaml`**（可用 LLM 草拟 + 人工审核），
不碰任何 Python 代码。

```
MySQL 客户库
    ↓  discover_tables()      扫描全库，套用排除规则（系统表 / 原始层）
可见表集合
    ↓  mapping.yaml           标准字段 ↔ 客户列 + 单位换算 + grain + 枚举取值
    ↓  compile_profile()      编译成运行时口径
metadata JSON（表格结构 + field_map + metric_bindings + inventory）
    ├── 动态 Prompt（元数据段 / 指标段 / 知识段 / RAG 段）→ LangChain SQL Agent
    └── outputs/metadata.json → 前端 / 接口
```

## 2. 两套数据源（本次评测用的对照）

| 分层 | 表 | 列名风格 | 列注释 | 谁在用 |
|---|---|---|---|---|
| 原始层 | `intelligent_production_iiot` | 原始 CSV | 无 | 只读，**对 Agent 排除** |
| 标准层 | `fact_production_record` | 标准字段名 | 45 列全有 | 标准模型 baseline |
| **客户层** | `mes_prod_log` | MES 缩写（`def_rate`/`stop_min`/`fpy`） | **45 列全空** | 「换一张表」对照 |

三张表都是 15000 行 × 45 列，逐行一一对应。`mes_prod_log` 模拟的是真实场景里最常见的
客户数据形态：**列名是别人家的缩写，注释一个都没有**。

### 唯一的语义差异：两列的单位

逐列对比后发现，`mes_prod_log` 与标准表**只有两列不是简单改名，而是换算了单位**：

| 标准字段 | 标准列 | 客户列 | 标准值 | 客户值 | 关系 |
|---|---|---|---|---|---|
| `defect_rate` 缺陷率 | `defect_rate` | `def_rate` | 3.906 | 0.03906 | ×100（百分数 ↔ 比例） |
| `machine_utilization` 设备利用率 | `machine_utilization` | `util` | 70.135 | 0.70135 | ×100（百分数 ↔ 比例） |

其余 43 列在数值上逐格相等（已用全表比对验证），只是改了名字。

**这两列是整套方案里最危险的失败模式**：漏掉 ×100 不会报错，只会让结果整体差 100 倍。
因此映射文件把换算写成显式表达式（`def_rate * 100`），并且：

- Prompt 的字段映射块会专门强调「漏掉换算会让结果整体差一个倍数」；
- 指标段直接给出 `AVG(def_rate * 100)` 这样的成品写法；
- `scripts/build_customer_cases.py` 会在**不调用大模型**的情况下，先证明
  「按映射翻译后的参照 SQL 与标准表结果集完全一致（40/40）」。

## 3. 发现模式

`metadata/inventory.py::discover_tables()` 做三件事：

1. `inspect(engine).get_table_names()` 扫描全库；
2. 套用排除规则（**取并集**）：
   - 跨客户通用：`information_schema` / `performance_schema` / `pg_` / `sqlite_` /
     `alembic_version` 等系统表前缀；
   - 客户特有：写在 `mapping.yaml` 的 `discovery.exclude` 里；
3. 给剩下的表标注角色（`fact` / `dimension` / `source` / `other`），输出扫描账本。

账本会进 metadata JSON 的 `inventory` 字段，因此「某张表为什么看不到」有据可查，
而不是靠猜。

**为什么排除原始层是硬要求**：`intelligent_production_iiot` 是同一个 CSV 的原样导入，
11 个数值指标以 `TEXT` 存储。把它暴露给 Agent，模型会：

- 在两张内容相同的表之间随机挑一张；
- 可能挑到脏类型那张，于是 `MIN`/`MAX`/`ORDER BY` 按**字典序**计算 ——
  `'9.99'` 大于 `'25.08'`，「停机时间最长的设备」会静默返回错误答案。

实测证据见本文第 7 节。

## 4. 表结构

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
每个列都带 `COMMENT`。

## 5. 标准字段词典

`metadata/standard_fields.py` 定义 **45 个标准字段**，它是「客户列 ↔ 标准口径」映射的
左边那一半。每个字段带：

- `name` 规范名（也是标准口径 SQL 里的列名）；
- `label` 中文名（业务人员提问时用的词）；
- `unit` **规范单位**（客户单位不同就必须在映射里声明换算）；
- `data_kind`（`key` / `dimension` / `measure` / `time`）；
- `column_candidates` 客户库里可能叫什么（用于自动草拟映射）；
- `aliases` 业务别名。

同时定义 **7 个业务指标口径**（`STANDARD_METRICS`），原先散在 `core/metrics.py` 与
`knowledge/knowledge_base.py` 的两份口径在此统一，`core/metrics.py` 改为从词典派生，
避免同一指标两处维护、两处漂移。

词典里刻意**不**依赖任何具体客户：客户特有的东西（列名、单位、枚举取值、表名）一律
放 `mapping.yaml`。

## 6. `mapping.yaml`：客户接入契约

完整示例见 `mappings/mes_prod_log.mapping.yaml`，结构：

```yaml
schema_version: "1.0"
profile: mes_prod_log              # 档案名
description: 某厂 MES 生产日志
database: {dialect: mysql, url_env: DATABASE_URL}
discovery:                          # 发现模式的客户特有规则
  exclude: [intelligent_production_iiot, fact_production_record]
relationships: []                   # 多表客户库在这里声明 JOIN 关系
tables:
  - name: mes_prod_log
    role: fact                      # fact / dimension / bridge / snapshot / source / other
    grain: 一行 = 一次生产运行（rec_no 唯一）
    primary_key: rec_no
    time_column: ""                 # 有时间列时填上，Prompt 会提示可用时间过滤
    columns:
      - column: def_rate            # 客户的真实列名
        standard_fields:
          - name: defect_rate       # 对应哪个标准字段
            scale: 100              # 客户单位 -> 标准口径的换算系数
            expression: def_rate * 100
            unit: 比例(0-1)          # 客户侧单位（写清楚便于人审）
            notes: SQL 必须写成 def_rate * 100
```

校验规则（`mapping.cli validate`）：

- 未知键、缺 `name`、`scale` 非数字 → **error**（结构错，直接失败）；
- 标准字段名不在词典里 → **error**（防止接入时自造字段名）；
- 同一标准字段在表内重复绑定 → **error**（会造成口径二义）；
- 映射的列在库里不存在（`--against-db`）→ **error**；
- 目标表同时出现在 `discovery.exclude` 里 → **error**（发现模式会把映射目标排除掉）。

## 7. 字段说明的来源优先级

```
mapping.yaml 的标准字段口径   ← 最高（人审过、带单位换算与枚举取值）
        ↓ 没有时
数据库列 COMMENT               ← 标准底座走这条
        ↓ 没有时
preset_metadata 人工兜底说明   ← 最后的兜底
```

映射优先于 `COMMENT` 是刻意的：客户库几乎没有列注释，而映射里的口径信息量严格更大。
若两者冲突（例如库里注释过期），以映射为准。

## 8. 字段类型治理（历史问题的修复，保留记录）

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

修复内容：11 列在装载时显式 `CAST` 为数值类型，并已核对源数据**没有空值、没有非数字脏值**。
这也是原始层必须对 Agent 排除的原因。

## 9. 加载与幂等

`sql/` 目录下的 SQL 是**标准底座表结构的唯一事实来源**（可 review、可 diff、可单独执行）：

| 文件 | 作用 |
|---|---|
| `01_drop_legacy_dim_tables.sql` | 删除历史派生维表 |
| `02_create_fact_production_record.sql` | 显式 DDL：类型 / 主键 / NOT NULL / COMMENT / 索引 |
| `03_load_fact_production_record.sql` | `TRUNCATE` + 显式列清单 + 显式 `CAST` |

```powershell
cd agent
D:\Anaconda\envs\sqllangchain\python.exe scripts\init_preset_schema.py
```

## 10. 数据质量校验

```powershell
cd agent
$env:ANALYSIS_MAPPING="mappings/standard_production.mapping.yaml"   # 可选
D:\Anaconda\envs\sqllangchain\python.exe scripts\check_schema_health.py
```

11 项检查（退出码非 0 即失败，可用于演示前自检）：

| # | 检查 | 说明 |
|---|---|---|
| 1 | 标准模型表清单 | 原始层 + 服务层存在，无历史派生维表；**不**因为库里还有别的客户表而失败 |
| 2 | 列清单与顺序与 DDL 一致 | 防漂移 |
| 3 | 列类型与 DDL 一致且无 `TEXT` 列 | 类型治理的核心断言 |
| 4 | 主键与 grain 唯一性 | `record_id` 为主键、唯一、非空 |
| 5 | 可空性与 DDL 一致且无空值违反 | |
| 6 | 行数与数值保真 | 与原始层行数一致，11 个转换列均值保真 |
| 7 | 数值排序回归 | `MIN`/`MAX` 必须与「按数值解释」一致 |
| 8 | 每个列都有 `COMMENT` | |
| 9 | **发现模式** | 扫描结果非空，且原始层被挡住 |
| 10 | **字段映射** | 能编译、引用的表与列真实存在、覆盖率可解释 |
| 11 | 兜底说明不引用不存在的表或列 | |

第 9、10 项替代了原先的「白名单不引用不存在的表」：业务表范围不再是代码常量，
而是发现与映射的产物，校验对象也跟着变。

## 11. 与 Agent 的关系

- **元数据段（Prompt）**：表结构来自 `metadata_service`；若有映射，额外插入
  「标准字段 ↔ 客户列」块（含单位换算强调）。
- **指标段（Prompt）**：有映射时直接用编译好的客户侧表达式
  （`AVG(def_rate * 100)`）；没有映射时退回启发式字段匹配。
- **RAG 段（Prompt）**：示例库里的 SQL 是标准字段口径，注入前经 `ColumnRewriter`
  改写成客户列口径；改写后仍引用未映射字段的示例会被**丢弃**（给一个跑不通的例子
  比不给更糟）。
- **知识段（Prompt）**：`knowledge/knowledge_base.py` 的业务对象 / 分析主题按
  映射解析到客户表与列；预留主题（库存分析）明确标注「未接入数据表」。
- **SQL 守卫**：Agent 只允许只读查询，且 `include_tables` 取自发现结果
  （见 `docs/PROMPT_AND_SQL_GUARD.md`）。
- **建模取数**：`tools/modeling.py` 直接读表，类型统一为数值后可省去文本到数值转换。

## 12. 如何扩展

- **接入新客户**：
  ```powershell
  python -m metadata.mapping.cli discover                      # 看看库里有什么
  python -m metadata.mapping.cli draft --table <表名> --out mappings/<客户>.mapping.yaml
  python -m metadata.mapping.cli review --mapping mappings/<客户>.mapping.yaml --against-db
  python scripts/build_customer_cases.py --mapping mappings/<客户>.mapping.yaml   # 零成本验证映射
  $env:ANALYSIS_MAPPING="mappings/<客户>.mapping.yaml"
  ```
- **新增标准字段**：在 `standard_fields.py` 里加一条（含 `column_candidates`），
  已有的映射不必改，只是多了一个可选口径。
- **新增业务指标**：在 `STANDARD_METRICS` 里加一条，指向某个标准字段。
- **多表客户库**：在 `mapping.yaml` 里声明多张表并在 `relationships` 里补真实关系；
  `DataResourceProvider` 会自动改为输出「表间关系」而不是单表提示。
- **新增数据表（标准底座）**：在 `sql/` 下新增 DDL 与装载 SQL → 加入
  `metadata/schema_ddl.py::SCHEMA_FILES` → 在 `mappings/standard_production.mapping.yaml`
  里补列映射 → 跑初始化与健康检查。
