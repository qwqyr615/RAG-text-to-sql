/**
 * 分析过程时间线。
 *
 * 对应题目「系统展示效果：是否能够展示分析过程」的评分要点。
 * 展示后端返回的 `analysis_steps`：检索到的相似示例、生成的 SQL、
 * 结果规模、分析结论、图表配置等。
 */

import { useState } from 'react'

import type { AnalysisStep, StepKind } from '../api/types'

/** 步骤类型 → 序号徽标的文案（等宽大写，符合设计系统的技术标记风格） */
const KIND_LABEL: Record<StepKind, string> = {
  prompt: 'CTX',
  sql: 'SQL',
  result: 'DAT',
  report: 'RPT',
  chart: 'VIS',
  model: 'ML',
}

interface AnalysisTimelineProps {
  steps: AnalysisStep[]
  /** 长文本（SQL、RAG 上下文）默认折叠 */
  collapsible?: boolean
}

export function AnalysisTimeline({ steps, collapsible = true }: AnalysisTimelineProps) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})

  if (!steps.length) {
    return <div className="empty">本次分析没有可展示的中间步骤</div>
  }

  return (
    <div className="timeline">
      {steps.map((step, index) => {
        const isLong = step.detail.length > 180
        const isOpen = expanded[index] ?? (!collapsible || !isLong)
        const detail = isOpen ? step.detail : `${step.detail.slice(0, 180)}…`

        return (
          <div className="timeline-item" key={`${step.title}-${index}`}>
            <div className="timeline-marker">{KIND_LABEL[step.kind] ?? '·'}</div>
            <div className="timeline-body">
              <div className="timeline-title">{step.title}</div>
              {step.kind === 'sql' ? (
                <pre className="code-block" style={{ marginTop: 6 }}>
                  {step.detail}
                </pre>
              ) : (
                <div className="timeline-detail" style={{ whiteSpace: 'pre-wrap' }}>
                  {detail}
                </div>
              )}
              {collapsible && isLong && step.kind !== 'sql' && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  style={{ paddingLeft: 0, marginTop: 4 }}
                  onClick={() => setExpanded((prev) => ({ ...prev, [index]: !isOpen }))}
                >
                  {isOpen ? '收起' : '展开全部'}
                </button>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
