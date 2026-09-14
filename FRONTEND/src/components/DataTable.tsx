/**
 * 通用数据表格。
 *
 * 用于展示查询结果（二维数组）、字段清单、建模指标等。
 * 刻意不做分页/排序等重型能力——分析结果通常在 200 行以内
 * （后端 `SQL_RESULT_ROW_LIMIT` 限制），简单渲染更利于「结果可读」。
 */

import type { ReactNode } from 'react'

interface DataTableProps {
  columns: string[]
  rows: unknown[][]
  /** 最多渲染多少行 */
  maxRows?: number
  emptyText?: string
  className?: string
}

/** 把单元格值格式化成可读文本 */
export function formatCell(value: unknown): ReactNode {
  if (value === null || value === undefined) {
    return <span style={{ color: 'var(--c-muted-slate)' }}>—</span>
  }
  if (typeof value === 'number') {
    // 浮点数保留 4 位有效小数，避免 3.9096120317372307 这种噪音
    if (Number.isInteger(value)) return value.toLocaleString('zh-CN')
    return Number(value.toFixed(4)).toLocaleString('zh-CN')
  }
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function DataTable({
  columns,
  rows,
  maxRows = 200,
  emptyText = '暂无数据',
  className,
}: DataTableProps) {
  if (!columns.length) {
    return <div className="empty">{emptyText}</div>
  }

  const visible = rows.slice(0, maxRows)
  const hidden = rows.length - visible.length

  return (
    <div className={className}>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((column, index) => (
                <th key={`${column}-${index}`}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr>
                <td colSpan={columns.length} style={{ textAlign: 'center', color: 'var(--c-muted-slate)' }}>
                  {emptyText}
                </td>
              </tr>
            ) : (
              visible.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {columns.map((_, cellIndex) => (
                    <td key={cellIndex}>{formatCell(row[cellIndex])}</td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {hidden > 0 && (
        <div className="caption" style={{ marginTop: 8 }}>
          仅展示前 {maxRows} 行，另有 {hidden} 行未显示
        </div>
      )}
    </div>
  )
}
