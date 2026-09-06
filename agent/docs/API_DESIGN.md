# 接口预留说明（图谱与页面阶段）

当前阶段先定义后端需要提供给前端的接口契约。
具体 Java/Spring Boot 或 FastAPI 实现待 Agent 搭建完成后补齐。

## 总体架构

```text
Vue/React 页面
    ↓ HTTP JSON
Spring Boot / FastAPI
    ↓
Python Agent 服务
    ↓
metadata_service
knowledge_service
EnterpriseAgent
MySQL
```

## 基础约定

- 基础路径：`/api/v1`
- 数据格式：`application/json`
- 统一错误返回：

```json
{
  "code": 500,
  "message": "错误描述"
}
```

## 1. 数据资源理解接口

### GET /api/v1/metadata/tables

获取所有业务表、字段、字段类型、字段说明、样例值。

响应示例：

```json
{
  "schema_version": "1.0",
  "tables": [
    {
      "table_name": "fact_production_record",
      "description": "生产记录事实表",
      "columns": [
        {
          "name": "defect_rate",
          "type": "DOUBLE",
          "nullable": true,
          "primary_key": false,
          "description": "缺陷率",
          "sample_value": 2.75
        }
      ]
    }
  ]
}
```

对应 Python 方法：

```python
from metadata.metadata_service import get_metadata_json
```

### GET /api/v1/metadata/relationships

获取表间关系，供前端关系图/知识图谱使用。

```json
{
  "relationships": [
    {
      "source_table": "dim_machine",
      "source_column": "line_id",
      "target_table": "dim_line",
      "target_column": "line_id",
      "relation_type": "many-to-one",
      "description": "多台设备属于同一条生产线"
    }
  ]
}
```

对应 Python 方法：

```python
from metadata.metadata_service import get_metadata_json
```

## 2. 业务知识接口

### GET /api/v1/knowledge/overview

获取主题、对象、规则的整体知识结构，给前端知识图谱页面使用。

```json
{
  "themes": [
    {
      "code": "quality_analysis",
      "name": "质量分析",
      "description": "...",
      "related_tables": ["fact_production_record"]
    }
  ],
  "objects": [
    {
      "name": "产品",
      "default_table": "dim_product",
      "key_field": "product_type"
    }
  ],
  "rules": [
    {
      "name": "缺陷率",
      "mapped_table": "fact_production_record",
      "mapped_field": "defect_rate"
    }
  ]
}
```

对应 Python 方法：

```python
from knowledge.knowledge_service import resolve_knowledge
```

### GET /api/v1/knowledge/graph

预留：返回知识图谱节点和边。

节点类型建议：

- `主题`
- `业务对象`
- `数据表`
- `字段`
- `指标/规则`

边类型建议：

- `主题包含对象`
- `对象关联表`
- `表包含字段`
- `指标映射字段`

建议后端由 `knowledge_service.resolve_knowledge()` + `metadata_service.get_metadata_json()` 组合生成。

## 3. Agent 问答接口

### POST /api/v1/agent/ask

普通 SQL 查询、统计分析、指标分析。

请求：

```json
{
  "question": "统计不同生产线的平均缺陷率",
  "session_id": "user-001",
  "metadata": "",
  "business_rules": ""
}
```

响应：

```json
{
  "success": true,
  "question": "统计不同生产线的平均缺陷率",
  "sql": "SELECT ...",
  "columns": ["production_line", "avg_defect_rate"],
  "rows": [["Line_A", 3.9]],
  "analysis_text": "各生产线平均缺陷率约为..."
}
```

对应 Python 方法：

```python
from agents.enterprise_agent import EnterpriseAgent

agent = EnterpriseAgent()
result = agent.sql_agent.ask({
    "question": "统计不同生产线的平均缺陷率"
})
```

### POST /api/v1/agent/report

生成分析报告。

请求：

```json
{
  "question": "生成一份质量分析报告"
}
```

响应：

```json
{
  "success": true,
  "report": "## 质量分析报告..."
}
```

对应 Python 方法：

```python
result = agent.sql_agent.ask_with_report({
    "question": "生成一份质量分析报告"
})
```

### POST /api/v1/agent/anomaly

执行异常检测。

响应：

```json
{
  "task_type": "异常检测",
  "algorithm": "IsolationForest",
  "total_records": 10000,
  "anomaly_count": 500,
  "records": []
}
```

### POST /api/v1/agent/regression

执行简单回归预测。

请求：

```json
{
  "target": "defect_rate"
}
```

响应：

```json
{
  "task_type": "回归预测建模",
  "algorithm": "LinearRegression",
  "r2_score": 0.76,
  "rmse": 0.35,
  "coefficients": {}
}
```

对应 Python 统一路由：

```python
response = agent.handle(question)
# response["type"] == "agent" 或 "model"
```

## 4. 前端页面预留

### 页面规划

| 页面 | 接口 |
|---|---|
| 数据资源页 | GET /api/v1/metadata/tables、GET /api/v1/metadata/relationships |
| 知识图谱页 | GET /api/v1/knowledge/overview、GET /api/v1/knowledge/graph |
| 智能问答页 | POST /api/v1/agent/ask、POST /api/v1/agent/report |
| 分析结果页 | 复用 agent 返回的 columns / rows / report |

## 后续完善内容

- 增加 Spring Boot Controller 或 FastAPI Router
- 增加前端路由与页面
- 增加知识图谱可视化库，例如 ECharts Graph / AntV G6
- 增加权限、会话管理、历史记录
