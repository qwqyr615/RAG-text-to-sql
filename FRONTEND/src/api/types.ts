/**
 * 后端接口契约的 TypeScript 类型定义。
 *
 * 契约来源：Java 网关（Spring Boot，:8080）→ FastAPI 适配层（:8000）→ Agent 内核。
 * 所有接口统一信封 `{code, msg, data}`，`code === 1` 表示成功。
 *
 * 字段命名刻意保持与后端返回一致的 **snake_case**，不做驼峰转换——
 * 这样前端能直接对照 Swagger/接口文档排查问题，也避免转换层引入的隐性 bug。
 */

/** 统一信封，与 Java `com.agent.result.Result` 字段一致 */
export interface ApiEnvelope<T> {
  code: number
  msg: string
  data: T | null
}

/** 后端返回失败时抛出的错误，携带后端可读信息 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: number = 0,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

// ---------------------------------------------------------------------------
// 系统
// ---------------------------------------------------------------------------
export interface HealthInfo {
  status: string
  agent_ready: boolean
  database_type: string
  table_count: number
  llm_model: string
  rag_enabled: boolean
  error: string | null
}

// ---------------------------------------------------------------------------
// 数据资源理解
// ---------------------------------------------------------------------------
export interface ColumnInfo {
  name: string
  type: string
  nullable: boolean
  default: unknown
  primary_key: boolean
  description: string
  standard_fields: string[]
  sample_value: unknown
}

export interface TableInfo {
  table_name: string
  description: string
  role: string
  grain: string
  primary_key: string
  time_column: string
  columns: ColumnInfo[]
  sample_rows: Record<string, unknown>[]
  row_count: number
}

export interface Relationship {
  source_table: string
  source_column: string
  target_table: string
  target_column: string
  relation_type: string
  description?: string
}

export interface MetadataPayload {
  schema_version: string
  database_type: string
  mapping_profile: string
  mapping_source: string
  tables: TableInfo[]
  relationships: Relationship[]
  field_map: Record<string, StandardField[]>
  metric_bindings: MetricBinding[]
  unmatched_metrics: string[]
  inventory?: {
    all_tables: string[]
    business_tables: string[]
    excluded: Record<string, string> | string[]
    roles: Record<string, string>
  }
}

export interface StandardField {
  standard_field: string
  label: string
  column: string
  expression: string
  unit: string
  customer_unit: string
  scale: number | null
  data_kind: string
  group: string
  enum_values: string[]
  needs_conversion: boolean
}

export interface MetricBinding {
  standard_field?: string
  label?: string
  column?: string
  expression?: string
  unit?: string
  [key: string]: unknown
}

// ---------------------------------------------------------------------------
// 业务知识
// ---------------------------------------------------------------------------
export interface KnowledgeTheme {
  code: string
  name: string
  description: string
  related_tables: string[]
}

export interface KnowledgeObject {
  name: string
  default_table: string
  key_field: string
  description?: string
}

export interface KnowledgeRule {
  name: string
  mapped_table: string | null
  mapped_field: string | null
  resolved_calculation: string
  calculation?: string
  candidate_fields?: string[]
  prefer_table?: string
  [key: string]: unknown
}

export interface KnowledgeOverview {
  schema_version: string
  themes: KnowledgeTheme[]
  objects: KnowledgeObject[]
  rules: KnowledgeRule[]
  graph?: KnowledgeGraph
}

export interface GraphNode {
  id: string
  name: string
  category: string
  description?: string
  resolved?: boolean
  expression?: string
  table?: string
  key_field?: string
  role?: string
}

export interface GraphEdge {
  source: string
  target: string
  label: string
}

export interface KnowledgeGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  categories: { name: string }[]
}

// ---------------------------------------------------------------------------
// 智能问答
// ---------------------------------------------------------------------------
export type TaskType = 'sql_query' | 'report' | 'anomaly' | 'regression'
export type StepKind = 'prompt' | 'sql' | 'result' | 'report' | 'chart' | 'model'

export interface AnalysisStep {
  title: string
  detail: string
  kind: StepKind
}

export interface ChartConfig {
  chart_type: string
  title: string
  /** ECharts option 原始配置，结构由后端大模型产出，前端只负责渲染 */
  option: Record<string, unknown>
  reason: string
  source: string
}

export interface PromptSectionUsage {
  name: string
  used_chars: number
  max_chars: number
  dropped_items?: number
  truncated?: boolean
  error?: string
}

export interface PromptUsage {
  used_chars: number
  total_budget: number
  sections: PromptSectionUsage[]
}

export interface AskResponse {
  task_type: TaskType
  success: boolean
  question: string
  session_id: string
  turns_used: number
  sql: string | null
  columns: string[]
  rows: unknown[][]
  row_count: number
  analysis_text: string
  report: string | null
  chart_config: ChartConfig | null
  rag_context: string | null
  prompt_usage: PromptUsage | null
  analysis_steps: AnalysisStep[]
  metric_bindings: MetricBinding[]
  error: string | null
  sql_error: string | null
}

export interface AskRequest {
  question: string
  sessionId: string
  metadata?: string
  businessRules?: string
  wantChart?: boolean
  wantReport?: boolean
}

export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed'

export interface JobState {
  job_id: string
  status: JobStatus
  created_at: number
  finished_at: number | null
  elapsed_ms: number | null
  progress: number
  stage: string
  result: AskResponse | null
  error: string | null
}

export interface JobSubmit {
  job_id: string
  status: JobStatus
}

// ---------------------------------------------------------------------------
// 建模
// ---------------------------------------------------------------------------
export interface ModelFeature {
  table: string
  name: string
  type: string
  description: string
  numeric: boolean
}

/** GET /modeling/features 的 data */
export interface ModelingFeatures {
  fields: ModelFeature[]
  total: number
  /** 建模模块实际查询的表（其他数据源的列不会出现在 fields 中） */
  table: string
  /** 后端在各算法下使用的默认特征集，前端可据此预选 */
  default_features: {
    anomaly: string[]
    regression: string[]
  }
  note: string
}

export interface AnomalyRequest {
  features?: string[] | null
  contamination?: number
  limit?: number | null
}

export interface AnomalyResult {
  task_type: string
  algorithm: string
  total_records: number
  anomaly_count: number
  anomaly_ratio: number
  feature_columns: string[]
  records: Record<string, unknown>[]
}

export interface RegressionRequest {
  target: string
  features?: string[] | null
  limit?: number | null
  testSize?: number
}

export interface RegressionResult {
  task_type: string
  algorithm: string
  target: string
  feature_columns: string[]
  train_size: number
  test_size: number
  r2_score: number
  rmse: number
  coefficients: Record<string, number>
  sample_predictions: Record<string, unknown>[]
}

// ---------------------------------------------------------------------------
// 统一建模（决策树 / 随机森林 / 逻辑回归 / KMeans）
// ---------------------------------------------------------------------------
export type AlgorithmFamily = 'anomaly' | 'supervised' | 'clustering'

/** GET /modeling/algorithms 的算法元信息 */
export interface AlgorithmInfo {
  name: string
  label: string
  family: AlgorithmFamily
  supervised: boolean
  task_type: string
  requires_target: boolean
  supports_task_auto: boolean
  params: string[]
  description: string
}

export interface AlgorithmsPayload {
  algorithms: AlgorithmInfo[]
  total: number
  table: string
}

/** POST /modeling/train 请求体 */
export interface TrainRequest {
  algorithm: string
  target?: string | null
  features?: string[] | null
  limit?: number | null
  testSize?: number
  maxDepth?: number | null
  nEstimators?: number | null
  taskType?: 'classification' | 'regression' | null
  threshold?: number | null
  nClusters?: number | null
  maxK?: number | null
}

/**
 * 建模结果。
 *
 * 不同算法的输出字段不同（`feature_importance` / `accuracy` / `clusters` /
 * `silhouette` …），因此这里用一个宽松类型，由结果渲染层按存在性分支处理。
 */
export interface TrainResult {
  algorithm: string
  task_type: string
  model_task?: string
  target?: string
  label_definition?: string[]
  label_derived?: boolean
  threshold?: number | null
  feature_columns: string[]
  train_size?: number
  test_size?: number
  params?: Record<string, unknown>
  scaled?: boolean

  // 回归
  r2_score?: number
  rmse?: number
  coefficients?: Record<string, number>
  intercept?: number

  // 分类
  accuracy?: number
  precision_macro?: number
  recall_macro?: number
  f1_macro?: number
  classes?: string[]
  class_distribution?: Record<string, number>

  // 树模型
  feature_importance?: Record<string, number>
  importance_summary?: string

  // 聚类
  n_clusters?: number
  auto_selected?: boolean
  silhouette?: number
  candidates?: { k: number; silhouette: number; inertia: number; calinski_harabasz: number }[]
  clusters?: {
    cluster: number
    size: number
    ratio: number
    centroid: Record<string, number>
    [key: string]: unknown
  }[]
  cluster_profile?: Record<string, unknown>[]
  overall_mean?: Record<string, number>
  total_records?: number

  sample_predictions?: Record<string, unknown>[]
  sample_records?: Record<string, unknown>[]
}
