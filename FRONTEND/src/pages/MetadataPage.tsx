/**
 * 数据资源页。
 *
 * 对应题目「数据资源理解功能」：展示已有数据表、字段、字段类型、字段说明、
 * 样例值和表间关系，帮助业务人员了解可用数据资源及其含义。
 *
 * 交互：左侧表清单（可搜索）→ 右侧字段字典；字段支持按名称/说明/类型过滤，
 * 并可查看真实样例数据的横向明细，避免「看不到真实取值」的空洞感。
 */

import { useMemo, useState } from 'react'

import { api } from '../api/client'
import { DataTable } from '../components/DataTable'
import { EmptyBlock, ErrorBlock, Loading, PageHeader, StatCard } from '../components/StateBlocks'
import { useAsync } from '../hooks/useAsync'
import type { TableInfo } from '../api/types'

/** 首列固定为字段名，其余为说明信息 */
const FIELD_COLUMNS = ['字段名', '类型', '可空', '主键', '字段说明', '样例值']

export function MetadataPage() {
  const metadata = useAsync(() => api.metadataTables(), [])
  const relationships = useAsync(() => api.metadataRelationships(), [])

  const [selectedTable, setSelectedTable] = useState<string | null>(null)
  const [tableFilter, setTableFilter] = useState('')
  const [fieldFilter, setFieldFilter] = useState('')
  const [showSamples, setShowSamples] = useState(false)

  const tables = metadata.data?.tables ?? []

  const visibleTables = useMemo(() => {
    const keyword = tableFilter.trim().toLowerCase()
    if (!keyword) return tables
    return tables.filter(
      (table) =>
        table.table_name.toLowerCase().includes(keyword) ||
        (table.description ?? '').toLowerCase().includes(keyword),
    )
  }, [tables, tableFilter])

  // 默认选中第一张表；表格数据变化时若已选表消失则回退
  const activeTable: TableInfo | null = useMemo(() => {
    if (!tables.length) return null
    const found = tables.find((table) => table.table_name === selectedTable)
    return found ?? tables[0]
  }, [tables, selectedTable])

  const fields = useMemo(() => {
    if (!activeTable) return []
    const keyword = fieldFilter.trim().toLowerCase()
    const rows = activeTable.columns.map((column) => [
      column.name,
      column.type,
      column.nullable ? '是' : '否',
      column.primary_key ? '✓' : '',
      column.description || '—',
      column.sample_value === null || column.sample_value === undefined
        ? '—'
        : String(column.sample_value),
    ])
    if (!keyword) return rows
    return rows.filter((row) =>
      String(row[4]).toLowerCase().includes(keyword) ||
      String(row[0]).toLowerCase().includes(keyword) ||
      String(row[1]).toLowerCase().includes(keyword),
    )
  }, [activeTable, fieldFilter])

  // 样例数据转成二维数组供表格渲染
  const sampleData = useMemo(() => {
    if (!activeTable?.sample_rows?.length) return { columns: [], rows: [] }
    const columns = Object.keys(activeTable.sample_rows[0])
    const rows = activeTable.sample_rows.map((record) => columns.map((key) => record[key]))
    return { columns, rows }
  }, [activeTable])

  const totalColumns = tables.reduce((sum, table) => sum + table.columns.length, 0)
  const describedColumns = tables.reduce(
    (sum, table) => sum + table.columns.filter((column) => column.description).length,
    0,
  )

  if (metadata.loading) return <Loading text="正在读取数据资源…" />
  if (metadata.error) return <ErrorBlock message={metadata.error} onRetry={metadata.reload} />
  if (!tables.length) return <EmptyBlock text="未发现任何业务表，请先初始化数据底座。" />

  return (
    <>
      <PageHeader
        eyebrow="DATA RESOURCE UNDERSTANDING"
        title="数据资源"
        description="系统以「发现模式」自动扫描数据库，读取表结构、字段类型、字段说明与真实样例值。"
      />

      <div className="grid grid-4" style={{ marginBottom: 32 }}>
        <StatCard label="业务表" value={tables.length} hint={`数据源 ${metadata.data?.database_type ?? '—'}`} />
        <StatCard label="字段总数" value={totalColumns} />
        <StatCard
          label="字段说明覆盖率"
          value={`${totalColumns ? Math.round((describedColumns / totalColumns) * 100) : 0}%`}
          hint={`${describedColumns}/${totalColumns} 个字段有说明`}
        />
        <StatCard
          label="表间关系"
          value={(relationships.data?.relationships ?? []).length}
          hint="单表宽表模型下无维表关系"
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 300px) minmax(0, 1fr)', gap: 24 }}>
        {/* 左：表清单 */}
        <aside>
          <div className="eyebrow" style={{ marginBottom: 10 }}>
            TABLES
          </div>
          <input
            className="input"
            placeholder="搜索表名或说明"
            value={tableFilter}
            onChange={(event) => setTableFilter(event.target.value)}
            style={{ marginBottom: 12 }}
          />
          <div className="stack" style={{ gap: 8 }}>
            {visibleTables.map((table) => {
              const active = activeTable?.table_name === table.table_name
              return (
                <button
                  key={table.table_name}
                  type="button"
                  onClick={() => setSelectedTable(table.table_name)}
                  style={{
                    textAlign: 'left',
                    width: '100%',
                    cursor: 'pointer',
                    padding: '13px 15px',
                    borderRadius: 'var(--radius-generous)',
                    border: `1px solid ${active ? 'var(--c-blue)' : 'var(--c-lightest-gray)'}`,
                    background: active ? 'rgba(24, 99, 220, 0.05)' : 'var(--c-white)',
                  }}
                >
                  <div
                    className="mono"
                    style={{ fontSize: 13.5, color: active ? 'var(--c-blue)' : 'var(--c-black)', marginBottom: 5, wordBreak: 'break-all' }}
                  >
                    {table.table_name}
                  </div>
                  <div className="caption" style={{ marginBottom: 7 }}>
                    {table.columns.length} 字段
                    {table.row_count > 0 ? ` · ${table.row_count.toLocaleString('zh-CN')} 行` : ''}
                  </div>
                  <div className="row" style={{ gap: 5, flexWrap: 'wrap' }}>
                    {table.role && <span className="tag">{table.role}</span>}
                    {table.grain && <span className="tag">粒度：{table.grain}</span>}
                  </div>
                </button>
              )
            })}
            {!visibleTables.length && <EmptyBlock text="没有匹配的表" />}
          </div>
        </aside>

        {/* 右：字段字典 */}
        <section style={{ minWidth: 0 }}>
          {activeTable && (
            <>
              <div className="card" style={{ marginBottom: 20 }}>
                <div className="row-between" style={{ alignItems: 'flex-start', flexWrap: 'wrap', gap: 14 }}>
                  <div style={{ minWidth: 0 }}>
                    <div className="eyebrow" style={{ marginBottom: 8 }}>
                      {activeTable.role || 'TABLE'}
                    </div>
                    <h2 className="feature-title mono" style={{ wordBreak: 'break-all' }}>
                      {activeTable.table_name}
                    </h2>
                    <p style={{ fontSize: 14.5, color: 'var(--c-muted-slate)', margin: '10px 0 0', lineHeight: 1.65, maxWidth: 680 }}>
                      {activeTable.description || '暂无表说明'}
                    </p>
                  </div>
                  <div className="stack" style={{ gap: 8, flex: 'none' }}>
                    {activeTable.grain && (
                      <span className="tag">粒度：{activeTable.grain}</span>
                    )}
                    {activeTable.primary_key && (
                      <span className="tag">主键：{activeTable.primary_key}</span>
                    )}
                    {activeTable.time_column && (
                      <span className="tag">时间列：{activeTable.time_column}</span>
                    )}
                  </div>
                </div>
              </div>

              <div className="row-between" style={{ marginBottom: 12, flexWrap: 'wrap', gap: 10 }}>
                <div className="eyebrow">
                  FIELDS · {fields.length}/{activeTable.columns.length}
                </div>
                <input
                  className="input"
                  placeholder="搜索字段名、类型或说明"
                  value={fieldFilter}
                  onChange={(event) => setFieldFilter(event.target.value)}
                  style={{ maxWidth: 300 }}
                />
              </div>

              <DataTable columns={FIELD_COLUMNS} rows={fields} maxRows={200} emptyText="没有匹配的字段" />

              {/* 样例数据 */}
              {sampleData.columns.length > 0 && (
                <div style={{ marginTop: 28 }}>
                  <div className="row-between" style={{ marginBottom: 12 }}>
                    <div className="eyebrow">SAMPLE ROWS · 真实数据抽样</div>
                    <button
                      type="button"
                      className="btn btn-outlined btn-sm"
                      onClick={() => setShowSamples((value) => !value)}
                    >
                      {showSamples ? '收起' : '展开样例数据'}
                    </button>
                  </div>
                  {showSamples && (
                    <DataTable columns={sampleData.columns} rows={sampleData.rows} />
                  )}
                </div>
              )}
            </>
          )}
        </section>
      </div>

      {/* 表间关系 */}
      <section style={{ marginTop: 40 }}>
        <div className="eyebrow" style={{ marginBottom: 12 }}>
          RELATIONSHIPS
        </div>
        {(relationships.data?.relationships ?? []).length > 0 ? (
          <DataTable
            columns={['源表', '源字段', '目标表', '目标字段', '关系类型', '说明']}
            rows={(relationships.data?.relationships ?? []).map((relation) => [
              relation.source_table,
              relation.source_column,
              relation.target_table,
              relation.target_column,
              relation.relation_type,
              relation.description ?? '—',
            ])}
          />
        ) : (
          <div className="alert alert-info">
            {relationships.data?.note ??
              '当前数据模型为单表宽表（已删除 dim_* 维表），因此没有实体表间关系。知识图谱请查看「业务知识」页。'}
          </div>
        )}
      </section>
    </>
  )
}
