/**
 * 后端 API 客户端。
 *
 * 约定：
 *  - 统一解包 `{code, msg, data}` 信封，`code !== 1` 时抛 `ApiError(msg)`，
 *    因此调用方只需 try/catch 拿到可直接展示给用户的中文错误。
 *  - 所有请求走 `/api/v1` 前缀，由 Vite devServer 代理到 Java 网关（:8080），
 *    生产环境由 Nginx 承担同样职责。
 *  - 长耗时问答使用「异步提交 + SSE 订阅」，并提供轮询兜底：
 *    实测一次提问可能耗时 90 秒以上，同步 fetch 必然超时。
 */

import {
  ApiError,
  type AnomalyRequest,
  type AnomalyResult,
  type ApiEnvelope,
  type AskRequest,
  type AskResponse,
  type HealthInfo,
  type JobState,
  type JobSubmit,
  type KnowledgeGraph,
  type KnowledgeOverview,
  type MetadataPayload,
  type ModelFeature,
  type RegressionRequest,
  type RegressionResult,
  type Relationship,
  type StandardField,
  type MetricBinding,
} from './types'

const BASE = '/api/v1'

/** 统一解包信封 */
async function unwrap<T>(response: Response): Promise<T> {
  if (!response.ok) {
    // 404/500 等由网关直接返回，尽力取出可读信息
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      detail = body?.detail || body?.msg || body?.message || detail
    } catch {
      /* 响应体不是 JSON，保留状态码描述 */
    }
    throw new ApiError(`请求失败：${detail}`, response.status)
  }

  let envelope: ApiEnvelope<T>
  try {
    envelope = (await response.json()) as ApiEnvelope<T>
  } catch {
    throw new ApiError('响应不是合法 JSON，请确认后端服务是否正常')
  }

  if (envelope.code !== 1) {
    throw new ApiError(envelope.msg || '后端返回失败，但未提供错误信息', envelope.code)
  }
  return envelope.data as T
}

async function get<T>(path: string, timeoutMs = 30_000): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${BASE}${path}`, { signal: controller.signal })
    return await unwrap<T>(response)
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(`请求超时（${Math.round(timeoutMs / 1000)}s）：${path}`)
    }
    throw error
  } finally {
    clearTimeout(timer)
  }
}

async function post<T>(path: string, body: unknown, timeoutMs = 180_000): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    return await unwrap<T>(response)
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(
        `请求超时（${Math.round(timeoutMs / 1000)}s）。该问题可能过于复杂，` +
          '可尝试缩小分析范围，或确认 Agent 服务是否正常。',
      )
    }
    // fetch 抛 TypeError 通常意味着连不上后端（Java 或 FastAPI 未启动）
    if (error instanceof TypeError) {
      throw new ApiError(
        '无法连接后端服务。请确认 FastAPI（:8000）与 Java 网关（:8080）均已启动。',
      )
    }
    throw error
  } finally {
    clearTimeout(timer)
  }
}

// ---------------------------------------------------------------------------
// 对外接口
// ---------------------------------------------------------------------------
export const api = {
  /** 健康检查 */
  health: () => get<HealthInfo>('/system/health', 10_000),

  /** 数据资源：表 / 字段 / 样例值 */
  metadataTables: () => get<MetadataPayload>('/metadata/tables'),

  /** 表间关系（单表模型下通常为空） */
  metadataRelationships: () =>
    get<{ relationships: Relationship[]; database_type: string; note: string }>(
      '/metadata/relationships',
    ),

  /** 标准字段口径映射 */
  metadataFieldMap: () =>
    get<{
      field_map: Record<string, StandardField[]>
      metric_bindings: MetricBinding[]
      unmatched_metrics: string[]
      mapping_profile: string
    }>('/metadata/field-map'),

  /** 业务知识总览 */
  knowledgeOverview: () => get<KnowledgeOverview>('/knowledge/overview'),

  /** 知识图谱节点与边 */
  knowledgeGraph: () => get<KnowledgeGraph>('/knowledge/graph'),

  /** 同步问答（最长 90s，仅供脚本/调试使用） */
  ask: (request: AskRequest) => post<AskResponse>('/agent/ask', request),

  /** 生成报告 */
  report: (request: AskRequest) =>
    post<AskResponse>('/agent/report', { ...request, wantReport: true }),

  /** 异步提交问答，立即返回 job_id */
  askAsync: (request: AskRequest) => post<JobSubmit>('/agent/ask/async', request, 30_000),

  /** 查询任务状态与结果 */
  job: (jobId: string) => get<JobState>(`/agent/jobs/${jobId}`, 15_000),

  /** 清空会话历史 */
  resetSession: async (sessionId: string) => {
    const response = await fetch(`${BASE}/agent/session/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
    })
    return unwrap<{ session_id: string; reset: boolean }>(response)
  },

  /** 异常检测 */
  anomaly: (request: AnomalyRequest) => post<AnomalyResult>('/modeling/anomaly', request),

  /** 线性回归建模 */
  regression: (request: RegressionRequest) =>
    post<RegressionResult>('/modeling/regression', request),

  /** 可用建模字段 */
  modelingFeatures: (role?: 'numeric') =>
    get<{ fields: ModelFeature[]; total: number }>(
      `/modeling/features${role ? `?role=${role}` : ''}`,
    ),
}

// ---------------------------------------------------------------------------
// 任务订阅：SSE 优先，轮询兜底
// ---------------------------------------------------------------------------
export interface JobSubscription {
  /** 主动终止订阅（组件卸载时调用） */
  close: () => void
}

/**
 * 订阅一个异步问答任务。
 *
 * 优先使用 SSE（`/jobs/{id}/stream`）以获得即时的进度推送；
 * 若浏览器不支持或连接失败，自动降级为定时轮询 `/jobs/{id}`。
 *
 * @param jobId     任务 ID
 * @param onUpdate  每次状态变化时回调（含终态）
 * @param onError   订阅层错误（业务失败通过 onUpdate 的 status='failed' 传递）
 */
export function subscribeJob(
  jobId: string,
  onUpdate: (state: JobState) => void,
  onError: (error: Error) => void,
): JobSubscription {
  let closed = false
  let source: EventSource | null = null
  let pollTimer: number | null = null
  let pollFailures = 0

  const stop = () => {
    closed = true
    if (source) {
      source.close()
      source = null
    }
    if (pollTimer !== null) {
      window.clearInterval(pollTimer)
      pollTimer = null
    }
  }

  const startPolling = () => {
    if (closed || pollTimer !== null) return
    pollTimer = window.setInterval(async () => {
      if (closed) return
      try {
        const state = await api.job(jobId)
        pollFailures = 0
        onUpdate(state)
        if (state.status === 'succeeded' || state.status === 'failed') stop()
      } catch (error) {
        pollFailures += 1
        // 连续失败才上报，避免抖动误报
        if (pollFailures >= 3) {
          onError(error instanceof Error ? error : new Error(String(error)))
          stop()
        }
      }
    }, 1500)
  }

  try {
    source = new EventSource(`${BASE}/agent/jobs/${jobId}/stream`)

    source.addEventListener('progress', (event) => {
      try {
        onUpdate(JSON.parse((event as MessageEvent).data) as JobState)
      } catch {
        /* 忽略畸形帧，等待下一帧 */
      }
    })

    source.addEventListener('done', (event) => {
      try {
        onUpdate(JSON.parse((event as MessageEvent).data) as JobState)
      } catch (error) {
        onError(error instanceof Error ? error : new Error(String(error)))
      }
      stop()
    })

    source.onerror = () => {
      // SSE 断开：若任务未结束，降级轮询继续拿结果
      if (closed) return
      if (source && source.readyState === EventSource.CLOSED) {
        source.close()
        source = null
        startPolling()
      }
    }
  } catch {
    // 构造 EventSource 失败，直接走轮询
    startPolling()
  }

  return { close: stop }
}
