/**
 * 业务知识页。
 *
 * 对应题目「业务知识管理功能」与「可通过知识图谱等可视化的方式来展示」。
 *
 * 图谱说明：`dim_*` 维表已删除，当前是单表宽表模型，**实体关系图没有数据支撑**，
 * 因此后端组装的是「主题 → 业务对象 → 指标口径 → 字段 → 数据表」的
 * **逻辑知识图谱**，每个节点都来自真实的 metadata / knowledge 解析结果。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'

import { api } from '../api/client'
import { DataTable } from '../components/DataTable'
import { EmptyBlock, ErrorBlock, Loading, PageHeader, StatCard } from '../components/StateBlocks'
import { useAsync } from '../hooks/useAsync'
import { chartTheme, colors, fonts } from '../styles/tokens'
import type { GraphNode } from '../api/types'

/** 节点类别 → 图形尺寸与颜色（严格冷色，紫色只用于「指标」这一层） */
const CATEGORY_STYLE: Record<string, { size: number; color: string; symbol: string }> = {
  主题: { size: 52, color: '#17171c', symbol: 'roundRect' },
  业务对象: { size: 40, color: '#1863dc', symbol: 'circle' },
  指标口径: { size: 36, color: '#9b60aa', symbol: 'diamond' },
  字段: { size: 24, color: '#93939f', symbol: 'circle' },
  数据表: { size: 44, color: '#4c6ee6', symbol: 'rect' },
}

export function KnowledgePage() {
  const overview = useAsync(() => api.knowledgeOverview(), [])
  const graph = useAsync(() => api.knowledgeGraph(), [])

  const themes = overview.data?.themes ?? []
  const objects = overview.data?.objects ?? []
  const rules = overview.data?.rules ?? []
  const resolvedRules = rules.filter((rule) => rule.mapped_field)
  const unresolvedRules = rules.filter((rule) => !rule.mapped_field)

  return (
    <>
      <PageHeader
        eyebrow="BUSINESS KNOWLEDGE"
        title="业务知识与知识图谱"
        description="围绕制造业务场景组织分析主题、业务对象与指标口径，并解析到真实数据表与字段。"
      />

      {overview.loading && <Loading text="正在读取业务知识…" />}
      {overview.error && <ErrorBlock message={overview.error} onRetry={overview.reload} />}

      {!overview.loading && !overview.error && (
        <>
          <div className="grid grid-4" style={{ marginBottom: 32 }}>
            <StatCard label="分析主题" value={themes.length} />
            <StatCard label="业务对象" value={objects.length} />
            <StatCard
              label="指标口径已解析"
              value={`${resolvedRules.length}/${rules.length}`}
              hint={unresolvedRules.length ? `${unresolvedRules.length} 项当前数据源缺字段` : '全部指标已解析到字段'}
            />
            <StatCard
              label="图谱节点"
              value={graph.data?.nodes.length ?? '—'}
              hint={`${graph.data?.edges.length ?? 0} 条关系`}
            />
          </div>

          {/* 知识图谱 */}
          <section className="section">
            <div className="eyebrow" style={{ marginBottom: 6 }}>
              KNOWLEDGE GRAPH
            </div>
            <h2 className="sub-heading" style={{ marginBottom: 8 }}>
              知识图谱
            </h2>
            <p className="caption" style={{ marginBottom: 18, maxWidth: 760 }}>
              主题 → 业务对象 → 指标口径 → 字段 → 数据表。节点可拖拽、可缩放，
              点击节点可高亮其直接关联。虚线连到指标的表示该指标在当前数据源中缺少对应字段。
            </p>

            {graph.loading && <Loading text="正在组装知识图谱…" />}
            {graph.error && <ErrorBlock message={graph.error} onRetry={graph.reload} />}
            {!graph.loading && !graph.error && (graph.data?.nodes.length ?? 0) > 0 && (
              <>
                <KnowledgeGraphCanvas
                  nodes={graph.data?.nodes ?? []}
                  edges={graph.data?.edges ?? []}
                />
                <div className="row" style={{ gap: 14, marginTop: 14, flexWrap: 'wrap' }}>
                  {Object.entries(CATEGORY_STYLE).map(([name, style]) => (
                    <span className="row" key={name} style={{ gap: 7 }}>
                      <span
                        style={{
                          width: 11,
                          height: 11,
                          borderRadius: name === '主题' || name === '数据表' ? 2 : '50%',
                          background: style.color,
                          display: 'inline-block',
                        }}
                      />
                      <span className="caption">{name}</span>
                    </span>
                  ))}
                </div>
              </>
            )}
            {!graph.loading && !graph.error && (graph.data?.nodes.length ?? 0) === 0 && (
              <EmptyBlock text="知识图谱为空" />
            )}
          </section>

          {/* 分析主题 */}
          <section className="section">
            <div className="eyebrow" style={{ marginBottom: 6 }}>
              THEMES
            </div>
            <h2 className="sub-heading" style={{ marginBottom: 20 }}>
              分析主题
            </h2>
            <div className="grid grid-2">
              {themes.map((theme) => (
                <div className="card" key={theme.code}>
                  <div className="eyebrow" style={{ marginBottom: 10 }}>
                    {theme.code}
                  </div>
                  <h3 className="feature-title" style={{ marginBottom: 10 }}>
                    {theme.name}
                  </h3>
                  <p style={{ fontSize: 14.5, color: 'var(--c-muted-slate)', margin: '0 0 14px', lineHeight: 1.65 }}>
                    {theme.description || '暂无描述'}
                  </p>
                  <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                    {theme.related_tables.length ? (
                      theme.related_tables.map((table) => (
                        <span className="tag mono" key={table}>
                          {table}
                        </span>
                      ))
                    ) : (
                      <span className="tag">当前数据源暂无对应表</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* 业务对象 */}
          <section className="section">
            <div className="eyebrow" style={{ marginBottom: 6 }}>
              BUSINESS OBJECTS
            </div>
            <h2 className="sub-heading" style={{ marginBottom: 20 }}>
              业务对象
            </h2>
            <DataTable
              columns={['业务对象', '默认表', '关键字段', '说明']}
              rows={objects.map((object) => [
                object.name,
                object.default_table,
                object.key_field || '—',
                object.description || '—',
              ])}
              emptyText="暂无业务对象"
            />
          </section>

          {/* 指标规则 */}
          <section>
            <div className="eyebrow" style={{ marginBottom: 6 }}>
              METRIC RULES
            </div>
            <h2 className="sub-heading" style={{ marginBottom: 20 }}>
              指标口径
            </h2>
            <DataTable
              columns={['指标', '映射表', '映射字段', '计算口径', '状态']}
              rows={rules.map((rule) => [
                rule.name,
                rule.mapped_table || '—',
                rule.mapped_field || '—',
                rule.resolved_calculation || rule.calculation || '—',
                rule.mapped_field ? '已解析' : '数据源缺字段',
              ])}
              emptyText="暂无指标规则"
            />
          </section>
        </>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// 图谱画布
// ---------------------------------------------------------------------------
function KnowledgeGraphCanvas({
  nodes,
  edges,
}: {
  nodes: GraphNode[]
  edges: { source: string; target: string; label: string }[]
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const [selected, setSelected] = useState<GraphNode | null>(null)

  // 计算每个节点的连接度，用于决定标签是否默认显示（避免标签糊成一片）
  const degree = useMemo(() => {
    const counter: Record<string, number> = {}
    edges.forEach((edge) => {
      counter[edge.source] = (counter[edge.source] ?? 0) + 1
      counter[edge.target] = (counter[edge.target] ?? 0) + 1
    })
    return counter
  }, [edges])

  const option = useMemo(() => {
    const categories = Object.keys(CATEGORY_STYLE).map((name) => ({ name }))

    return {
      tooltip: {
        ...chartTheme.tooltip,
        formatter: (params: { dataType: string; data: Record<string, unknown> }) => {
          if (params.dataType === 'edge') {
            return String((params.data as { label?: string }).label ?? '')
          }
          const data = params.data as unknown as GraphNode
          const lines = [`<strong>${data.name}</strong>`, `类别：${data.category}`]
          if (data.description) lines.push(String(data.description))
          if (data.expression) lines.push(`口径：${data.expression}`)
          if (data.category === '指标口径') {
            lines.push(data.resolved ? '状态：已解析到字段' : '状态：当前数据源缺字段')
          }
          return lines.join('<br/>')
        },
      },
      legend: {
        data: categories.map((category) => category.name),
        top: 0,
        textStyle: { color: colors.mutedSlate, fontFamily: fonts.body },
        icon: 'circle',
      },
      animationDuration: 600,
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          categories,
          data: nodes.map((node) => {
            const style = CATEGORY_STYLE[node.category] ?? { size: 26, color: colors.mutedSlate, symbol: 'circle' }
            const links = degree[node.id] ?? 0
            // 未解析的指标用空心表示，视觉上提示「缺口」
            const dimmed = node.category === '指标口径' && node.resolved === false
            return {
              id: node.id,
              name: node.name,
              category: Math.max(0, categories.findIndex((category) => category.name === node.category)),
              symbolSize: style.size + Math.min(links * 2, 12),
              symbol: node.category === '指标口径' && !dimmed ? 'diamond' : style.symbol,
              itemStyle: {
                color: dimmed ? colors.white : style.color,
                borderColor: style.color,
                borderWidth: dimmed ? 1.5 : 0,
                borderType: dimmed ? 'dashed' : 'solid',
              },
              label: {
                show: true,
                position: 'right',
                fontSize: 11,
                color: colors.nearBlack,
                fontFamily: fonts.body,
                // 只在连接度较高时默认显示标签，其余 hover 时显示
                opacity: links >= 2 ? 1 : 0,
                formatter: node.name,
              },
              emphasis: { label: { show: true, opacity: 1 }, scale: 1.1 },
              // 兼容后端可能未提供 resolved 字段的情况
              resolved: node.resolved,
            }
          }),
          links: edges.map((edge) => ({
            source: edge.source,
            target: edge.target,
            label: { show: false, formatter: edge.label },
            lineStyle: {
              color: colors.borderCool,
              width: 1,
              curveness: 0.08,
              type: edge.label === '未映射' ? 'dashed' : 'solid',
            },
          })),
          force: { repulsion: 260, edgeLength: [70, 150], gravity: 0.08 },
          lineStyle: { color: colors.borderCool, opacity: 0.9 },
          emphasis: { focus: 'adjacency', lineStyle: { width: 2, color: colors.interactionBlue } },
        },
      ],
    }
  }, [nodes, edges, degree])

  useEffect(() => {
    if (!containerRef.current) return
    const chart = echarts.init(containerRef.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart

    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(containerRef.current)

    // ECharts 的 ECElementEvent 类型把 dataType 标为可选，这里只关心 node 点击，
    // 因此用一个局部结构做窄化，避免引入 any。
    chart.on('click', (params) => {
      const event = params as { dataType?: string; data?: unknown }
      if (event.dataType === 'node' && event.data) {
        setSelected(event.data as GraphNode)
      }
    })

    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, true)
  }, [option])

  return (
    <>
      <div ref={containerRef} className="graph-canvas" />
      {selected && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="row-between" style={{ marginBottom: 10 }}>
            <div className="eyebrow">节点详情</div>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelected(null)}>
              关闭
            </button>
          </div>
          <div className="row" style={{ gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
            <span className="tag tag-solid">{selected.category}</span>
            <span style={{ fontSize: 17 }}>{selected.name}</span>
          </div>
          {selected.description && (
            <p style={{ fontSize: 14.5, color: 'var(--c-muted-slate)', margin: '0 0 8px', lineHeight: 1.65 }}>
              {selected.description}
            </p>
          )}
          {selected.expression && (
            <div className="mono" style={{ color: 'var(--c-near-black)' }}>
              计算口径：{selected.expression}
            </div>
          )}
          {selected.table && (
            <div className="mono" style={{ color: 'var(--c-muted-slate)', marginTop: 6 }}>
              所属表：{selected.table}
            </div>
          )}
        </div>
      )}
    </>
  )
}
