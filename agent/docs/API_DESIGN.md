# 接口设计（已实现）

本文档描述**当前实际可用**的接口契约，是前端、Java 网关与 Python 服务之间的
唯一事实来源。原「接口预留说明」中的设计已在实现时扩展（新增异步任务、SSE、
知识图谱等），本版为落地后的最终形态。

## 总体架构

```text
React 前端（FRONTEND/，:5173）
    ↓ HTTP JSON / SSE   /api/v1/**
Spring Boot 网关（backend/，:8080）
    ↓ HTTP JSON / SSE
FastAPI 适配层（agent/server/，:8000）
    ↓ 函数直接调用
Agent 内核（agents/ metadata/ knowledge/ tools/）
    ↓
MySQL + Milvus + DeepSeek
```

启动顺序：**FastAPI → Java 网关 → 前端**。
Java 与 FastAPI 之间通过 `agent.api.base-url` 配置（默认 `http://127.0.0.1:8000`）。

## 基础约定

- 基础路径：`/api/v1`
- 数据格式：`application/json`；SSE 为 `text/event-stream`
- **统一返回信封**（与 Java 侧 `com.agent.result.Result` 字段级一致）：

```json
{ "code": 1, "msg": "", "data": { } }
```

| 字段 | 说明 |
|---|---|
| `code` | `1` 成功；`0` 失败 |
| `msg` | 失败时为可直接展示的中文错误信息 |
| `data` | 业务数据；失败时为 `null` 或部分结果 |

> **为什么统一信封**：Java 网关原样透传即可，前端只需一处解包逻辑
> （`FRONTEND/src/api/client.ts` 的 `unwrap`）。

Python 侧的 Swagger 文档：`http://127.0.0.1:8000/docs`

---

## 1. 系统

### GET /api/v1/system/health

健康检查，用于前端顶部状态指示与启动探测。

```json
{
  "code": 1,
  "msg": "",
  "data": {
    "status": "ok",
    "agent_ready": true,
    "database_type": "mysql",
    "table_count": 3,
    "llm_model": "deepseek-chat",
    "rag_enabled": true,
    "error": null
  }
}
```

`status` 为 `ok`（内核就绪）或 `degraded`（服务在跑但内核未就绪，
通常因为数据库未启动或 `.env` 未配置，此时 `error` 给出原因）。

---

## 2. 数据资源理解

### GET /api/v1/metadata/tables

获取全部业务表、字段、类型、说明、样例值与行数。

```json
{
  "code": 1,
  "data": {
    "schema_version": "1.1",
    "database_type": "mysql",
    "mapping_profile": "",
    "tables": [
      {
        "table_name": "fact_production_record",
        "description": "生产记录事实宽表",
        "role": "fact",
        "grain": "一行代表一次生产运行",
        "primary_key": "record_id",
        "time_column": "",
        "row_count": 15000,
        "columns": [
          {
            "name": "defect_rate",
            "type": "DOUBLE",
            "nullable": false,
            "default": null,
            "primary_key": false,
            "description": "缺陷率百分比，越高越差，口径 AVG(defect_rate)",
            "standard_fields": [],
            "sample_value": 2.7556942401
          }
        ],
        "sample_rows": [ { "record_id": 1, "machine_id": "M07" } ]
      }
    ],
    "relationships": [],
    "field_map": {},
    "metric_bindings": [],
    "unmatched_metrics": [],
    "inventory": {
      "all_tables": ["fact_production_record", "..."],
      "business_tables": ["fact_production_record"],
      "excluded": {},
      "roles": { "fact_production_record": "fact" }
    }
  }
}
```

要点：
- 表范围由**发现模式**决定（扫描数据库 + 排除规则 + `mapping.yaml` 的
  `discovery.include`），不硬编码表白名单；
- 字段说明优先级：`mapping.yaml` 标准字段口径 → DDL `COMMENT` → 预置兜底；
- `sample_rows` 是真实数据抽样（默认 2 行）。

### GET /api/v1/metadata/relationships

表间关系。

```json
{
  "code": 1,
  "data": {
    "relationships": [],
    "database_type": "mysql",
    "note": "单表宽表模型下无维表关系；请使用 /api/v1/knowledge/graph 获取知识图谱。"
  }
}
```

> ⚠️ `dim_*` 维表已删除（当前是单表宽表模型），因此 `relationships`
> 通常为**空数组**。实体关系图没有数据支撑，知识图谱请用第 3 节的
> `/knowledge/graph`。

### GET /api/v1/metadata/field-map

标准字段口径映射，含单位换算与枚举取值。

```json
{
  "code": 1,
  "data": {
    "field_map": {
      "mes_prod_log": [
        {
          "standard_field": "defect_rate",
          "label": "缺陷率",
          "column": "defect_rate_pct",
          "expression": "defect_rate_pct / 100",
          "unit": "%",
          "customer_unit": "%",
          "scale": 0.01,
          "data_kind": "numeric",
          "group": "质量",
          "enum_values": [],
          "needs_conversion": true
        }
      ]
    },
    "metric_bindings": [],
    "unmatched_metrics": [],
    "mapping_profile": "mes_prod_log"
  }
}
```

---

## 3. 业务知识管理

### GET /api/v1/knowledge/overview

主题、业务对象、指标规则的总体结构，并附带图谱。

```json
{
  "code": 1,
  "data": {
    "schema_version": "1.0",
    "themes": [
      {
        "code": "quality_analysis",
        "name": "质量分析",
        "description": "围绕缺陷率、良率、质量得分等进行分析。",
        "related_tables": ["fact_production_record"]
      }
    ],
    "objects": [
      { "name": "设备", "default_table": "fact_production_record", "key_field": "machine_id" }
    ],
    "rules": [
      {
        "name": "缺陷率",
        "mapped_table": "fact_production_record",
        "mapped_field": "defect_rate",
        "resolved_calculation": "AVG(defect_rate)"
      }
    ],
    "graph": { "nodes": [], "edges": [], "categories": [] }
  }
}
```

`rules` 中 `mapped_field` 为 `null` 表示**当前数据源缺少该指标字段**，
`resolved_calculation` 会是「当前数据源缺少该指标字段」——前端应展示为数据缺口。

### GET /api/v1/knowledge/graph

知识图谱节点与边，供前端可视化（ECharts Graph / AntV G6）。

```json
{
  "code": 1,
  "data": {
    "nodes": [
      { "id": "theme:quality_analysis", "name": "质量分析", "category": "主题",
        "description": "围绕缺陷率、良率、质量得分等进行分析。" },
      { "id": "object:设备", "name": "设备", "category": "业务对象", "key_field": "machine_id" },
      { "id": "metric:缺陷率", "name": "缺陷率", "category": "指标口径",
        "expression": "AVG(defect_rate)", "resolved": true },
      { "id": "field:fact_production_record.defect_rate", "name": "defect_rate",
        "category": "字段", "table": "fact_production_record" },
      { "id": "table:fact_production_record", "name": "fact_production_record",
        "category": "数据表", "role": "fact" }
    ],
    "edges": [
      { "source": "theme:quality_analysis", "target": "table:fact_production_record", "label": "涉及数据表" },
      { "source": "metric:缺陷率", "target": "field:fact_production_record.defect_rate", "label": "映射字段" },
      { "source": "field:fact_production_record.defect_rate", "target": "table:fact_production_record", "label": "属于" }
    ],
    "categories": [{ "name": "主题" }, { "name": "业务对象" }, { "name": "指标口径" },
                   { "name": "字段" }, { "name": "数据表" }]
  }
}
```

图谱结构为 **主题 → 业务对象 → 指标口径 → 字段 → 数据表** 的**逻辑图谱**。
因为维表已删除，实体关系图无依据，故改用知识解析结果构图，保证每个节点都有真实来源。
`resolved: false` 的指标节点表示缺口，前端可用空心/虚线样式区分。

**不变量**：每条边的 `source` 与 `target` 都必定是 `nodes` 中存在的 id
（悬空边已在后端过滤），前端可安全渲染。

---

## 4. 自然语言智能分析

### POST /api/v1/agent/ask（同步）

> ⚠️ **最长阻塞 90 秒**（`SQL_AGENT_MAX_EXECUTION_TIME`），实测有 94 秒的案例。
> 前端与生产网关**不应**使用此接口，请改用 `/agent/ask/async` + SSE。
> 保留它是为了脚本调用与接口调试。

请求：

```json
{
  "question": "统计不同生产线的平均缺陷率",
  "sessionId": "web-abc123",
  "metadata": "",
  "businessRules": "",
  "wantChart": true,
  "wantReport": false
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `question` | 是 | 用户自然语言问题 |
| `sessionId` | 否 | 会话 ID，默认 `default`；同一 ID 内保留多轮历史 |
| `metadata` | 否 | 本次补充的数据资源说明 |
| `businessRules` | 否 | 本次补充的业务口径 |
| `wantChart` | 否 | 是否生成图表配置，默认 `true` |
| `wantReport` | 否 | 是否生成 Markdown 报告，默认 `false` |

### AskResponse（`/agent/ask`、`/agent/report` 的 data，及任务结果的 `result`）

```json
{
  "task_type": "sql_query",
  "success": true,
  "question": "统计不同生产线的平均缺陷率",
  "session_id": "web-abc123",
  "turns_used": 0,
  "sql": "SELECT production_line, AVG(defect_rate) AS avg_defect_rate FROM fact_production_record GROUP BY production_line LIMIT 50",
  "columns": ["production_line", "avg_defect_rate"],
  "rows": [["Line_A", 3.9096], ["Line_B", 3.9091]],
  "row_count": 2,
  "analysis_text": "## 结论\n各生产线平均缺陷率接近…",
  "report": null,
  "chart_config": {
    "chart_type": "bar",
    "title": "各生产线平均缺陷率",
    "reason": "不同生产线之间做横向对比，柱状图最直观。",
    "source": "llm",
    "option": {
      "tooltip": { "trigger": "axis" },
      "xAxis": { "type": "category", "data": ["Line_A", "Line_B"] },
      "yAxis": { "type": "value" },
      "series": [{ "type": "bar", "data": [3.9096, 3.9091] }]
    }
  },
  "rag_context": "1. 相似问题：统计不同生产线的平均缺陷率…",
  "prompt_usage": {
    "used_chars": 5443,
    "total_budget": 8000,
    "sections": [
      { "name": "metadata", "used_chars": 2223, "max_chars": 3000, "dropped_items": 2, "truncated": true }
    ]
  },
  "analysis_steps": [
    { "title": "检索相似示例（RAG）", "detail": "1. 相似问题：…", "kind": "prompt" },
    { "title": "生成并执行只读 SQL", "detail": "SELECT …", "kind": "sql" },
    { "title": "获取查询结果", "detail": "返回 2 行，列：production_line、avg_defect_rate", "kind": "result" },
    { "title": "生成分析结论", "detail": "## 结论…", "kind": "report" },
    { "title": "生成图表配置", "detail": "bar · 不同生产线之间做横向对比…", "kind": "chart" }
  ],
  "metric_bindings": [],
  "error": null,
  "sql_error": null
}
```

字段语义要点：

| 字段 | 说明 |
|---|---|
| `task_type` | `sql_query` / `report` / `anomaly` / `regression` |
| `rows` | **二维数组**，与 `columns` 按下标对应 |
| `chart_config.option` | 可直接喂给 ECharts 的原始配置；结构由大模型产出，**前端不要强类型化** |
| `chart_config.source` | `llm`（大模型生成）或 `fallback`（规则兜底） |
| `chart_config.chart_type` | `bar`/`line`/`pie`/`scatter`/`table`；为 `table` 时 `option` 为空对象，前端应改用表格渲染 |
| `sql_error` | 结果**回放取数**失败原因；此时 `analysis_text` 仍然有效，不要整页报错 |
| `prompt_usage` | 四段 Prompt 的预算用量，可用于展示「分析过程」 |
| `analysis_steps[].kind` | `prompt`/`sql`/`result`/`report`/`chart`/`model`，前端据此选渲染方式 |

> **图表为何由大模型产出**：题目要求「支持可视化输出」，图表类型需贴合语义
> （趋势→折线、占比→饼图、对比→柱状）。硬编码规则难以覆盖，因此由模型产出
> ECharts option，前端只渲染。模型输出非法 JSON 或异常时，后台会回退到
> 确定性启发式规则，**保证前端一定有图可画**。

### POST /api/v1/agent/report

生成 Markdown 报告。请求体同上，内部强制 `wantReport=true`。
响应中 `task_type` 为 `report`，`report` 字段为 Markdown 正文。

### POST /api/v1/agent/ask/async（推荐）

异步提交问答，**立即返回**，避免超时。

```json
{ "code": 1, "data": { "job_id": "399eee7a4664440f9775d0f2fc265468", "status": "pending" } }
```

### GET /api/v1/agent/jobs/{job_id}

轮询任务状态与结果。

```json
{
  "code": 1,
  "data": {
    "job_id": "399eee7a4664440f9775d0f2fc265468",
    "status": "succeeded",
    "created_at": 1757856000.12,
    "finished_at": 1757856094.45,
    "elapsed_ms": 94329,
    "progress": 100,
    "stage": "分析完成",
    "result": { "…AskResponse…": "" },
    "error": null
  }
}
```

`status`：`pending` → `running` → `succeeded` / `failed`。
任务不存在时返回 HTTP `404`。

> 任务表是**进程内**的（`JobStore`，上限 200 个，自动淘汰最旧的已完成任务）。
> 生产环境如需多实例部署，应换成 Redis。

### GET /api/v1/agent/jobs/{job_id}/stream（SSE）

以 Server-Sent Events 推送任务状态，避免前端忙轮询。

```text
event: progress
data: {"job_id":"399e…","status":"running","progress":20,"stage":"正在检索业务知识与相似示例…"}

event: done
data: {"job_id":"399e…","status":"succeeded","progress":100,"stage":"分析完成","result":{…}}
```

- 事件名只有 `progress` 与 `done` 两种；
- `done` 表示终态（`succeeded` 或 `failed`），前端收到后应关闭连接；
- 服务端设置了 `X-Accel-Buffering: no`，避免 Nginx 缓冲导致事件延迟；
- 最长推送 10 分钟；前端应实现**轮询兜底**（见
  `FRONTEND/src/api/client.ts` 的 `subscribeJob`）。

### DELETE /api/v1/agent/session/{session_id}

清空指定会话的多轮历史，对应 CLI 里的 `new`。

```json
{ "code": 1, "data": { "session_id": "web-abc123", "reset": true } }
```

---

## 5. 机器建模

> 已实现的算法（对应题目「建模能力」的要求）：
> **Isolation Forest 异常检测、线性回归、决策树、随机森林、逻辑回归、KMeans 聚类**。
>
> 接口分两组：
> - **统一入口** `POST /modeling/train` + `GET /modeling/algorithms` —— 推荐，
>   新增算法只需在上游 `ALGORITHM_CATALOG` 加一条，前端自动出现入口；
> - **专用接口** `/modeling/anomaly`、`/modeling/regression` —— 早期为便于调试开放，
>   最终调用同一批建模函数，结果结构一致，保留以兼容既有调用。

### GET /api/v1/modeling/algorithms

列出全部算法及元信息，供前端渲染算法卡片与参数表单。

```json
{
  "code": 1,
  "data": {
    "total": 6,
    "table": "fact_production_record",
    "algorithms": [
      {
        "name": "decision_tree",
        "label": "决策树 · DecisionTree",
        "family": "supervised",
        "supervised": true,
        "task_type": "回归 / 分类",
        "requires_target": true,
        "supports_task_auto": true,
        "params": ["target", "features", "maxDepth", "testSize", "taskType"],
        "description": "可解释性最强：给出特征重要性与决策规则，按目标列自动判定分类或回归。"
      }
    ]
  }
}
```

| 算法 `name` | 任务类型 | 需要 target | 可调参数 |
|---|---|---|---|
| `isolation_forest` | 异常检测 | 否 | `contamination`、`features`、`limit` |
| `linear_regression` | 回归 | 是 | `target`、`features`、`testSize`、`limit` |
| `decision_tree` | 回归 / 分类（自动判定） | 是 | `maxDepth`、`taskType` 等 |
| `random_forest` | 回归 / 分类（自动判定） | 是 | `nEstimators`、`maxDepth`、`taskType` 等 |
| `logistic_regression` | 二分类 | 是 | `threshold`、`testSize` 等 |
| `kmeans` | 聚类 | 否 | `nClusters`、`maxK`、`features` |

### POST /api/v1/modeling/train

统一建模入口。

请求：

```json
{
  "algorithm": "random_forest",
  "target": "defect_rate",
  "features": ["quality_score", "downtime_minutes"],
  "limit": 10000,
  "nEstimators": 100,
  "maxDepth": null,
  "testSize": 0.2
}
```

| 字段 | 说明 |
|---|---|
| `algorithm` | 必填，取上面的 `name` |
| `target` | 有监督算法必填；KMeans / 异常检测不使用 |
| `features` | 不传则用该算法的默认特征集 |
| `maxDepth` | 树深度上限（决策树默认 5；随机森林默认不限） |
| `nEstimators` | 随机森林树数量，默认 100 |
| `taskType` | 强制 `classification` / `regression`，不传则按目标列取值个数自动判定 |
| `threshold` | 逻辑回归二分阈值，不传取目标列中位数 |
| `nClusters` / `maxK` | KMeans 聚类数 / 自动选 k 上限 |

**参数命名**：camelCase 与 snake_case 都接受（上游为这些字段声明了 camelCase 别名）。

**任务类型自动判定**：目标列去重后取值个数 ≤ 15 视为分类，否则为回归。
例如 `fault_event_count`（0/1/2/3）会被判为分类，`defect_rate` 判为回归。

**回归 / 分类响应**（决策树、随机森林）：

```json
{
  "code": 1,
  "data": {
    "algorithm": "RandomForest",
    "task_type": "回归预测建模",
    "model_task": "regression",
    "target": "defect_rate",
    "feature_columns": ["quality_score", "first_pass_yield"],
    "train_size": 8000,
    "test_size": 2000,
    "params": { "max_depth": null, "n_estimators": 100, "random_state": 42 },
    "r2_score": 0.7714,
    "rmse": 0.3685,
    "feature_importance": { "quality_score": 0.7021, "first_pass_yield": 0.135 },
    "importance_summary": "quality_score(0.7021)、first_pass_yield(0.1350)",
    "sample_predictions": [
      { "record_id": 4032, "machine_id": "M17", "production_line": "Line_A",
        "shift": "Night", "actual": 3.91, "predict": 3.95 }
    ]
  }
}
```

分类任务把 `r2_score`/`rmse` 换成 `accuracy` / `precision_macro` /
`recall_macro` / `f1_macro` / `classes` / `class_distribution`。

`feature_importance` 按降序排列且归一化到 1（可用作特征筛选依据）。

**逻辑回归响应**（额外字段）：

| 字段 | 说明 |
|---|---|
| `label_definition` | 两个类别的文字定义，如 `["<= 3.9041", "> 3.9041"]` |
| `label_derived` | `true` 表示目标列非天然二分类，已按阈值二分 |
| `threshold` | 实际使用的阈值 |
| `coefficients` | 标准化后的系数，按绝对值降序，可直接比较重要性 |
| `scaled` | 恒为 `true`，表示特征已标准化 |
| `sample_predictions[].probability` | 正类概率 |

**KMeans 响应**：

```json
{
  "code": 1,
  "data": {
    "algorithm": "KMeans",
    "task_type": "聚类分析",
    "model_task": "clustering",
    "feature_columns": ["defect_rate", "quality_score"],
    "total_records": 9500,
    "n_clusters": 3,
    "auto_selected": true,
    "silhouette": 0.1488,
    "candidates": [{ "k": 2, "silhouette": 0.1488, "inertia": 45000.0, "calinski_harabasz": 1200.0 }],
    "clusters": [
      { "cluster": 0, "size": 2101, "ratio": 0.37,
        "centroid": { "defect_rate": 3.288 },
        "dominant_machine_id": "M08", "dominant_production_line": "Line_D" }
    ],
    "cluster_profile": [{ "cluster": 0, "defect_rate": 3.288 }],
    "overall_mean": { "defect_rate": 3.8961 },
    "sample_records": []
  }
}
```

- `auto_selected: true` 表示未指定 `nClusters`，后端在 2..6 间按轮廓系数择优；
- `candidates` 给出每个候选 k 的评分，前端可展示「如何选 k」；
- `clusters[].dominant_*` 是该簇出现最多的上下文字段取值，用于描述「这是个什么簇」；
- `cluster_profile` 是各簇的特征均值，与 `overall_mean` 对比即可看出簇间差异。

**错误返回**（`code=0`，均为可读中文）：

| 场景 | `msg` 示例 |
|---|---|
| 未知算法 | `不支持的算法 'xgboost'，可选：isolation_forest, linear_regression, ...` |
| 有监督算法缺 target | `随机森林 · RandomForest 必须提供 target（目标列）` |
| 目标列/特征列不存在 | `目标列不存在: no_such_column` |
| 少数类过少 | `少数类样本过少（4 条），逻辑回归结果不可靠，请调整阈值或更换目标列` |
| 有效样本不足 | `有效数据太少，无法训练模型（至少需要 30 条有效样本）` |

> **建模只查询一张表**（默认 `fact_production_record`）。
> 数据库里可能同时存在多个数据源（例如客户原始表用 `mot_t`/`def_rate` 这类缩写列名），
> 因此 `/modeling/features` 只返回**建模表**的字段，避免用户选中不属于该表的列。

### POST /api/v1/modeling/anomaly

Isolation Forest 异常检测（专用接口）。

请求：

```json
{ "features": ["defect_rate", "downtime_minutes"], "contamination": 0.05, "limit": 10000 }
```

`features` 为 `null` 时使用内置默认特征集。

响应：

```json
{
  "code": 1,
  "data": {
    "task_type": "异常检测",
    "algorithm": "IsolationForest",
    "total_records": 10000,
    "anomaly_count": 500,
    "anomaly_ratio": 0.05,
    "feature_columns": ["defect_rate", "downtime_minutes"],
    "records": [ { "machine_id": "M07", "defect_rate": 9.1, "anomaly_score": -0.21 } ]
  }
}
```

`records` 已包含上下文字段（`record_id`/`machine_id`/`production_line`/
`batch_id`/`shift`/`product_type`）与 `anomaly_score`（越低越异常）。

### POST /api/v1/modeling/regression

线性回归训练与评估（专用接口）。

请求：

```json
{ "target": "defect_rate", "features": null, "limit": 10000, "testSize": 0.2 }
```

响应：

```json
{
  "code": 1,
  "data": {
    "task_type": "回归预测建模",
    "algorithm": "LinearRegression",
    "target": "defect_rate",
    "feature_columns": ["quality_score", "downtime_minutes"],
    "train_size": 8000,
    "test_size": 2000,
    "r2_score": 0.7612,
    "rmse": 0.3512,
    "coefficients": { "quality_score": -0.12 },
    "sample_predictions": [ { "actual": 2.75, "predict": 2.81 } ]
  }
}
```

### GET /api/v1/modeling/features?role=numeric

列出可用于建模的字段，供前端下拉/多选。

```json
{
  "code": 1,
  "data": {
    "fields": [
      { "table": "fact_production_record", "name": "defect_rate", "type": "DOUBLE",
        "description": "缺陷率百分比", "numeric": true }
    ],
    "total": 39,
    "table": "fact_production_record",
    "default_features": {
      "anomaly": ["defect_rate", "quality_score"],
      "regression": ["quality_score", "first_pass_yield"]
    },
    "note": "建模模块只查询 fact_production_record 表；如需分析其他数据源，请先在 mapping.yaml 中完成字段映射。"
  }
}
```

`role=numeric` 只返回数值字段；省略则返回全部。`default_features` 供前端预选，
避免默认全选导致 `record_id` 这类无意义字段进入模型。

---

## 6. 前端页面与接口对应

| 页面 | 路由 | 使用接口 |
|---|---|---|
| 总览 | `/` | `/system/health`、`/metadata/tables`、`/knowledge/overview` |
| 智能问答 | `/chat` | `POST /agent/ask/async`、`/agent/jobs/{id}`、`/agent/jobs/{id}/stream` |
| 数据资源 | `/metadata` | `/metadata/tables`、`/metadata/relationships` |
| 业务知识 | `/knowledge` | `/knowledge/overview`、`/knowledge/graph` |
| 机器建模 | `/modeling` | `/modeling/features`、`POST /modeling/anomaly`、`POST /modeling/regression` |
| 分析报告 | `/report` | `POST /agent/report` |

---

## 7. 实现位置索引

| 能力 | Python（agent/server/） | Java（backend/） | 前端（FRONTEND/src/） |
|---|---|---|---|
| 统一信封 | `schemas.py` 的 `ok()` / `fail()` | `com.agent.result.Result` | `api/client.ts` 的 `unwrap` |
| 内核结果转换 | `schemas.py` 的 `to_ask_response()` | 透传 | `api/types.ts` |
| 异步任务 | `deps.py` 的 `JobStore` | 转发 | `hooks/useAsk.ts` |
| SSE | `routes.py` 的 `agent_job_stream` | `SseEmitter` 代理 | `api/client.ts` 的 `subscribeJob` |
| 图表生成 | `agents/chart_agent.py` | 透传 | `components/EChart.tsx` |
| 知识图谱 | `deps.py` 的 `build_knowledge_graph` | 透传 | `pages/KnowledgePage.tsx` |

---

## 8. 已知约束与后续改进

- **单表宽表模型**：`dim_*` 维表已删除，`/metadata/relationships` 为空，
  图谱改用逻辑图谱。若后续恢复维表，需同步调整图谱组装与前端图例。
- **任务表为进程内存储**：多实例部署需替换为 Redis。
- **`metric_bindings` 可能为空**：仅在配置了 `ANALYSIS_MAPPING`（客户
  `mapping.yaml`）时才有内容；用内置单表标准模型时字段说明来自 DDL `COMMENT`。
- **SSE 进度粒度较粗**：内核 `agent.invoke()` 是同步阻塞的，无法在工具循环内
  上报细粒度进度，因此进度是阶段式的（20% → 85% → 100%）。
  前端已用实测计时补偿。
- **无鉴权**：当前未实现登录与权限，题目未要求；如需接入，应在 Java 网关加
  JWT 拦截器（可参照苍穹外卖的 `JwtTokenAdminInterceptor`）。
