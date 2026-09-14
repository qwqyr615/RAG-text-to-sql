/**
 * 分析报告页。
 *
 * 对应题目「生成一份本周质量分析结果」这类需求与「报告生成」能力。
 * 流程：提交主题 → 后端执行 SQL 分析并生成 Markdown 报告 → 前端渲染、复制、导出。
 *
 * 与智能问答页的区别：这里聚焦「报告产出」，因此隐藏图表/表格视图，
 * 只保留报告正文与分析过程，并提供导出能力。
 */

import { useEffect, useState } from 'react'

import { AnalysisTimeline } from '../components/AnalysisTimeline'
import { Markdown } from '../components/Markdown'
import { PageHeader } from '../components/StateBlocks'
import { useAsk } from '../hooks/useAsk'
import type { AskResponse } from '../api/types'

/** 报告主题模板：围绕制造业务场景，覆盖四大分析主题 */
const TEMPLATES = [
  { title: '质量分析报告', question: '生成一份质量分析报告，覆盖缺陷率、良率与质量得分。' },
  { title: '设备分析报告', question: '生成一份设备分析报告，覆盖停机时长、故障次数与设备利用率。' },
  { title: '生产分析报告', question: '生成一份生产分析报告，覆盖产量、生产效率与生产节拍。' },
  { title: '综合运营报告', question: '生成一份本周综合运营分析报告。' },
]

export function ReportPage() {
  const [sessionId] = useState(() => `report-${Date.now().toString(36)}`)
  const [question, setQuestion] = useState(TEMPLATES[0].question)
  const [copied, setCopied] = useState(false)
  const { loading, stage, elapsed, result, error, ask } = useAsk(sessionId)

  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  const generate = () => {
    void ask({ question, sessionId, wantReport: true, wantChart: false })
  }

  const download = (report: string) => {
    const blob = new Blob([report], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `分析报告_${new Date().toISOString().slice(0, 10)}.md`
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
    URL.revokeObjectURL(url)
  }

  return (
    <>
      <PageHeader
        eyebrow="ANALYSIS REPORT"
        title="分析报告"
        description="基于真实数据的查询结果生成 Markdown 报告，包含指标结论、数据支撑与业务建议。"
      />

      <div className="grid grid-2" style={{ marginBottom: 24, alignItems: 'start' }}>
        {/* 模板选择 */}
        <div className="card">
          <div className="eyebrow" style={{ marginBottom: 14 }}>
            REPORT TEMPLATES
          </div>
          <div className="stack" style={{ gap: 10 }}>
            {TEMPLATES.map((template) => {
              const active = question === template.question
              return (
                <button
                  key={template.title}
                  type="button"
                  onClick={() => setQuestion(template.question)}
                  style={{
                    textAlign: 'left',
                    cursor: 'pointer',
                    padding: '13px 16px',
                    borderRadius: 'var(--radius-generous)',
                    border: `1px solid ${active ? 'var(--c-blue)' : 'var(--c-lightest-gray)'}`,
                    background: active ? 'rgba(24, 99, 220, 0.05)' : 'var(--c-white)',
                  }}
                >
                  <div style={{ fontSize: 15.5, color: active ? 'var(--c-blue)' : 'var(--c-black)', marginBottom: 5 }}>
                    {template.title}
                  </div>
                  <div className="caption">{template.question}</div>
                </button>
              )
            })}
          </div>
        </div>

        {/* 自定义主题 */}
        <div className="card">
          <label className="field-label" htmlFor="report-question">
            报告主题（可自定义）
          </label>
          <textarea
            id="report-question"
            className="textarea"
            rows={4}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            disabled={loading}
          />
          <div className="row-between" style={{ marginTop: 14, flexWrap: 'wrap', gap: 10 }}>
            <span className="caption">
              {loading ? `生成中 · 已用时 ${elapsed}s` : '报告生成包含一次完整的数据分析，通常耗时 20–90 秒'}
            </span>
            <button type="button" className="btn btn-dark" onClick={generate} disabled={loading || !question.trim()}>
              {loading ? '生成中…' : '生成报告'}
            </button>
          </div>
          {loading && (
            <div style={{ marginTop: 14 }}>
              <div className="row" style={{ gap: 10, marginBottom: 8 }}>
                <span className="spinner" />
                <span style={{ fontSize: 14 }}>{stage || '正在分析…'}</span>
              </div>
              <div className="progress-track">
                <div className="progress-bar" style={{ width: '60%' }} />
              </div>
            </div>
          )}
        </div>
      </div>

      {error && (
        <div className="alert alert-danger" style={{ marginBottom: 24 }}>
          <strong>报告生成失败。</strong> {error}
        </div>
      )}

      {result && <ReportView result={result} onDownload={download} onCopy={() => setCopied(true)} copied={copied} />}
    </>
  )
}

function ReportView({
  result,
  onDownload,
  onCopy,
  copied,
}: {
  result: AskResponse
  onDownload: (report: string) => void
  onCopy: () => void
  copied: boolean
}) {
  const report = result.report

  return (
    <div className="stack" style={{ gap: 24 }}>
      <div className="card">
        <div className="row-between" style={{ marginBottom: 18, flexWrap: 'wrap', gap: 12 }}>
          <div className="eyebrow">REPORT PREVIEW</div>
          {report && (
            <div className="row" style={{ gap: 8 }}>
              <button
                type="button"
                className="btn btn-outlined btn-sm"
                onClick={() => {
                  void navigator.clipboard?.writeText(report)
                  onCopy()
                }}
              >
                {copied ? '已复制 ✓' : '复制 Markdown'}
              </button>
              <button type="button" className="btn btn-outlined btn-sm" onClick={() => onDownload(report)}>
                导出 .md
              </button>
            </div>
          )}
        </div>

        {report ? (
          <Markdown content={report} />
        ) : (
          <div className="alert alert-info">
            后端未返回报告正文，仅返回了分析结论。以下为结论内容：
            <div style={{ marginTop: 12 }}>
              <Markdown content={result.analysis_text} />
            </div>
          </div>
        )}
      </div>

      {/* 数据支撑：报告的可信度来自这里 */}
      {result.sql && (
        <div className="card">
          <div className="eyebrow" style={{ marginBottom: 12 }}>
            DATA BASIS · 报告依据的查询与结果
          </div>
          <pre className="code-block" style={{ marginBottom: 16 }}>
            {result.sql}
          </pre>
          <div className="caption">
            查询返回 {result.row_count} 行
            {result.columns.length ? `，列：${result.columns.join('、')}` : ''}
            {result.sql_error ? ` · 结构化取数失败：${result.sql_error}` : ''}
          </div>
        </div>
      )}

      {result.analysis_steps.length > 0 && (
        <div className="card">
          <div className="eyebrow" style={{ marginBottom: 16 }}>
            ANALYSIS PROCESS
          </div>
          <AnalysisTimeline steps={result.analysis_steps} />
        </div>
      )}
    </div>
  )
}
