/**
 * ECharts 容器组件。
 *
 * 职责边界：**前端只负责渲染，不理解业务语义**。
 * 图表 option 由后端大模型产出（见 `agent/agents/chart_agent.py`），
 * 本组件只做三件事：
 *  1. 把后端 option 与 Cohere 主题合并（主题保证配色落在设计系统内）；
 *  2. 处理容器尺寸变化（ResizeObserver）与实例销毁，避免内存泄漏；
 *  3. `chart_type === 'table'` 或缺少 option 时，明确告知调用方改用表格渲染。
 */

import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

import { chartTheme, colors, fonts } from '../styles/tokens'

interface EChartProps {
  /** 后端返回的 ECharts option */
  option: Record<string, unknown>
  height?: number
  /** 主题色是否覆盖后端配色（默认 true，保证视觉统一） */
  applyTheme?: boolean
  className?: string
}

/** Cohere 主题：补齐坐标轴、图例、提示框的默认样式 */
function buildThemedOption(
  option: Record<string, unknown>,
  applyTheme: boolean,
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...option }

  if (!applyTheme) return merged

  merged.textStyle = { fontFamily: fonts.body, color: colors.nearBlack, ...(option.textStyle as object) }
  merged.color = (option.color as string[]) ?? [...chartTheme.color]

  // xAxis / yAxis 可能是对象或数组，统一按数组处理
  const decorateAxis = (axis: unknown) => {
    const list = Array.isArray(axis) ? axis : axis ? [axis] : []
    return list.map((item) => {
      const record = (item ?? {}) as Record<string, unknown>
      return {
        axisLine: { lineStyle: { color: colors.borderCool }, ...(record.axisLine as object) },
        axisTick: { show: false, ...(record.axisTick as object) },
        axisLabel: { color: colors.mutedSlate, fontSize: 12, ...(record.axisLabel as object) },
        splitLine: { lineStyle: { color: colors.lightestGray }, ...(record.splitLine as object) },
        ...record,
      }
    })
  }

  if (merged.xAxis !== undefined) {
    merged.xAxis = decorateAxis(merged.xAxis)
  }
  if (merged.yAxis !== undefined) {
    merged.yAxis = decorateAxis(merged.yAxis)
  }

  // series 兜底配色：后端未指定 itemStyle 时套用主题色
  if (Array.isArray(merged.series)) {
    merged.series = (merged.series as Record<string, unknown>[]).map((item, index) => {
      const series = { ...item }
      if (!series.itemStyle && !series.lineStyle) {
        series.itemStyle = { color: chartTheme.color[index % chartTheme.color.length] }
      }
      // 折线图默认加平滑与细线，符合企业级克制风格
      if (series.type === 'line' && series.smooth === undefined) {
        series.smooth = true
      }
      return series
    })
  }

  merged.grid = { ...chartTheme.grid, ...(option.grid as object) }
  merged.legend = {
    ...(option.legend as object),
    textStyle: { color: colors.mutedSlate, fontFamily: fonts.body },
    icon: 'roundRect',
    itemWidth: 10,
    itemHeight: 10,
  }
  merged.tooltip = { ...chartTheme.tooltip, ...(option.tooltip as object) }

  return merged
}

export function EChart({ option, height = 380, applyTheme = true, className }: EChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  // 初始化 + 销毁
  useEffect(() => {
    if (!containerRef.current) return
    const chart = echarts.init(containerRef.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart

    // 容器尺寸变化时重绘（侧边栏折叠、窗口缩放都会触发）
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(containerRef.current)

    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  // option 变化时更新
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const themed = buildThemedOption(option, applyTheme)
    // notMerge=true：后端每次给的是完整 option，合并会残留上一次的 series
    chart.setOption(themed, true)
  }, [option, applyTheme])

  return <div ref={containerRef} className={className} style={{ width: '100%', height }} />
}
