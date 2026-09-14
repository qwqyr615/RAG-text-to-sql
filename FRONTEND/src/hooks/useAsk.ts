/**
 * 自然语言问析的共享状态逻辑。
 *
 * 为什么抽成 hook：智能问答页、总览页、报告页都需要同一套
 * 「异步提交 → SSE/轮询订阅 → 拿到结果」流程，且必须统一处理：
 *  - 一次提问可能耗时 90 秒以上（实测最慢 94 秒），必须有进度反馈与计时；
 *  - 组件卸载要取消订阅，否则 EventSource 泄漏；
 *  - 业务失败（success=false）与请求失败（ApiError）要区分展示。
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { api, subscribeJob, type JobSubscription } from '../api/client'
import type { AskResponse, JobState } from '../api/types'

export interface AskOptions {
  question: string
  sessionId?: string
  wantChart?: boolean
  wantReport?: boolean
}

export interface UseAskResult {
  /** 是否正在分析 */
  loading: boolean
  /** 当前阶段描述（来自后端任务状态） */
  stage: string
  /** 后端上报的进度百分比 */
  progress: number
  /** 前端实测耗时（秒），因为后端进度是阶段式的，实测秒数更直观 */
  elapsed: number
  /** 分析结果 */
  result: AskResponse | null
  /** 请求层错误（连不上后端、超时等） */
  error: string | null
  /** 提交问题 */
  ask: (options: AskOptions) => Promise<void>
  /** 中断当前分析（停止订阅，后端任务会在后台自然结束） */
  cancel: () => void
  /** 清空结果 */
  reset: () => void
}

export function useAsk(defaultSessionId = 'web-default'): UseAskResult {
  const [loading, setLoading] = useState(false)
  const [stage, setStage] = useState('')
  const [progress, setProgress] = useState(0)
  const [elapsed, setElapsed] = useState(0)
  const [result, setResult] = useState<AskResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const subscriptionRef = useRef<JobSubscription | null>(null)
  const timerRef = useRef<number | null>(null)
  const mountedRef = useRef(true)

  /** 清理订阅与计时器 */
  const cleanup = useCallback(() => {
    subscriptionRef.current?.close()
    subscriptionRef.current = null
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      cleanup()
    }
  }, [cleanup])

  /** 处理任务状态变化 */
  const handleState = useCallback((state: JobState) => {
    if (!mountedRef.current) return
    setStage(state.stage || '')
    setProgress(state.progress ?? 0)

    if (state.status === 'succeeded' || state.status === 'failed') {
      cleanup()
      setLoading(false)
      setProgress(100)

      if (state.status === 'failed' && !state.result) {
        setError(state.error || '分析失败')
        return
      }
      // result 里带着 success 标记，业务失败也走这里，由页面同时展示结论与错误
      setResult(state.result ?? null)
      if (state.result && !state.result.success) {
        setError(state.result.error || '分析失败')
      }
    }
  }, [cleanup])

  const ask = useCallback(
    async ({ question, sessionId, wantChart = true, wantReport = false }: AskOptions) => {
      const trimmed = question.trim()
      if (!trimmed) {
        setError('请输入分析问题')
        return
      }

      cleanup()
      setLoading(true)
      setError(null)
      setResult(null)
      setProgress(0)
      setStage('正在提交分析任务…')
      setElapsed(0)

      const startedAt = Date.now()
      timerRef.current = window.setInterval(() => {
        if (mountedRef.current) setElapsed(Math.floor((Date.now() - startedAt) / 1000))
      }, 1000)

      try {
        const submit = await api.askAsync({
          question: trimmed,
          sessionId: sessionId ?? defaultSessionId,
          wantChart,
          wantReport,
        })
        if (!mountedRef.current) return

        setStage('正在检索业务知识与相似示例…')
        subscriptionRef.current = subscribeJob(
          submit.job_id,
          handleState,
          (subscriptionError) => {
            if (!mountedRef.current) return
            cleanup()
            setLoading(false)
            setError(subscriptionError.message)
          },
        )
      } catch (submitError) {
        if (!mountedRef.current) return
        cleanup()
        setLoading(false)
        setError(submitError instanceof Error ? submitError.message : String(submitError))
      }
    },
    [cleanup, defaultSessionId, handleState],
  )

  const cancel = useCallback(() => {
    cleanup()
    setLoading(false)
    setStage('已取消订阅（后端任务仍在后台执行）')
  }, [cleanup])

  const reset = useCallback(() => {
    cleanup()
    setResult(null)
    setError(null)
    setProgress(0)
    setStage('')
    setElapsed(0)
    setLoading(false)
  }, [cleanup])

  return { loading, stage, progress, elapsed, result, error, ask, cancel, reset }
}
