/**
 * 页面级三态展示的公共片段：加载中 / 出错 / 空数据。
 */

import type { ReactNode } from 'react'

export function Loading({ text = '正在加载…' }: { text?: string }) {
  return (
    <div className="empty" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }}>
      <span className="spinner" />
      {text}
    </div>
  )
}

export function ErrorBlock({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="alert alert-danger">
      <strong>加载失败。</strong> {message}
      {onRetry && (
        <div style={{ marginTop: 10 }}>
          <button type="button" className="btn btn-outlined btn-sm" onClick={onRetry}>
            重新加载
          </button>
        </div>
      )}
    </div>
  )
}

export function EmptyBlock({ text }: { text: string }) {
  return <div className="empty">{text}</div>
}

/** 页面标题区：等宽大写 eyebrow + 衬线标题，符合 Cohere 的字阶层级 */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <div className="row-between" style={{ alignItems: 'flex-end', marginBottom: 28, gap: 20, flexWrap: 'wrap' }}>
      <div style={{ minWidth: 0 }}>
        <div className="eyebrow" style={{ marginBottom: 8 }}>
          {eyebrow}
        </div>
        <h1 className="section-heading">{title}</h1>
        {description && (
          <p className="body-large" style={{ color: 'var(--c-muted-slate)', margin: '10px 0 0', maxWidth: 720 }}>
            {description}
          </p>
        )}
      </div>
      {actions}
    </div>
  )
}

/** 指标卡：数字用衬线字体给出视觉重量 */
export function StatCard({
  label,
  value,
  hint,
}: {
  label: string
  value: ReactNode
  hint?: string
}) {
  return (
    <div className="card" style={{ padding: 22 }}>
      <div className="eyebrow" style={{ marginBottom: 10 }}>
        {label}
      </div>
      <div
        style={{
          fontFamily: 'var(--font-display)',
          fontSize: 34,
          lineHeight: 1.1,
          letterSpacing: '-0.6px',
          color: 'var(--c-black)',
        }}
      >
        {value}
      </div>
      {hint && (
        <div className="caption" style={{ marginTop: 8 }}>
          {hint}
        </div>
      )}
    </div>
  )
}
