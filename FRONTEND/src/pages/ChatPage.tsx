/**
 * 智能问答页 —— 系统的核心闭环。
 *
 * 交互设计参照 vanna 前端的对话式布局（`text-to-sql/frontends/webcomponent` 的
 * chat + progress tracker），视觉遵循 DESIGN.md 的 Cohere 设计系统。
 *
 * 页面刻意同时呈现「结果」与「过程」：
 *  - 结果：分析结论、ECharts 图表、数据表格、生成的 SQL；
 *  - 过程：analysis_steps 时间线、Prompt 各段预算用量、RAG 命中示例。
 * 因为题目的评分要点同时考察「展示分析过程」与「结果展示效果」。
 *
 * 长耗时提示：实测一次提问可能耗时 90 秒以上，因此必须给出
 * 阶段文案 + 实测计时，否则用户会以为页面卡死。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { AnalysisTimeline } from '../components/AnalysisTimeline'
import { DataTable } from '../components/DataTable'
import { EChart } from '../components/EChart'
import { Markdown } from '../components/Markdown'
import { PageHeader } from '../components/StateBlocks'
import { useAsk } from '../hooks/useAsk'
import type { AskResponse } from '../api/types'

/** 预置示例问题：覆盖 SQL 查询、趋势、异常、回归、报告五类任务 */
const PRESETS = [
  '请分析各工序的良率。',
  '统计不同生产线的平均缺陷率',
  '找出停机时间最长的 10 台设备',
  '统计每条产线最近 7 天的产量趋势',
  '找出最近一个月不良数量最高的产品',
  '找出异常数据',
  '用回归模型预测缺陷率',
  '生成一份质量分析报告',
]

type ResultTab = 'chart' | 'table' | 'sql' | 'steps' | 'report'

export function ChatPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [question, setQuestion] = useState('')
  const [sessionId] = useState(() => `web-${Date.now().toString(36)}`)
  const [tab, setTab] = useState<ResultTab>('chart')
  const [history, setHistory] = useState<AskResponse[]>([])
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  const { loading, stage, progress, elapsed, result, error, ask, cancel, reset } = useAsk(sessionId)

  // 从总览页的示例问题跳转过来时，自动填入并聚焦
  useEffect(() => {
    const preset = searchParams.get('q')
    if (preset) {
      setQuestion(preset)
      setSearchParams({}, { replace: true })
      inputRef.current?.focus()
    }
  }, [searchParams, setSearchParams])

  // 拿到新结果：归档到历史，并把默认 tab 切到最可能想看的内容
  useEffect(() => {
    if (!result) return
    setHistory((prev) => [result, ...prev].slice(0, 12))
    if (result.report) setTab('report')
    else if (result.chart_config && result.chart_config.chart_type !== 'table') setTab('chart')
    else if (result.columns.length) setTab('table')
    else setTab('steps')
  }, [result])

  const submit = (text?: string) => {
    const target = (text ?? question).trim()
    if (!target) return
    if (text) setQuestion(text)
    void ask({ question: target, sessionId })
  }

  const chartUsable = useMemo(
    () =>
      result?.chart_config != null &&
      result.chart_config.chart_type !== 'table' &&
      Object.keys(result.chart_config.option ?? {}).length > 0,
    [result],
  )

  return (
    <>
      <PageHeader
        eyebrow="NATURAL LANGUAGE ANALYTICS"
        title="智能问答"
        description="用自然语言描述分析需求，系统自动定位数据、对齐业务口径、生成只读 SQL 并完成图表与结论输出。"
        actions={
          <button
            type="button"
            className="btn btn-outlined"
            onClick={() => {
              reset()
              setHistory([])
            }}
            disabled={loading}
          >
            清空结果
          </button>
        }
      />

      {/* 提问区 */}
      <div className="card" style={{ marginBottom: 24 }}>
        <label className="field-label" htmlFor="question-input">
          分析问题
        </label>
        <textarea
          id="question-input"
          ref={inputRef}
          className="textarea"
          rows={3}
          placeholder="例如：统计不同生产线的平均缺陷率"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            // Ctrl/Cmd + Enter 提交，避免与多行输入冲突
            if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
              event.preventDefault()
              submit()
            }
          }}
          disabled={loading}
        />

        <div className="row-between" style={{ marginTop: 14, flexWrap: 'wrap', gap: 12 }}>
          <span className="caption">
            会话 <span className="mono">{sessionId}</span> · 支持多轮追问（如「那 Night 班次呢」）
          </span>
          <div className="row" style={{ gap: 8 }}>
            {loading ? (
              <button type="button" className="btn btn-outlined" onClick={cancel}>
                停止等待
              </button>
            ) : null}
            <button
              type="button"
              className="btn btn-dark"
              onClick={() => submit()}
              disabled={loading || !question.trim()}
            >
              {loading ? '分析中…' : '开始分析'}
            </button>
          </div>
        </div>

        {/* 示例问题 */}
        <div style={{ marginTop: 18, paddingTop: 18, borderTop: '1px solid var(--c-lightest-gray)' }}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>
            SAMPLE QUESTIONS
          </div>
          <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
            {PRESETS.map((preset) => (
              <button
                key={preset}
                type="button"
                className="tag"
                style={{ cursor: loading ? 'not-allowed' : 'pointer', background: 'var(--c-white)' }}
                onClick={() => submit(preset)}
                disabled={loading}
              >
                {preset}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 进度：长耗时任务必须有明确反馈 */}
      {loading && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="row-between" style={{ marginBottom: 12 }}>
            <span className="row" style={{ gap: 10 }}>
              <span className="spinner" />
              <span style={{ fontSize: 15 }}>{stage || '正在分析…'}</span>
            </span>
            <span className="mono" style={{ color: 'var(--c-muted-slate)' }}>
              已用时 {elapsed}s
            </span>
          </div>
          <div className="progress-track">
            <div className="progress-bar" style={{ width: `${Math.max(progress, 4)}%` }} />
          </div>
          <div className="caption" style={{ marginTop: 10 }}>
            复杂问题需要多轮工具调用，耗时通常在 10–90 秒之间，请勿关闭页面。
          </div>
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <div className="alert alert-danger" style={{ marginBottom: 24 }}>
          <strong>分析未完成。</strong> {error}
        </div>
      )}

      {/* 分析结果 */}
      {result && <ResultView result={result} tab={tab} setTab={setTab} chartUsable={chartUsable} />}

      {/* 历史记录 */}
      {history.length > 1 && (
        <section style={{ marginTop: 40 }}>
          <div className="eyebrow" style={{ marginBottom: 14 }}>
            HISTORY
          </div>
          <div className="stack" style={{ gap: 10 }}>
            {history.slice(1).map((item, index) => (
              <div
                key={`${item.question}-${index}`}
                className="card"
                style={{ padding: 16, cursor: 'pointer' }}
                onClick={() => setHistory((prev) => [item, ...prev.filter((x) => x !== item)])}
              >
                <div className="row-between" style={{ gap: 12 }}>
                  <span style={{ fontSize: 15, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {item.question}
                  </span>
                  <span className="row" style={{ gap: 6, flex: 'none' }}>
                    <span className="tag">{item.row_count} 行</span>
                    {item.task_type === 'report' && <span className="tag">报告</span>}
                    {item.chart_config && <span className="tag tag-accent">图表</span>}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// 结果展示
// ---------------------------------------------------------------------------
function ResultView({
  result,
  tab,
  setTab,
  chartUsable,
}: {
  result: AskResponse
  tab: ResultTab
  setTab: (tab: ResultTab) => void
  chartUsable: boolean
}) {
  const tabs: { key: ResultTab; label: string; show: boolean }[] = [
    { key: 'chart', label: '图表', show: chartUsable },
    { key: 'table', label: '数据表格', show: result.columns.length > 0 },
    { key: 'sql', label: '生成的 SQL', show: Boolean(result.sql) },
    { key: 'steps', label: '分析过程', show: result.analysis_steps.length > 0 },
    { key: 'report', label: '分析报告', show: Boolean(result.report) },
  ]

  return (
    <div className="stack" style={{ gap: 24 }}>
      {/* 文字结论 —— 始终展示，这是用户最关心的部分 */}
      <div className="card">
        <div className="row-between" style={{ marginBottom: 14, flexWrap: 'wrap', gap: 10 }}>
          <div className="eyebrow">ANALYSIS CONCLUSION</div>
          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            <span className="tag tag-solid">{taskLabel(result.task_type)}</span>
            {result.turns_used > 0 && <span className="tag">注入了 {result.turns_used} 轮历史</span>}
            {result.row_count > 0 && <span className="tag">{result.row_count} 行结果</span>}
            {result.sql_error && <span className="tag" style={{ color: 'var(--c-danger)', borderColor: 'var(--c-danger)' }}>取数失败</span>}
          </div>
        </div>

        <Markdown content={result.analysis_text} />

        {result.sql_error && (
          <div className="alert alert-danger" style={{ marginTop: 16 }}>
            <strong>结果回放取数失败：</strong> {result.sql_error}
            <div style={{ marginTop: 4, color: 'var(--c-muted-slate)' }}>
              文字结论仍然有效，仅结构化数据未能取回。
            </div>
          </div>
        )}

        {/* 命中的指标口径 */}
        {result.metric_bindings.length > 0 && (
          <div style={{ marginTop: 18, paddingTop: 16, borderTop: '1px solid var(--c-lightest-gray)' }}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>
              BUSINESS METRICS
            </div>
            <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
              {result.metric_bindings.map((binding, index) => (
                <span className="tag tag-accent" key={index}>
                  {String(binding.label ?? binding.standard_field ?? binding.column ?? '指标')}
                  {binding.expression ? ` · ${binding.expression}` : ''}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 多视图切换 */}
      <div className="card">
        <div className="row" style={{ gap: 4, marginBottom: 20, flexWrap: 'wrap' }}>
          {tabs
            .filter((item) => item.show)
            .map((item) => (
              <button
                key={item.key}
                type="button"
                className="btn btn-sm"
                onClick={() => setTab(item.key)}
                style={{
                  color: tab === item.key ? 'var(--c-blue)' : 'var(--c-black)',
                  background: tab === item.key ? 'rgba(24, 99, 220, 0.06)' : 'transparent',
                }}
              >
                {item.label}
              </button>
            ))}
        </div>

        {/* 图表 */}
        {tab === 'chart' && result.chart_config && chartUsable && (
          <>
            <div className="row-between" style={{ marginBottom: 14, flexWrap: 'wrap', gap: 10 }}>
              <div>
                <h3 className="feature-title">{result.chart_config.title || '分析图表'}</h3>
                <div className="caption" style={{ marginTop: 4 }}>
                  {result.chart_config.reason}
                </div>
              </div>
              <span className="tag">
                {result.chart_config.chart_type} ·{' '}
                {result.chart_config.source === 'llm' ? '大模型生成' : '规则兜底'}
              </span>
            </div>
            <EChart option={result.chart_config.option} height={400} />
          </>
        )}

        {/* 数据表格 */}
        {tab === 'table' && (
          <DataTable columns={result.columns} rows={result.rows} />
        )}

        {/* SQL */}
        {tab === 'sql' && result.sql && (
          <>
            <div className="row-between" style={{ marginBottom: 12 }}>
              <div className="caption">
                该 SQL 由大模型生成，执行前经过只读守卫校验（仅允许 SELECT / WITH）。
              </div>
              <button
                type="button"
                className="btn btn-outlined btn-sm"
                onClick={() => void navigator.clipboard?.writeText(result.sql ?? '')}
              >
                复制
              </button>
            </div>
            <pre className="code-block">{result.sql}</pre>
            {result.rag_context && (
              <div style={{ marginTop: 18 }}>
                <div className="eyebrow" style={{ marginBottom: 8 }}>
                  RAG 检索到的相似示例
                </div>
                <pre className="code-block" style={{ maxHeight: 260, overflowY: 'auto' }}>
                  {result.rag_context}
                </pre>
              </div>
            )}
          </>
        )}

        {/* 分析过程 */}
        {tab === 'steps' && (
          <>
            <AnalysisTimeline steps={result.analysis_steps} />
            {result.prompt_usage && (
              <div style={{ marginTop: 22, paddingTop: 18, borderTop: '1px solid var(--c-lightest-gray)' }}>
                <div className="eyebrow" style={{ marginBottom: 10 }}>
                  PROMPT 预算用量 · {result.prompt_usage.used_chars}/{result.prompt_usage.total_budget} 字符
                </div>
                <div className="stack" style={{ gap: 10 }}>
                  {result.prompt_usage.sections?.map((section) => (
                    <div key={section.name}>
                      <div className="row-between" style={{ marginBottom: 4 }}>
                        <span className="mono">{section.name}</span>
                        <span className="caption">
                          {section.used_chars}/{section.max_chars}
                          {section.dropped_items ? ` · 丢弃 ${section.dropped_items} 项` : ''}
                          {section.truncated ? ' · 已截断' : ''}
                          {section.error ? ` · 失败(${section.error})` : ''}
                        </span>
                      </div>
                      <div className="progress-track">
                        <div
                          className="progress-bar"
                          style={{
                            width: `${Math.min(100, (section.used_chars / Math.max(section.max_chars, 1)) * 100)}%`,
                          }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {/* 报告 */}
        {tab === 'report' && result.report && <Markdown content={result.report} />}
      </div>
    </div>
  )
}

function taskLabel(taskType: string): string {
  switch (taskType) {
    case 'report':
      return '报告生成'
    case 'anomaly':
      return '异常检测'
    case 'regression':
      return '回归建模'
    default:
      return 'SQL 查询分析'
  }
}
