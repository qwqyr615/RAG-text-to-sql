/**
 * 应用外壳：顶部导航 + 内容区 + 页脚。
 *
 * 设计取自 DESIGN.md 的 Navigation 规范：白色横向导航、16px 正文、
 * 深色实心 CTA；激活态用交互蓝。右侧固定展示 Agent 服务健康状态，
 * 因为「后端没起」是这个多进程架构最常见的故障，必须一眼可见。
 */

import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { api } from '../api/client'
import type { HealthInfo } from '../api/types'

interface NavItem {
  to: string
  label: string
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', label: '总览' },
  { to: '/chat', label: '智能问答' },
  { to: '/metadata', label: '数据资源' },
  { to: '/knowledge', label: '业务知识' },
  { to: '/modeling', label: '机器建模' },
  { to: '/report', label: '分析报告' },
]

export function AppShell() {
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const location = useLocation()

  // 路由切换时重新探测，方便排查「服务中途挂了」
  useEffect(() => {
    let cancelled = false

    const probe = async () => {
      try {
        const info = await api.health()
        if (!cancelled) {
          setHealth(info)
          setHealthError(null)
        }
      } catch (error) {
        if (!cancelled) {
          setHealth(null)
          setHealthError(error instanceof Error ? error.message : String(error))
        }
      }
    }

    void probe()
    return () => {
      cancelled = true
    }
  }, [location.pathname])

  const ready = health?.agent_ready === true
  const dotClass = ready ? 'status-dot status-dot-ok' : 'status-dot status-dot-bad'

  return (
    <div className="app-shell">
      <header
        style={{
          borderBottom: '1px solid var(--c-lightest-gray)',
          background: 'var(--c-white)',
          position: 'sticky',
          top: 0,
          zIndex: 20,
        }}
      >
        <div className="container" style={{ height: 68, display: 'flex', alignItems: 'center', gap: 28 }}>
          {/* 品牌区 */}
          <NavLink
            to="/"
            style={{ display: 'flex', flexDirection: 'column', gap: 2, flex: 'none' }}
          >
            <span
              style={{
                fontFamily: 'var(--font-display)',
                fontSize: 19,
                letterSpacing: '-0.4px',
                color: 'var(--c-black)',
                lineHeight: 1.1,
              }}
            >
              企业数据底座
            </span>
            <span className="eyebrow" style={{ fontSize: 10, letterSpacing: '0.4px' }}>
              INTELLIGENT ANALYTICS AGENT
            </span>
          </NavLink>

          {/* 主导航 */}
          <nav style={{ display: 'flex', alignItems: 'center', gap: 4, flex: 1, overflowX: 'auto' }}>
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                style={({ isActive }) => ({
                  fontSize: 16,
                  padding: '7px 13px',
                  borderRadius: 'var(--radius-pill)',
                  whiteSpace: 'nowrap',
                  color: isActive ? 'var(--c-blue)' : 'var(--c-black)',
                  background: isActive ? 'rgba(24, 99, 220, 0.06)' : 'transparent',
                })}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          {/* 服务状态 */}
          <div
            className="tag"
            title={
              healthError
                ? healthError
                : health
                  ? `数据库 ${health.database_type || '—'} · 业务表 ${health.table_count} 张 · 模型 ${health.llm_model}`
                  : '正在探测服务状态'
            }
            style={{ flex: 'none' }}
          >
            <span className={dotClass} />
            {ready ? 'Agent 就绪' : healthError ? '服务不可用' : '连接中'}
          </div>
        </div>
      </header>

      {/* 后端不可用时的全局提示：给出可执行的排查步骤 */}
      {healthError && (
        <div className="container" style={{ paddingTop: 16 }}>
          <div className="alert alert-danger">
            <strong>无法连接后端服务。</strong> {healthError}
            <div style={{ marginTop: 6, color: 'var(--c-muted-slate)' }}>
              启动顺序：① FastAPI（<span className="mono">cd agent && python -m uvicorn server.main:app --port 8000</span>）
              → ② Java 网关（<span className="mono">cd backend && mvn spring-boot:run</span>）。
            </div>
          </div>
        </div>
      )}

      {/* degraded：后端通了但 Agent 内核没就绪（通常是数据库没起） */}
      {health && !ready && (
        <div className="container" style={{ paddingTop: 16 }}>
          <div className="alert">
            <strong>Agent 内核未就绪。</strong>
            {health.error ? ` ${health.error}` : ' 请检查数据库连接与 .env 配置。'}
          </div>
        </div>
      )}

      <main className="page">
        <div className="container">
          <Outlet />
        </div>
      </main>

      <footer
        style={{
          borderTop: '1px solid var(--c-lightest-gray)',
          background: 'var(--c-snow)',
          padding: '28px 0',
        }}
      >
        <div
          className="container row-between"
          style={{ flexWrap: 'wrap', gap: 12 }}
        >
          <span className="caption">
            企业数据底座智能问析 Agent 系统 · 用户提问 → 智能分析 → 结果展示
          </span>
          <span className="caption">
            React + Spring Boot + FastAPI + LangChain · 只读 SQL 守卫 · RAG 示例检索
          </span>
        </div>
      </footer>
    </div>
  )
}
